from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

from src.data import make_dataset
from src.quote_research import csv_chain, live_breakout_reference, live_carry, live_option_quotes, sample_chain


NOW = datetime(2026, 11, 16, 18, tzinfo=UTC)


def row(**fields):
    return SimpleNamespace(**({
        "quote_status": "fresh", "timestamp": NOW, "expiry": datetime(2026, 11, 20, 19, tzinfo=UTC),
        "mid": 80.0, "symbol": "CLZ6",
    } | fields))


def test_live_carry_uses_signed_quotes_and_skips_unsynchronized_prices():
    near = row(mid=-5)
    far = row(expiry=near.expiry + timedelta(days=30), mid=1)
    carry, bias, stamp = live_carry(near, far, 0.25)
    assert (carry, bias, stamp) == (-6, "Short nearby", NOW)
    far.timestamp -= timedelta(seconds=20)
    with pytest.raises(ValueError, match="timestamps differ"):
        live_carry(near, far, 0.25)


def test_stale_and_unlinked_options_never_enter_live_pricing():
    option = row(symbol="LOZ6 C7000", underlying_symbol="CLZ6", right="C", strike=70, bid=2.2, ask=2.3)
    stale = row(symbol="LOZ6 C7200", underlying_symbol="CLZ6", right="C", strike=72, bid=1.0, ask=1.1, quote_status="stale")
    unlinked = row(symbol="LOZ6 C6800", underlying_symbol=None)
    snapshot = SimpleNamespace(status="live", options=[option, stale, unlinked])
    quotes, originals = live_option_quotes(snapshot)
    assert list(quotes) == [option.symbol]
    assert originals[option.symbol] is option
    snapshot.status = "stale"
    assert live_option_quotes(snapshot) == ({}, {})


def test_static_csv_validates_selected_underlying_product_and_duplicate_symbols():
    csv = b"symbol,underlying,expiry,right,strike,bid,ask\nOPTION-70,CLG27,2027-01-15,C,70,2.22,2.34\n"
    assert csv_chain(csv, "CL")[0].underlying == "CLG27"
    with pytest.raises(ValueError, match="dated MCL"):
        csv_chain(csv, "MCL")
    with pytest.raises(ValueError, match="one quote per option"):
        csv_chain(csv + csv.splitlines()[1], "CL")
    assert {q.underlying for q in sample_chain("MCL")} == {"MCLG27"}


def test_live_breakout_uses_completed_matching_daily_bars():
    sessions = pd.bdate_range(end="2026-11-13", periods=25)
    data = make_dataset(pd.DataFrame({
        "date": sessions,
        "contract": "CLZ26",
        "expiry": pd.Timestamp("2026-11-20"),
        "open": 70.0,
        "high": 74.0,
        "low": 66.0,
        "close": 70.0,
        "volume": 1_000,
    }), "Fixture", "CL", sessions)
    high, low, signal, latest = live_breakout_reference(row(), data)
    assert (high, low, signal, latest) == (76.0, 64.0, "Above channel", sessions[-1])
    with pytest.raises(ValueError, match="recent completed"):
        live_breakout_reference(row(timestamp=NOW + timedelta(days=7)), data)
    with pytest.raises(ValueError, match="fresh two-sided"):
        live_breakout_reference(row(quote_status="stale"), data)
