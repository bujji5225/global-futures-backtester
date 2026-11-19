import pandas as pd
import pytest

from src.engine import Settings, backtest
from src.strategies import (
    CURVE_CARRY,
    DONCHIAN_BREAKOUT,
    Strategy,
    curve_carry_per_30_days,
    signal_series,
    signals,
)


def test_breakout_uses_prior_entry_channel_and_shorter_exit_channel():
    prices = pd.Series([10, 11, 12, 13, 12.7, 11.6, 9, 8, 9.5])
    strategy = Strategy(DONCHIAN_BREAKOUT, breakout_window=3, channel_exit_window=2, breakout_filter=0)

    targets = signal_series(prices, strategy)

    assert targets.tolist() == [0, 0, 0, 1, 1, 0, -1, -1, 0]
    pd.testing.assert_series_equal(targets.iloc[:6], signal_series(prices.iloc[:6], strategy))


def test_breakout_flattens_and_rewarms_after_missing_settlement():
    prices = pd.Series([10, 11, 12, 13, pd.NA, 14, 15, 16, 17], dtype="Float64")
    strategy = Strategy(DONCHIAN_BREAKOUT, breakout_window=3, channel_exit_window=2, breakout_filter=0)
    assert signal_series(prices, strategy).tolist() == [0, 0, 0, 1, 0, 0, 0, 0, 1]


def test_breakout_filter_uses_range_known_before_signal():
    close = pd.Series([10, 10, 10, 12, 14])
    high = pd.Series([11, 11, 11, 20, 15])
    low = pd.Series([9, 9, 9, 10, 12])
    parameters = dict(breakout_window=3, channel_exit_window=2, breakout_vol_window=2)

    filtered = signal_series(
        close, Strategy(DONCHIAN_BREAKOUT, breakout_filter=0.75, **parameters), high=high, low=low,
    )
    unfiltered = signal_series(
        close, Strategy(DONCHIAN_BREAKOUT, breakout_filter=0, **parameters), high=high, low=low,
    )
    small_buffer = signal_series(
        close, Strategy(DONCHIAN_BREAKOUT, breakout_filter=0.25, **parameters), high=high, low=low,
    )

    assert unfiltered.iloc[3] == 1
    assert filtered.iloc[3] == 0
    assert small_buffer.iloc[3] == 1


def test_breakout_fills_next_session_open():
    dates = pd.bdate_range("2025-01-06", periods=6)
    frame = pd.DataFrame({
        "date": dates, "contract": "CLG25", "expiry": pd.Timestamp("2025-01-21"),
        "open": [10, 10, 11, 11, 15, 15],
        "high": [11, 11, 12, 14, 15, 15],
        "low": [10, 10, 10, 11, 13, 12],
        "close": [10, 10, 11, 13, 14, 12], "volume": 100,
    })
    strategy = Strategy(DONCHIAN_BREAKOUT, breakout_window=3, channel_exit_window=2, breakout_filter=0)

    result = backtest(frame, strategy, Settings(roll_days=0, commission=0, slippage_ticks=0))

    assert result.fills.iloc[0].date == dates[4]
    assert result.fills.iloc[0].price == 15
    assert result.fills.iloc[0].when == "open"
    assert result.trades.iloc[0].entry_signal_date == dates[3]


def _settlement_curve(dates):
    near = pd.DataFrame({
        "date": dates, "contract": "CLQ25", "expiry": pd.Timestamp("2025-07-21"),
        "settlement": [71, 71, 72, 71, 69],
    })
    far = pd.DataFrame({
        "date": dates, "contract": "CLV25", "expiry": pd.Timestamp("2025-09-19"),
        "settlement": [70, 70, 74, 74, 74],
    })
    return pd.concat([near, far], ignore_index=True)


def test_carry_normalizes_expiry_gap_and_disables_unsynchronized_signals():
    assert curve_carry_per_30_days(-8, -10, 60) == 1
    with pytest.raises(ValueError, match="expire after"):
        curve_carry_per_30_days(80, 82, 0)

    dates = pd.bdate_range("2025-07-01", periods=5)
    data = _settlement_curve(dates)
    missing_far = data.contract.eq("CLV25") & data.date.eq(dates[3])
    data = data.loc[~missing_far]
    pair = ("CLQ25", "CLV25")
    strategy = Strategy(CURVE_CARRY, carry_threshold=0.75)

    qualified = signals(data, strategy, [pair])[pair]

    assert qualified.tolist() == [0, 0, -1, 0, -1]


def test_carry_trades_single_nearby_leg_and_records_prior_curve_regime():
    dates = pd.bdate_range("2025-07-01", periods=5)
    strategy = Strategy(CURVE_CARRY)
    settings = Settings(roll_days=1, commission=0, slippage_ticks=0)

    result = backtest(_settlement_curve(dates), strategy, settings)

    assert result.fills.contract.unique().tolist() == ["CLQ25"]
    assert result.fills.when.unique().tolist() == ["settlement"]
    assert result.trades.entry_date.tolist() == [dates[1], dates[3]]
    assert result.trades.entry_signal_date.tolist() == [dates[0], dates[2]]
    assert result.trades.direction.tolist() == ["Long", "Short"]
    assert result.trades.entry_spread.tolist() == [1, -2]
    assert result.trades.entry_regime.tolist() == ["Backwardation", "Contango"]
    assert result.metrics["Net P&L"] == pytest.approx(2000)


def test_carry_remains_flat_without_a_deferred_contract():
    dates = pd.bdate_range("2025-07-01", periods=5)
    data = _settlement_curve(dates).query("contract == 'CLQ25'")
    result = backtest(data, Strategy(CURVE_CARRY), Settings(roll_days=1))
    assert result.fills.empty
