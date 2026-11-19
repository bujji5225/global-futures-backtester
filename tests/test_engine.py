from dataclasses import replace

import pandas as pd
import pytest

from src.data import COLUMNS, demo_data
from src.engine import Settings, backtest
from src.research import chronological_split, evaluate
from src.strategies import STRATEGY_NAMES, Strategy, signal_series

DAYS = pd.bdate_range("2025-01-06", periods=5)
CL = "CLG25"
FAR = "CLH25"
FREE = Settings(commission=0, slippage_ticks=0, roll_days=0)


def quotes(prices, contract=CL, expiry="2025-02-01", volume=50):
    return pd.DataFrame([
        (day, contract, pd.Timestamp(expiry), price, price, price, price, vol)
        for day, price, vol in zip(DAYS, prices, [volume] * len(DAYS) if isinstance(volume, int) else volume)
    ], columns=COLUMNS)


@pytest.mark.parametrize("position, prices, expected", [
    (1, [69, 70, 70.4, 70.4, 71], 400),
    (-1, [69, 70, 71, 71, 71], -1_000),
])
def test_cl_long_and_short_next_session_execution(position, prices, expected):
    result = backtest(quotes(prices), settings=FREE, targets={DAYS[0]: {CL: position}})
    trade = result.trades.iloc[0]
    assert trade.entry_date == DAYS[1]
    assert trade.exit_date == DAYS[2]
    assert trade.entry_price == 70
    assert trade.gross_pnl == pytest.approx(expected)
    assert result.metrics["Net P&L"] == pytest.approx(expected)


def test_flat_account_and_cost_sensitivity():
    frame = quotes([69, 70, 70.8, 71, 72])
    result = backtest(frame, settings=FREE, targets={})
    assert result.trades.empty and result.fills.empty
    assert result.metrics["Net P&L"] == 0
    targets = {DAYS[0]: {CL: 1}}
    low = backtest(frame, settings=FREE, targets=targets)
    high = backtest(frame, settings=replace(FREE, commission=2.5, slippage_ticks=1), targets=targets)
    assert low.metrics["Net P&L"] == pytest.approx(800)
    assert high.metrics["Net P&L"] == pytest.approx(775)


def test_micro_contract_uses_its_own_multiplier_and_tick_value():
    ticker = "MCLG25"
    frame = quotes([69, 70, 70.8, 71, 72], contract=ticker)
    targets = {DAYS[0]: {ticker: 1}}
    result = backtest(frame, targets=targets)
    assert result.trades.iloc[0].gross_pnl == pytest.approx(80)
    assert result.trades.iloc[0].costs == pytest.approx(7)
    with pytest.raises(ValueError, match="match the MCL product"):
        backtest(frame, settings=FREE, targets=targets)


def test_roll_fills_use_own_contract_prices_and_charge_both_sides():
    front = quotes([69, 70, 70.4, 71, 72], expiry="2025-01-10")
    far = quotes([90, 90, 91, 91.1, 91.2], contract=FAR, expiry="2025-03-01")
    targets = {DAYS[0]: {CL: 1}, DAYS[1]: {CL: 1}, DAYS[2]: {FAR: 1}, DAYS[3]: {FAR: 1}}
    result = backtest(pd.concat([front, far]), settings=Settings(roll_days=1), targets=targets)
    roll = result.fills[result.fills.date.eq(DAYS[3])]
    assert list(zip(roll.action, roll.contract, roll.price, roll.cost)) == [
        ("Close", CL, 71, 12.5), ("Open", FAR, 91.1, 12.5),
    ]
    assert result.rolls[["from_contract", "to_contract"]].values.tolist() == [[CL, FAR]]
    assert result.metrics["Net P&L"] == pytest.approx(1_050)


def test_volume_roll_reads_previous_session_and_never_moves_back():
    front = quotes([70] * 5, volume=[100, 100, 1, 1_000, 1_000])
    far = quotes([72] * 5, contract=FAR, expiry="2025-03-01", volume=50)
    result = backtest(pd.concat([front, far]), settings=replace(FREE, roll_rule="Prior-session volume"), targets={})
    assert result.rolls[["date", "from_contract", "to_contract", "reason"]].values.tolist() == [
        [DAYS[3], CL, FAR, "Prior-session volume"],
    ]
    assert result.daily.near_contract.tolist() == ["", CL, CL, FAR, FAR]


def test_calendar_spread_sums_legs_and_costs_before_trade_metrics():
    front = quotes([70, 70, 80, 72, 73])
    far = quotes([75, 75, 75, 74, 73], contract=FAR, expiry="2025-03-01")
    strategy = Strategy("Calendar spread", window=3, entry=1.0)
    result = backtest(pd.concat([front, far]), strategy, Settings(roll_days=0))
    first_trade = result.round_trips.iloc[0]
    legs = result.trades.query("group == @first_trade.group")
    assert first_trade.entry_date == DAYS[3]
    assert first_trade.legs == 2
    assert dict(zip(legs.contract, legs.quantity)) == {CL: -1, FAR: 1}
    assert first_trade.gross_pnl == pytest.approx(-2_000)
    assert first_trade.costs == 50
    assert first_trade.net_pnl == pytest.approx(-2_050)


def test_risk_guard_exits_at_open_and_never_reenters_after_drawdown():
    frame = quotes([70, 70, 55, 75, 80])
    targets = {d: {CL: 1} for d in DAYS}
    result = backtest(frame, settings=replace(FREE, max_drawdown=0.1), targets=targets)
    assert len(result.trades) == 1
    assert result.trades.iloc[0].reason == "Drawdown halt"
    assert result.trades.iloc[0].exit_date == DAYS[2]
    assert result.metrics["Max drawdown"] == pytest.approx(-0.15)
    blocked = backtest(frame, settings=replace(FREE, margin=101_000), targets=targets)
    assert blocked.trades.empty and blocked.metrics["Risk-blocked sessions"] == 4


def test_missing_quote_for_a_held_position_fails_explicitly():
    front = quotes([70] * 5).drop(index=2)
    far = quotes([75] * 5, contract=FAR, expiry="2025-03-01")
    with pytest.raises(ValueError, match="held contract"):
        backtest(pd.concat([front, far]), settings=FREE, targets={DAYS[0]: {CL: 1}})


@pytest.mark.parametrize("name", STRATEGY_NAMES)
def test_holdout_is_chronological_flat_start_and_reconciles(name):
    frame = demo_data(periods=90)
    dates = frame.date
    split = chronological_split(frame, dates.min(), dates.max())
    assert split.development_end < split.test_start
    outcomes = evaluate(frame, Strategy(name), Settings(), split)
    assert outcomes["Holdout"].daily.index[0] == split.test_start
    for result in outcomes.values():
        assert result.daily.pnl.sum() == pytest.approx(result.trades.net_pnl.sum(), abs=1e-8)
        assert result.daily.cost.sum() == pytest.approx(result.fills.cost.sum())
        assert result.fills.quantity.sum() == 0
        assert result.metrics["Net P&L"] == pytest.approx(result.daily.pnl.sum(), abs=1e-8)


def test_signals_need_warmup_and_stay_flat_across_a_missing_observation():
    prices = pd.Series([1, 2, 3, None, 2, 1, 0])
    actual = signal_series(prices, Strategy("Momentum", fast=2, slow=3))
    assert actual.tolist() == [0, 0, 1, 0, 0, 0, -1]
