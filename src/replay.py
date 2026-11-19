"""Day-by-day settlement replay of the April 2020 WTI expiry."""
from dataclasses import dataclass

import pandas as pd

from .rollover import roll_deadlines

MAY = "CLK20"
JUNE = "CLM20"
SIDE_COST = 12.50
CL_MULTIPLIER = 1_000


@dataclass(frozen=True)
class Decision:
    date: pd.Timestamp
    requested: str
    outcome: str
    contract: str
    day_pnl: float
    costs: float
    cumulative_pnl: float


def april_panel(dataset):
    quotes = dataset.frame
    event = quotes.loc[quotes.date.between("2020-04-13", "2020-04-24") & quotes.contract.isin([MAY, JUNE])]
    return event.pivot(index="date", columns="contract", values="settlement").sort_index()


def exit_deadline(dataset, buffer):
    expiry = dataset.frame.loc[dataset.frame.contract.eq(MAY), "expiry"].iloc[0]
    return roll_deadlines({MAY: expiry}, buffer, dataset.calendar)[MAY]


def choices(contract):
    if contract == MAY:
        return ("Hold", "Cut", "Roll")
    if contract == JUNE:
        return ("Hold", "Cut")
    return ("Stay flat",)


def advance(panel, decisions, action, deadline):
    index = len(decisions)
    if index >= len(panel) - 1:
        raise ValueError("Replay is complete.")
    current_date, next_date = panel.index[index:index + 2]
    contract = decisions[-1].contract if decisions else MAY
    if action not in choices(contract):
        raise ValueError("This decision is unavailable for the current position.")
    previous_pnl = decisions[-1].cumulative_pnl if decisions else 0.0
    gross = (panel.at[next_date, contract] - panel.at[current_date, contract]) * CL_MULTIPLIER if contract else 0.0
    if pd.isna(gross):
        raise ValueError("A required settlement is absent from the replay.")
    outcome, remaining = action, contract
    if action == "Roll":
        remaining = JUNE
    elif action == "Cut":
        remaining = ""
    elif action == "Hold" and contract == MAY and next_date >= deadline:
        outcome, remaining = "Cut at expiry buffer", ""
    costs = SIDE_COST * (int(bool(contract)) + int(bool(remaining))) if remaining != contract else 0
    return Decision(next_date, action, outcome, remaining, gross - costs, costs, previous_pnl + gross - costs)


def policy_replay(panel, deadline):
    decisions = []
    for next_date in panel.index[1:]:
        held = decisions[-1].contract if decisions else MAY
        action = "Roll" if held == MAY and next_date >= deadline else "Hold"
        decisions.append(advance(panel, decisions, action, deadline))
    return decisions
