from datetime import date

import pytest

from src.options_strategies import (
    BEAR_PUT,
    BULL_CALL,
    IRON_CONDOR,
    LONG_STRADDLE,
    OptionQuote,
    build_strategy,
)


def quote(right, strike, bid, ask, underlying="CLF27", expiry="2026-12-16"):
    return OptionQuote(f"LOF27 {right}{strike}", underlying, expiry, right, strike, bid, ask)


def test_bull_call_quotes_opening_fees_and_negative_domain():
    low = quote("C", -5, 3.0, 3.1)
    high = quote("C", 0, 1.25, 1.30)
    spread = build_strategy(BULL_CALL, [high, low], commission=2.5, contracts=2)
    assert [leg.quantity for leg in spread.legs] == [2, -2]
    assert [leg.premium for leg in spread.legs] == [3.1, 1.25]
    assert spread.entry_cashflow == pytest.approx(-3700)
    assert spread.commissions == 10
    assert spread.max_loss == pytest.approx(3710)
    assert spread.max_profit == pytest.approx(6290)
    assert spread.breakevens == pytest.approx((-3.145,))
    assert spread.payoff(-20) == pytest.approx(-3710)
    assert spread.payoff(20) == pytest.approx(6290)
    assert spread.curve().futures_price.min() < -5
    assert spread.payoff(spread.breakevens[0]) == pytest.approx(0)


def test_bear_put_payoff_uses_the_higher_strike_as_the_long_leg():
    low = quote("put", 60, 1.05, 1.1)
    high = quote("P", 65, 2.95, 3.00)
    spread = build_strategy(BEAR_PUT, [low, high], commission=2.5)
    assert high.expiry == date(2026, 12, 16)
    assert [leg.quantity for leg in spread.legs] == [-1, 1]
    assert spread.payoff(-37.63) == pytest.approx(3045)
    assert spread.max_loss == pytest.approx(1955)
    assert spread.max_profit == pytest.approx(3045)
    assert spread.breakevens == pytest.approx((63.045,))


def test_asymmetric_iron_condor_has_two_independent_wing_losses():
    quotes = [quote("P", 55, 0.4, 0.5), quote("P", 60, 1, 1.1),
              quote("C", 70, 1.5, 1.6), quote("C", 78, 0.2, 0.3)]
    condor = build_strategy(IRON_CONDOR, reversed(quotes), multiplier=100, commission=2.5)
    assert condor.entry_cashflow == pytest.approx(170)
    assert condor.net_entry_cashflow == pytest.approx(160)
    assert condor.max_profit == pytest.approx(160)
    assert condor.max_loss == pytest.approx(640)
    assert condor.payoff(-10) == pytest.approx(-340)
    assert condor.payoff(65) == pytest.approx(160)
    assert condor.payoff(90) == pytest.approx(-640)
    assert condor.breakevens == pytest.approx((58.4, 71.6))


def test_long_straddle_has_finite_loss_and_two_tail_breakevens():
    straddle = build_strategy(LONG_STRADDLE, [quote("P", -1, 0.7, 0.8), quote("C", -1, 1.1, 1.2)])
    assert straddle.max_profit == float("inf")
    assert straddle.max_loss == pytest.approx(2_000)
    assert straddle.breakevens == pytest.approx((-3, 1))
    assert straddle.payoff(-5) == pytest.approx(2_000)
    assert straddle.payoff(3) == pytest.approx(2_000)
    assert set(straddle.curve(0).columns) == {"futures_price", "pnl"}


@pytest.mark.parametrize("bid,ask", [(-0.1, 1), (1.2, 1), (0, 0), (None, 1), (0.2, float("inf"))])
def test_invalid_or_missing_side_is_rejected(bid, ask):
    with pytest.raises(ValueError):
        quote("C", 65, bid, ask)


def test_leg_validation_rejects_mixed_futures_expiry_and_missing_executable_bid():
    low = quote("C", 60, 3, 3.2)
    with pytest.raises(ValueError, match="same underlying"):
        build_strategy(BULL_CALL, [low, quote("C", 65, 1.0, 1.2, underlying="CLG27")])
    with pytest.raises(ValueError, match="same underlying"):
        build_strategy(BULL_CALL, [low, quote("C", 65, 1.0, 1.2, expiry="2026-12-17")])
    with pytest.raises(ValueError, match="positive displayed bid"):
        build_strategy(BULL_CALL, [low, quote("C", 65, 0, 1.2)])
    with pytest.raises(ValueError, match="same strike"):
        build_strategy(LONG_STRADDLE, [low, quote("P", 65, 1.0, 1.2)])


def test_expensive_spread_has_no_fake_breakeven_or_positive_maximum():
    spread = build_strategy(BULL_CALL, [quote("C", 65, 5.80, 6), quote("C", 70, 0.20, 0.25)])
    assert spread.max_profit == pytest.approx(-800)
    assert spread.max_loss == pytest.approx(5800)
    assert spread.breakevens == ()
