"""Select maturities at the next open using the preceding session's quotes."""
from dataclasses import dataclass

import pandas as pd

ROLL_RULES = ("Expiry buffer", "Prior-session volume")


@dataclass(frozen=True)
class Selection:
    near: str = ""
    far: str = ""
    reason: str = ""


def roll_deadlines(expiries, buffer, sessions=()):
    """First session on which positions must leave the delivery buffer."""
    calendar = pd.DatetimeIndex(sessions).sort_values()
    if calendar.empty:
        offset = pd.offsets.BDay(buffer)
    else:
        closures = pd.bdate_range(calendar[0], calendar[-1]).difference(calendar)
        offset = pd.offsets.CustomBusinessDay(n=buffer, holidays=closures)
    return {code: offset.rollforward(expiry) - offset for code, expiry in expiries.items()}


def roll_schedule(dates, panels, expiries, deadlines, rule):
    """Move only forward. Volume can advance a roll ahead of the expiry buffer."""
    order = list(expiries.sort_values().index)
    rank = {contract: i for i, contract in enumerate(order)}
    earliest = 0
    previous = Selection()
    schedule = {}
    known = set()
    for prior, date in zip(dates[:-1], dates[1:]):
        prior_bar = panels[prior]
        known.update(prior_bar.index)
        eligible = [contract for contract in order[earliest:] if date < deadlines[contract]]
        quoted = [contract for contract in eligible if contract in prior_bar.index]
        near = quoted[0] if quoted else ""
        reason = ""
        if previous.near and previous.near != near:
            reason = "Expiry buffer" if previous.near not in eligible else "Missing prior-session quote"
        if rule == "Prior-session volume" and len(quoted) >= 2:
            first, second = quoted[:2]
            if prior_bar.at[second, "volume"] > prior_bar.at[first, "volume"]:
                near, reason = second, "Prior-session volume"
        if near:
            earliest = rank[near]
        elif previous.near and previous.near not in eligible:
            earliest = rank[previous.near] + 1

        far = ""
        if near:
            following = next((c for c in eligible if rank[c] > rank[near] and c in known), "")
            if following in prior_bar.index:
                far = following
        previous = schedule[date] = Selection(near, far, reason)
    return schedule
