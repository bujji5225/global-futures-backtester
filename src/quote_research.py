"""Validate synchronized quote inputs for live and static strategy research."""
from collections import defaultdict
from datetime import date, timedelta
from math import isfinite
import re
from zoneinfo import ZoneInfo

import pandas as pd

from .data import csv_frame
from .options_strategies import OptionQuote
from .strategies import DONCHIAN_BREAKOUT, Strategy, breakout_levels, curve_carry_per_30_days

OPTION_COLUMNS = ("symbol", "underlying", "expiry", "right", "strike", "bid", "ask")


def grouped_chain(quotes):
    groups = defaultdict(list)
    for quote in quotes:
        groups[quote.underlying, quote.expiry].append(quote)
    return dict(sorted(groups.items()))


def csv_chain(content, product):
    frame = csv_frame(content)
    if frame.columns.has_duplicates or set(OPTION_COLUMNS) - set(frame.columns) or frame.empty:
        raise ValueError(f"Upload distinct CSV columns {', '.join(OPTION_COLUMNS)} with at least one quote.")
    quotes = [OptionQuote(*row) for row in frame[list(OPTION_COLUMNS)].itertuples(index=False, name=None)]
    if len({quote.symbol for quote in quotes}) != len(quotes):
        raise ValueError("A snapshot can contain only one quote per option symbol.")
    if any(not re.fullmatch(rf"{product}[FGHJKMNQUVXZ](?:[0-9]{{1,2}}|[0-9]{{4}})", quote.underlying)
           for quote in quotes):
        raise ValueError(f"Use dated {product} underlying futures identifiers in the snapshot.")
    return quotes


def sample_chain(product):
    underlying = f"{product}G27"
    expiry = date(2027, 1, 15)
    strike_premiums = [
        (62, (8.15, 8.29), (0.12, 0.17)), (65, (5.42, 5.56), (0.48, 0.57)),
        (68, (3.25, 3.38), (1.22, 1.36)), (70, (2.22, 2.34), (2.17, 2.29)),
        (72, (1.29, 1.42), (3.27, 3.42)), (75, (0.45, 0.55), (5.48, 5.66)),
        (78, (0.15, 0.21), (8.09, 8.26)),
    ]
    return [
        OptionQuote(f"DEMO-{underlying}-{right}{strike}", underlying, expiry, right, strike, *premium)
        for strike, call, put in strike_premiums
        for right, premium in (("C", call), ("P", put))
    ]


def live_option_quotes(snapshot):
    if snapshot.status != "live":
        return {}, {}
    quotes, originals = {}, {}
    for row in snapshot.options:
        if row.quote_status != "fresh" or not row.expiry or not row.underlying_symbol:
            continue
        try:
            quote = OptionQuote(row.symbol, row.underlying_symbol, row.expiry.date(), row.right, row.strike, row.bid, row.ask)
        except ValueError:
            continue
        quotes[quote.symbol] = quote
        originals[quote.symbol] = row
    return quotes, originals


def match_quote_times(rows, max_skew_seconds=12):
    """Return the most recent timestamp after checking every selected quote."""
    if not rows or any(row.quote_status != "fresh" or row.timestamp is None for row in rows):
        raise ValueError("Wait for fresh two-sided quotes on every selected leg.")
    dates = [row.timestamp for row in rows]
    if (max(dates) - min(dates)).total_seconds() > max_skew_seconds:
        raise ValueError(f"Selected quote timestamps differ by more than {max_skew_seconds:g} seconds.")
    return max(dates)


def live_carry(near, far, threshold):
    as_of = match_quote_times([near, far])
    if not near.expiry or not far.expiry or near.mid is None or far.mid is None:
        raise ValueError("Both futures need dated contract definitions and two-sided quotes.")
    if not isfinite(threshold) or threshold < 0:
        raise ValueError("Carry threshold must be nonnegative.")
    days = (far.expiry.date() - near.expiry.date()).days
    carry = curve_carry_per_30_days(near.mid, far.mid, days)
    lean = "Flat"
    if carry and abs(carry) >= threshold:
        lean = "Long nearby" if carry > 0 else "Short nearby"
    return carry, lean, as_of


def live_breakout_reference(row, dataset):
    """Compare a fresh midpoint with a channel from the matching future's prior daily bars."""
    quote_time = match_quote_times([row])
    if dataset is None or dataset.synthetic or row.expiry is None or row.mid is None:
        raise ValueError("Select recent historical bars for this dated futures contract.")
    exchange_time = quote_time.astimezone(ZoneInfo("America/Chicago"))
    session = pd.Timestamp(exchange_time.date() + timedelta(days=int(exchange_time.hour >= 17)))
    expiry = pd.Timestamp(row.expiry.astimezone(ZoneInfo("America/Chicago")).date())
    frame = dataset.frame
    bars = frame.loc[frame.expiry.eq(expiry) & frame.date.lt(session)].set_index("date").sort_index()
    if bars.empty or session - bars.index[-1] > pd.Timedelta(days=4):
        raise ValueError("The matching contract needs recent completed daily bars.")
    # Reindex by market sessions to keep missing contract bars visible to rolling indicators.
    sessions = pd.DatetimeIndex(dataset.calendar or tuple(frame.date.unique())).sort_values()
    sessions = sessions[(sessions >= bars.index[0]) & (sessions < session)]
    prices = bars.reindex(sessions.append(pd.DatetimeIndex([session])))
    prices.loc[session, dataset.price_column] = row.mid
    daily_ranges = {} if dataset.settlement_only else {"high": prices.high, "low": prices.low}
    upper, lower, _, _ = breakout_levels(prices[dataset.price_column], Strategy(DONCHIAN_BREAKOUT), **daily_ranges)
    top, bottom = upper.iloc[-1], lower.iloc[-1]
    if not all(map(isfinite, (top, bottom))):
        raise ValueError("The default breakout needs 20 completed bars without gaps on the selected contract.")
    label = "Above channel" if row.mid > top else "Below channel" if row.mid < bottom else "Inside channel"
    return top, bottom, label, bars.index[-1]
