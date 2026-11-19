"""Strategies return a signed target using data through each session's close."""
from dataclasses import dataclass
from math import isfinite

import numpy as np
import pandas as pd

STRATEGY_NAMES = ("Momentum", "Mean reversion", "Calendar spread")
LONG_SPREAD = "Long spread benchmark"
SPREAD_STRATEGIES = ("Calendar spread", LONG_SPREAD)
DONCHIAN_BREAKOUT = "Donchian breakout"
CURVE_CARRY = "Curve carry"
ADVANCED_STRATEGY_NAMES = (DONCHIAN_BREAKOUT, CURVE_CARRY)
PAIR_SIGNAL_STRATEGIES = (*SPREAD_STRATEGIES, CURVE_CARRY)


@dataclass(frozen=True)
class Strategy:
    name: str = "Momentum"
    fast: int = 10
    slow: int = 40
    window: int = 20
    entry: float = 1.5
    exit: float = 0.25
    breakout_window: int = 20
    channel_exit_window: int = 10
    breakout_vol_window: int = 14
    breakout_filter: float = 0.25
    carry_threshold: float = 0.25

    def validate(self):
        if self.name not in (*STRATEGY_NAMES, LONG_SPREAD, *ADVANCED_STRATEGY_NAMES):
            raise ValueError("Unknown strategy.")
        if self.name == "Momentum":
            if any(type(n) is not int for n in (self.fast, self.slow)) or not 2 <= self.fast < self.slow:
                raise ValueError("Use integer SMA windows with 2 ≤ fast < slow.")
        elif self.name in ("Mean reversion", "Calendar spread"):
            if type(self.window) is not int or self.window < 3:
                raise ValueError("Use an integer Z-score window of at least three sessions.")
            if not all(isfinite(x) for x in (self.entry, self.exit)) or not 0 <= self.exit < self.entry:
                raise ValueError("Use thresholds with 0 ≤ exit < entry.")
        elif self.name == DONCHIAN_BREAKOUT:
            lookbacks = (self.breakout_window, self.channel_exit_window, self.breakout_vol_window)
            if any(type(n) is not int for n in lookbacks) or not 2 <= self.channel_exit_window < self.breakout_window:
                raise ValueError("Use integer channel windows with 2 ≤ exit < entry.")
            if self.breakout_vol_window < 2 or not isfinite(self.breakout_filter) or self.breakout_filter < 0:
                raise ValueError("Use a volatility window of at least two sessions and a nonnegative breakout buffer.")
        elif self.name == CURVE_CARRY:
            if not isfinite(self.carry_threshold) or self.carry_threshold < 0:
                raise ValueError("Use a finite nonnegative dollar carry threshold per 30 days.")


def breakout_levels(prices, strategy, high=None, low=None):
    """Price thresholds based on complete observations strictly before each row."""
    prices = prices.astype(float)
    if high is None and low is None:
        high = low = prices
        daily_range = prices.diff().abs()
    else:
        previous_close = prices.shift(1)
        daily_range = pd.concat([high - low, (high - previous_close).abs(), (low - previous_close).abs()], axis=1).max(axis=1)
    upper, lower = high.shift(1), low.shift(1)
    buffer = 0.0
    if strategy.breakout_filter:
        buffer = daily_range.shift(1).rolling(strategy.breakout_vol_window).mean() * strategy.breakout_filter
    entry_high = upper.rolling(strategy.breakout_window).max() + buffer
    entry_low = lower.rolling(strategy.breakout_window).min() - buffer
    exit_high = upper.rolling(strategy.channel_exit_window).max()
    exit_low = lower.rolling(strategy.channel_exit_window).min()
    return entry_high, entry_low, exit_high, exit_low


def _breakout_signal(prices, strategy, high=None, low=None):
    entry_high, entry_low, exit_high, exit_low = breakout_levels(prices, strategy, high, low)
    position = 0
    values = []
    for price, top, bottom, cover, sell in zip(prices.astype(float), entry_high, entry_low, exit_high, exit_low):
        if not all(map(isfinite, (price, top, bottom, cover, sell))):
            position = 0
        elif (position > 0 and price < sell) or (position < 0 and price > cover):
            position = 0
        elif position == 0 and price > top:
            position = 1
        elif position == 0 and price < bottom:
            position = -1
        values.append(position)
    return pd.Series(values, index=prices.index, dtype=int)


def curve_carry_per_30_days(near, far, expiry_gap_days):
    """Dollar slope from contemporaneous nearby/deferred marks, including negative prices."""
    if not isfinite(expiry_gap_days) or expiry_gap_days <= 0:
        raise ValueError("The deferred contract must expire after the nearby contract.")
    return (near - far) * (30 / expiry_gap_days)


def signal_series(prices, strategy, *, high=None, low=None):
    strategy.validate()
    if strategy.name == CURVE_CARRY:
        raise ValueError("Curve carry requires a synchronized contract pair and expiry gap.")
    if strategy.name == DONCHIAN_BREAKOUT:
        if (high is None) != (low is None):
            raise ValueError("Pass both high and low prices or neither for a settlement-only breakout.")
        return _breakout_signal(prices, strategy, high, low)
    if strategy.name == LONG_SPREAD:
        return prices.notna().astype(int)
    if strategy.name == "Momentum":
        fast = prices.rolling(strategy.fast).mean()
        slow = prices.rolling(strategy.slow).mean()
        return np.sign(fast - slow).fillna(0).astype(int)

    mean = prices.rolling(strategy.window).mean()
    std = prices.rolling(strategy.window).std().replace(0, np.nan)
    z_score = (prices - mean) / std
    position = 0
    values = []
    for value in z_score:
        if pd.isna(value) or abs(value) <= strategy.exit:
            position = 0
        elif value >= strategy.entry:
            position = -1
        elif value <= -strategy.entry:
            position = 1
        values.append(position)
    return pd.Series(values, index=prices.index, dtype=int)


def signals(df, strategy, pairs=()):
    price_column = "settlement" if "settlement" in df else "close"
    closes = df.pivot(index="date", columns="contract", values=price_column).sort_index()
    if strategy.name == CURVE_CARRY:
        strategy.validate()
        expiry = df.groupby("contract").expiry.first()
        result = {}
        for near, far in pairs:
            slope = curve_carry_per_30_days(closes[near], closes[far], (expiry[far] - expiry[near]).days)
            qualifying = (slope.abs() >= strategy.carry_threshold) & np.isfinite(slope)
            result[(near, far)] = np.sign(slope.where(qualifying, 0)).astype(int)
        return result
    if strategy.name in SPREAD_STRATEGIES:
        return {pair: signal_series(closes[pair[0]] - closes[pair[1]], strategy) for pair in pairs}
    if strategy.name == DONCHIAN_BREAKOUT and price_column == "close":
        highs = df.pivot(index="date", columns="contract", values="high").reindex_like(closes)
        lows = df.pivot(index="date", columns="contract", values="low").reindex_like(closes)
        return {contract: signal_series(closes[contract], strategy, high=highs[contract], low=lows[contract]) for contract in closes}
    return {contract: signal_series(closes[contract], strategy) for contract in closes}
