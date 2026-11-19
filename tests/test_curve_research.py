from io import BytesIO
import json
from zipfile import ZipFile

import pandas as pd
import pytest

from src.data import eia_archive, load_csv
from src.engine import Settings, backtest
from src.replay import MAY, JUNE, advance, april_panel, exit_deadline, policy_replay
from src.research import BASE, BENCHMARK, STRESS, study_brief, study_curve, study_evidence, walk_forward_windows
from src.rollover import roll_deadlines
from src.strategies import Strategy


def test_eia_settlements_preserve_both_contracts_at_april_expiry():
    data = eia_archive()
    assert data.settlement_only and not data.synthetic
    assert data.frame.date.agg(["min", "max"]).tolist() == list(pd.to_datetime(["2018-01-02", "2024-04-05"]))
    assert data.frame.groupby("date").size().eq(4).all()
    panel = april_panel(data)
    assert panel.loc["2020-04-20", [MAY, JUNE]].tolist() == [-37.63, 20.43]
    assert panel.loc["2020-04-21", MAY] == 10.01
    assert pd.isna(panel.loc["2020-04-22", MAY])


def test_calendar_buffer_counts_exchange_sessions_and_upload_validates():
    expiry = {"CLK20": pd.Timestamp("2020-04-13")}
    dates = pd.to_datetime(["2020-04-08", "2020-04-09", "2020-04-13", "2020-04-14"])
    assert roll_deadlines(expiry, 1, dates)["CLK20"] == pd.Timestamp("2020-04-09")
    assert roll_deadlines(expiry, 1)["CLK20"] == pd.Timestamp("2020-04-10")
    quotes = b"date,contract,expiry,settlement\n2020-04-09,CLK20,2020-04-21,22.76\n"
    data = load_csv(quotes, "sample.csv", "CL", b"date\n2020-04-09\n2020-04-13\n")
    assert data.calendar_label == "Uploaded exchange sessions"
    with pytest.raises(ValueError, match="must include every trading date"):
        load_csv(quotes, "sample.csv", "CL", b"date\n2020-04-13\n")


def test_next_settlement_proxy_has_correct_fill_date_and_pnl():
    dates = pd.bdate_range("2024-02-01", periods=4)
    frame = pd.DataFrame({
        "date": dates, "contract": "CLH24", "expiry": pd.Timestamp("2024-02-20"),
        "settlement": [70, 71, 73, 74],
    })
    settings = Settings(commission=0, slippage_ticks=0, roll_days=1)
    result = backtest(frame, settings=settings, targets={dates[0]: {"CLH24": 1}, dates[-2]: {"CLH24": -1}})
    assert result.fills.date.tolist() == [dates[1], dates[2]]
    assert result.fills.when.tolist() == ["settlement", "settlement"]
    assert result.metrics["Net P&L"] == 2_000
    with pytest.raises(ValueError, match="require volume"):
        backtest(frame, settings=Settings(roll_days=1, roll_rule="Prior-session volume"))


def test_walk_forward_only_creates_complete_disjoint_test_windows():
    dates = pd.bdate_range("2024-01-01", periods=25)
    windows = walk_forward_windows(dates, 10, 6)
    assert len(windows) == 2
    assert windows[0].test_start == dates[10]
    assert windows[0].test_end < windows[1].test_start
    assert windows[-1].test_end == dates[21]
    assert all(w.development_end < w.test_start for w in windows)


def test_archived_curve_study_reconciles_spreads_and_exports_metadata():
    dataset = eia_archive()
    strategy = Strategy("Calendar spread")
    study = study_curve(dataset, strategy, Settings(), 252, 252, 2)
    windows = study.windows()
    assert len(study.folds) == 5
    assert set(windows.Scenario) == {BASE, STRESS, BENCHMARK}
    assert set(study.regimes()["Signal curve"]) == {"Contango", "Backwardation"}
    for fold in study.folds:
        base = fold.tests[BASE]
        assert base.daily.index[0] == fold.window.test_start
        assert base.trades.net_pnl.sum() == pytest.approx(base.metrics["Net P&L"])
        assert base.round_trips.legs.eq(2).all()
        assert (base.round_trips.entry_signal_date < base.round_trips.entry_date).all()
        assert fold.tests[STRESS].settings["slippage_ticks"] - base.settings["slippage_ticks"] == 2
        assert fold.tests[BENCHMARK].trades.query("direction == 'Long'").quantity.gt(0).all()
    brief = study_brief(dataset, study)
    assert "next settlement as a proxy" in brief
    with ZipFile(BytesIO(study_evidence(dataset, study, brief))) as archive:
        metadata = json.loads(archive.read("study.json"))
        assert metadata["sha256"] == dataset.fingerprint
        assert metadata["execution"] == "Next settlement proxy"
        assert "base-spread-legs.csv" in archive.namelist()


def test_replay_marks_signed_pnl_and_applies_early_exit_risk():
    data = eia_archive()
    panel = april_panel(data)
    early = exit_deadline(data, 5)
    late = exit_deadline(data, 1)
    assert early == pd.Timestamp("2020-04-14")
    assert late == pd.Timestamp("2020-04-20")
    roll = advance(panel, [], "Roll", early)
    assert roll.contract == JUNE and roll.costs == 25
    assert roll.day_pnl == pytest.approx((20.11 - 22.41) * 1_000 - 25)
    forced = advance(panel, [], "Hold", early)
    assert forced.contract == "" and forced.outcome == "Cut at expiry buffer"
    assert len(policy_replay(panel, late)) == len(panel) - 1
    assert policy_replay(panel, late)[-1].cumulative_pnl < policy_replay(panel, early)[-1].cumulative_pnl
