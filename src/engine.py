"""Daily futures ledger with independent marks and fills for every contract."""
from dataclasses import asdict, dataclass
from math import isfinite

import pandas as pd

from .contract_specs import SPECS
from .data import validate
from .risk_metrics import metrics
from .rollover import ROLL_RULES, Selection, roll_deadlines, roll_schedule
from .strategies import CURVE_CARRY, PAIR_SIGNAL_STRATEGIES, SPREAD_STRATEGIES, Strategy, signals

FILL_COLUMNS = ["date", "when", "group", "contract", "action", "quantity", "price", "cost", "reason"]
TRADE_COLUMNS = [
    "group", "contract", "entry_date", "exit_date", "quantity", "direction",
    "entry_price", "exit_price", "gross_pnl", "costs", "net_pnl", "reason",
    "entry_signal_date", "entry_spread", "entry_regime",
]
ROLL_COLUMNS = ["date", "from_contract", "to_contract", "reason"]


@dataclass(frozen=True)
class Settings:
    capital: float = 100_000
    multiplier: float = 1_000
    tick: float = 0.01
    commission: float = 2.50
    slippage_ticks: float = 1
    contracts: int = 1
    margin: float = 6_000
    max_leverage: float = 3
    max_drawdown: float = 0.25
    roll_days: int = 5
    roll_rule: str = "Expiry buffer"

    def validate(self):
        values = asdict(self)
        values.pop("roll_rule")
        if not all(isfinite(value) for value in values.values()):
            raise ValueError("Settings must be finite.")
        if min(self.capital, self.multiplier, self.tick, self.margin, self.max_leverage) <= 0:
            raise ValueError("Capital, multiplier, tick, margin and leverage must be positive.")
        if min(self.commission, self.slippage_ticks) < 0:
            raise ValueError("Commission and slippage must be nonnegative.")
        if type(self.contracts) is not int or type(self.roll_days) is not int or self.contracts < 1 or self.roll_days < 0:
            raise ValueError("Use whole contract counts ≥ 1 and whole roll buffers ≥ 0.")
        if not 0 < self.max_drawdown <= 1:
            raise ValueError("Drawdown limit must be in (0, 1].")
        if self.roll_rule not in ROLL_RULES:
            raise ValueError("Unknown rollover rule.")

    @property
    def cost_per_side(self):
        return self.commission + self.slippage_ticks * self.tick * self.multiplier


@dataclass(frozen=True)
class Result:
    daily: pd.DataFrame
    trades: pd.DataFrame
    fills: pd.DataFrame
    round_trips: pd.DataFrame
    rolls: pd.DataFrame
    metrics: dict
    settings: dict
    strategy: dict


def net_round_trips(trades):
    return trades.groupby("group", as_index=False).agg(
        entry_date=("entry_date", "first"),
        exit_date=("exit_date", "first"),
        contracts=("contract", ", ".join),
        legs=("contract", "size"),
        gross_pnl=("gross_pnl", "sum"),
        costs=("costs", "sum"),
        net_pnl=("net_pnl", "sum"),
        reason=("reason", "first"),
        entry_signal_date=("entry_signal_date", "first"),
        entry_spread=("entry_spread", "first"),
        entry_regime=("entry_regime", "first"),
    )


def spread_regime(spread):
    if pd.isna(spread):
        return None
    if abs(spread) < 1e-8:
        return "Flat curve"
    return "Contango" if spread < 0 else "Backwardation"


def backtest(data, strategy=Strategy(), settings=None, start=None, end=None, targets=None, *, sessions=()):
    """Run a flat-start account on inclusive dates with pre-period indicator warmup."""
    df = validate(data)
    first_contract = df.contract.iloc[0]
    product = next((spec for code, spec in SPECS.items() if first_contract.startswith(code)), None)
    if settings is None:
        spec = product or SPECS["CL"]
        settings = Settings(multiplier=spec.multiplier, tick=spec.tick, margin=spec.illustrative_margin)
    if product and (settings.multiplier, settings.tick) != (product.multiplier, product.tick):
        raise ValueError(f"Multiplier and tick must match the {product.code} product.")
    settings.validate()
    strategy.validate()
    settlement_only = "settlement" in df
    if settlement_only and settings.roll_rule != "Expiry buffer":
        raise ValueError("Settlement-only data supports the expiry roll rule. Volume rolls require volume quotes.")
    if settlement_only and settings.roll_days == 0:
        raise ValueError("Settlement-proxy runs require an expiry buffer of at least one trading session.")
    execution_column = "settlement" if settlement_only else "open"
    mark_column = "settlement" if settlement_only else "close"
    if end is not None:
        df = df.loc[df.date <= pd.Timestamp(end)]
    all_dates = pd.DatetimeIndex(df.date.unique())
    dates = all_dates if start is None else all_dates[all_dates >= pd.Timestamp(start)]
    if len(dates) < 2:
        raise ValueError("Select at least two trading sessions.")
    panels = {date: rows.set_index("contract") for date, rows in df.groupby("date", sort=False)}
    expiries = df.groupby("contract").expiry.first()
    deadlines = roll_deadlines(expiries, settings.roll_days, sessions)
    schedule = roll_schedule(all_dates, panels, expiries, deadlines, settings.roll_rule)
    pairs = {(s.near, s.far) for s in schedule.values() if s.near and s.far}
    signal_map = signals(df, strategy, sorted(pairs)) if targets is None else {}

    equity = peak = float(settings.capital)
    multiplier = settings.multiplier
    side_cost = settings.cost_per_side
    positions, marks, entries = {}, {}, {}
    daily, trades, fills, rolls = [], [], [], []
    halted, group, entry_signal_date, entry_spread = False, 0, None, None
    first_index = all_dates.get_loc(dates[0])
    for index, date in enumerate(dates, start=first_index):
        prior = all_dates[index - 1] if index else None
        previous_selection, selection = schedule.get(prior, Selection()), schedule.get(date, Selection())
        bar = panels[date]
        final_settlement = settlement_only and date == dates[-1]
        held_at_start = bool(positions)
        day_cost = 0.0

        def close_positions(price_column, reason):
            nonlocal equity, day_cost
            for contract, quantity in positions.items():
                price = bar.at[contract, price_column]
                entry_date, entry_price = entries[contract]
                cost = abs(quantity) * side_cost
                gross = quantity * (price - entry_price) * multiplier
                direction = "Long" if quantity > 0 else "Short"
                trades.append((group, contract, entry_date, date, quantity, direction,
                               entry_price, price, gross, 2 * cost, gross - 2 * cost, reason,
                               entry_signal_date, entry_spread, spread_regime(entry_spread)))
                fills.append((date, price_column, group, contract, "Close", -quantity, price, cost, reason))
                equity -= cost
                day_cost += cost
            positions.clear()
            entries.clear()

        for contract, quantity in positions.items():
            missed_deadline = prior < deadlines[contract] < date if prior is not None else False
            expired = date > expiries[contract] or (settlement_only and date == expiries[contract])
            if expired or missed_deadline or contract not in bar.index:
                raise ValueError(f"Cannot mark or exit held contract {contract} safely on {date.date()}. Check expiry and missing quotes.")
            equity += quantity * (bar.at[contract, execution_column] - marks[contract]) * multiplier
        halted = halted or equity <= 0 or equity / peak - 1 <= -settings.max_drawdown

        if previous_selection.near and selection.near != previous_selection.near:
            rolls.append((date, previous_selection.near, selection.near, selection.reason))

        desired = {}
        if prior is not None and not halted and not final_settlement:
            if targets is not None:
                proposed = targets.get(prior, {})
                for contract, quantity in proposed.items():
                    if type(quantity) is not int:
                        raise ValueError("Explicit targets must use integer quantities.")
                    if not quantity:
                        continue
                    if contract not in panels[prior].index or date >= deadlines[contract]:
                        raise ValueError(f"Target {contract} violates prior-quote or expiry eligibility.")
                    desired[contract] = quantity
            elif strategy.name in PAIR_SIGNAL_STRATEGIES and selection.far:
                pair = (selection.near, selection.far)
                quantity = int(signal_map[pair].at[prior]) * settings.contracts
                if quantity:
                    desired = {selection.near: quantity}
                    if strategy.name in SPREAD_STRATEGIES:
                        desired[selection.far] = -quantity
            elif strategy.name not in PAIR_SIGNAL_STRATEGIES and selection.near:
                quantity = int(signal_map[selection.near].at[prior]) * settings.contracts
                if quantity:
                    desired = {selection.near: quantity}

        for contract in desired:
            if contract not in bar.index:
                raise ValueError(f"Missing execution quote for {contract} on {date.date()}.")
        lots = sum(abs(q) for q in desired.values())
        margin_used = lots * settings.margin
        notional = sum(abs(q * bar.at[c, execution_column] * multiplier) for c, q in desired.items())
        turnover = lots + sum(abs(q) for q in positions.values()) if desired != positions else 0
        projected_equity = equity - turnover * side_cost
        blocked = bool(desired) and (
            margin_used > projected_equity or notional > projected_equity * settings.max_leverage
        )
        if blocked:
            desired = {}

        if desired != positions:
            reason = "Signal"
            if final_settlement:
                reason = "End of period"
            elif halted:
                reason = "Drawdown halt"
            elif blocked:
                reason = "Margin or leverage limit"
            elif any(date >= deadlines[c] for c in positions):
                reason = "Expiry buffer"
            elif targets is None:
                old_pair = (previous_selection.near, previous_selection.far)
                new_pair = (selection.near, selection.far)
                if previous_selection.near != selection.near or (strategy.name in PAIR_SIGNAL_STRATEGIES and old_pair != new_pair):
                    reason = selection.reason or "Contract selection changed"
            close_positions(execution_column, reason)
            if desired:
                group += 1
                entry_signal_date, entry_spread = prior, None
                has_curve_entry = selection.far in desired or (strategy.name == CURVE_CARRY and bool(selection.far))
                if strategy.name in PAIR_SIGNAL_STRATEGIES and selection.near in desired and has_curve_entry:
                    entry_spread = (panels[prior].at[selection.near, mark_column]
                                    - panels[prior].at[selection.far, mark_column])
                for contract, quantity in desired.items():
                    price = bar.at[contract, execution_column]
                    cost = abs(quantity) * side_cost
                    entries[contract] = (date, price)
                    fills.append((date, execution_column, group, contract, "Open", quantity, price, cost, reason))
                    equity -= cost
                    day_cost += cost
                positions.update(desired)

        exposed = bool(positions) or (settlement_only and held_at_start)
        gross = net = 0
        for contract, quantity in positions.items():
            open_price, close_price = bar.at[contract, execution_column], bar.at[contract, mark_column]
            equity += quantity * (close_price - open_price) * multiplier
            marks[contract] = close_price
            leg_notional = quantity * close_price * multiplier
            gross += abs(leg_notional)
            net += leg_notional
        if date == dates[-1]:
            close_positions(mark_column, "End of period")

        peak = max(peak, equity)
        drawdown = equity / peak - 1
        halted = halted or equity <= 0 or drawdown <= -settings.max_drawdown
        daily.append({
            "date": date, "signal_date": prior, "equity": equity, "cost": day_cost, "drawdown": drawdown,
            "drawdown_usd": equity - peak, "gross_exposure": gross, "net_exposure": net,
            "margin_used": sum(abs(q) for q in desired.values()) * settings.margin,
            "exposed": exposed, "halted": halted, "risk_blocked": blocked,
            "near_contract": selection.near, "far_contract": selection.far,
            "near_quantity": desired.get(selection.near, 0),
            "far_quantity": desired.get(selection.far, 0),
        })

    ledger = pd.DataFrame(daily).set_index("date")
    ledger["pnl"] = ledger.equity.diff()
    ledger.iloc[0, ledger.columns.get_loc("pnl")] = ledger.equity.iloc[0] - settings.capital
    trade_frame = pd.DataFrame(trades, columns=TRADE_COLUMNS)
    round_trips = net_round_trips(trade_frame)
    return Result(
        ledger, trade_frame, pd.DataFrame(fills, columns=FILL_COLUMNS), round_trips,
        pd.DataFrame(rolls, columns=ROLL_COLUMNS), metrics(ledger, round_trips, settings.capital),
        asdict(settings), asdict(strategy),
    )
