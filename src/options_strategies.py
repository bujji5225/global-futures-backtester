"""Expiry payoff for selected structures made of options on one WTI future.

Quotes are dollars per barrel. Intrinsic value is measured against the underlying
futures price at option expiry. Early exercise, resulting futures positions,
subsequent execution, and margin are outside this model.
"""
from dataclasses import dataclass
from datetime import date, datetime
from math import fsum, inf, isfinite
from numbers import Integral

import numpy as np
import pandas as pd

BULL_CALL = "Bull call spread"
BEAR_PUT = "Bear put spread"
IRON_CONDOR = "Iron condor"
LONG_STRADDLE = "Long straddle"
OPTION_STRATEGY_NAMES = (BULL_CALL, BEAR_PUT, IRON_CONDOR, LONG_STRADDLE)


def _finite(value, label):
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"{label} must be a finite number.") from error
    if not isfinite(numeric):
        raise ValueError(f"{label} must be a finite number.")
    return numeric


@dataclass(frozen=True)
class OptionQuote:
    symbol: str
    underlying: str
    expiry: date
    right: str
    strike: float
    bid: float
    ask: float

    def __post_init__(self):
        for field in ("symbol", "underlying"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"The option {field} is required.")
            object.__setattr__(self, field, value.strip().upper())

        expiry = self.expiry
        if isinstance(expiry, datetime):
            expiry = expiry.date()
        elif isinstance(expiry, str):
            try:
                parsed = date.fromisoformat(expiry)
                if parsed.isoformat() != expiry:
                    raise ValueError
                expiry = parsed
            except ValueError as error:
                raise ValueError("Option expiry must use YYYY-MM-DD.") from error
        if not isinstance(expiry, date) or pd.isna(expiry):
            raise ValueError("Option expiry must use YYYY-MM-DD.")

        right = {"C": "C", "CALL": "C", "P": "P", "PUT": "P"}.get(str(self.right).strip().upper())
        if right is None:
            raise ValueError("Option right must be call or put.")
        strike = _finite(self.strike, "Strike")
        bid, ask = _finite(self.bid, "Bid"), _finite(self.ask, "Ask")
        if bid < 0 or ask <= 0 or bid > ask:
            raise ValueError("Option quotes require 0 ≤ bid ≤ ask and a positive ask.")

        object.__setattr__(self, "expiry", expiry)
        object.__setattr__(self, "right", right)
        object.__setattr__(self, "strike", strike)
        object.__setattr__(self, "bid", bid)
        object.__setattr__(self, "ask", ask)


@dataclass(frozen=True)
class OptionLeg:
    quote: OptionQuote
    quantity: int

    @property
    def premium(self):
        return self.quote.ask if self.quantity > 0 else self.quote.bid

    def intrinsic(self, futures_price):
        if self.quote.right == "C":
            return max(futures_price - self.quote.strike, 0.0)
        return max(self.quote.strike - futures_price, 0.0)


@dataclass(frozen=True)
class OptionAnalysis:
    name: str
    legs: tuple[OptionLeg, ...]
    multiplier: float
    commission: float

    @property
    def entry_cashflow(self):
        """Positive dollars received on entry before commissions."""
        return -self.multiplier * fsum(leg.quantity * leg.premium for leg in self.legs)

    @property
    def commissions(self):
        return self.commission * sum(abs(leg.quantity) for leg in self.legs)

    @property
    def net_entry_cashflow(self):
        return self.entry_cashflow - self.commissions

    def payoff(self, futures_price):
        """Dollar P&L at expiry after premiums and opening commissions."""
        price = _finite(futures_price, "Futures price")
        return self.net_entry_cashflow + self.multiplier * fsum(
            leg.quantity * leg.intrinsic(price) for leg in self.legs
        )

    @property
    def _knots(self):
        return sorted({leg.quote.strike for leg in self.legs})

    @property
    def _tail_slopes(self):
        left = -self.multiplier * sum(leg.quantity for leg in self.legs if leg.quote.right == "P")
        right = self.multiplier * sum(leg.quantity for leg in self.legs if leg.quote.right == "C")
        return left, right

    @property
    def max_profit(self):
        """Highest attainable dollar P&L, including negative values if every outcome loses."""
        left, right = self._tail_slopes
        return inf if left < 0 or right > 0 else max(self.payoff(strike) for strike in self._knots)

    @property
    def max_loss(self):
        """Largest loss as a nonnegative dollar magnitude."""
        left, right = self._tail_slopes
        return inf if left > 0 or right < 0 else max(0.0, -min(self.payoff(strike) for strike in self._knots))

    @property
    def breakevens(self):
        """Finite break-even prices, including endpoints of any flat zero-P&L interval."""
        strikes = self._knots
        tolerance = 1e-8
        levels = [self.payoff(strike) for strike in strikes]
        levels = [0.0 if abs(level) <= tolerance else level for level in levels]
        roots = [strike for strike, level in zip(strikes, levels) if level == 0]
        for x, y, px, py in zip(strikes, strikes[1:], levels, levels[1:]):
            if (px < 0 < py) or (py < 0 < px):
                roots.append(x - px * (y - x) / (py - px))
        left, right = self._tail_slopes
        # A root outside the outer strikes belongs to a non-flat tail.
        if left and levels[0] / left > 0:
            roots.append(strikes[0] - levels[0] / left)
        if right and levels[-1] / right < 0:
            roots.append(strikes[-1] - levels[-1] / right)
        return tuple(sorted(set(roots)))

    def curve(self, center=None, points=201):
        """Sample expiry P&L, keeping the strikes and break-evens on the grid."""
        if not isinstance(points, Integral) or isinstance(points, bool) or points < 3:
            raise ValueError("The curve requires at least three sample points.")
        bounds = self._knots + list(self.breakevens)
        if center is not None:
            bounds.append(_finite(center, "Futures price"))
        left, right = min(bounds), max(bounds)
        pad = max(2.0, (right - left) * 0.18, max(abs(left), abs(right)) * 0.03)
        prices = np.unique(np.concatenate([np.linspace(left - pad, right + pad, points), bounds]))
        return pd.DataFrame({"futures_price": prices, "pnl": [self.payoff(value) for value in prices]})


def build_strategy(name, quotes, *, contracts=1, multiplier=1_000, commission=0.0):
    """Construct a same-expiry spread from the selected outright option quotes."""
    if name not in OPTION_STRATEGY_NAMES:
        raise ValueError("Unknown option strategy.")
    if not isinstance(contracts, Integral) or isinstance(contracts, bool) or contracts < 1:
        raise ValueError("Contract count must be a positive integer.")
    multiplier = _finite(multiplier, "Contract multiplier")
    commission = _finite(commission, "Opening commission")
    if multiplier <= 0 or commission < 0:
        raise ValueError("Use a positive multiplier and nonnegative opening commission.")

    quotes = list(quotes)
    expected = 4 if name == IRON_CONDOR else 2
    if len(quotes) != expected:
        raise ValueError(f"{name} requires {expected} quoted options.")
    if any(not isinstance(quote, OptionQuote) for quote in quotes):
        raise ValueError("Every option leg needs a valid quote.")
    if len({quote.symbol for quote in quotes}) != expected:
        raise ValueError("Select a distinct option contract for each leg.")
    if len({(quote.underlying, quote.expiry) for quote in quotes}) != 1:
        raise ValueError("All legs must have the same underlying futures contract and option expiry.")

    quotes.sort(key=lambda quote: (quote.strike, quote.right))
    strikes = [quote.strike for quote in quotes]
    rights = "".join(quote.right for quote in quotes)
    if name == LONG_STRADDLE:
        if rights != "CP" or strikes[0] != strikes[1]:
            raise ValueError("A long straddle needs a call and a put at the same strike.")
        signs = (1, 1)
    else:
        expected_rights, signs = {
            BULL_CALL: ("CC", (1, -1)),
            BEAR_PUT: ("PP", (-1, 1)),
            IRON_CONDOR: ("PPCC", (1, -1, -1, 1)),
        }[name]
        if rights != expected_rights or len(set(strikes)) != expected:
            raise ValueError(f"{name} requires {expected_rights} at distinct ascending strikes.")

    legs = tuple(OptionLeg(quote, int(contracts) * sign) for quote, sign in zip(quotes, signs))
    if any(leg.quantity < 0 and leg.quote.bid == 0 for leg in legs):
        raise ValueError("Short option legs require a positive displayed bid.")
    return OptionAnalysis(name, legs, multiplier, commission)
