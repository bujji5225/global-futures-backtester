"""Chronological evaluation and walk-forward WTI curve research."""
from dataclasses import asdict, dataclass, replace
from io import BytesIO
import json
from zipfile import ZIP_DEFLATED, ZipFile

import pandas as pd

from .engine import backtest
from .strategies import LONG_SPREAD, Strategy

BASE = "Spread reversion"
STRESS = "Higher slippage"
BENCHMARK = "Always long spread"
HYPOTHESIS = (
    "A nearby-minus-deferred WTI spread that has moved far from its recent average may revert. "
    "Compare the net outcomes of entries signaled in contango and backwardation."
)


@dataclass(frozen=True)
class Split:
    development_start: pd.Timestamp
    development_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp


def chronological_split(data, start, end, development_share=0.70):
    if not 0 < development_share < 1:
        raise ValueError("Development proportion must be between 0 and 100%.")
    dates = pd.DatetimeIndex(data.date.unique()).sort_values()
    dates = dates[(dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end))]
    boundary = int(len(dates) * development_share)
    if min(boundary, len(dates) - boundary) < 2:
        raise ValueError("The split needs at least two sessions in both development and holdout.")
    return Split(dates[0], dates[boundary - 1], dates[boundary], dates[-1])


def evaluate(data, strategy, settings, split, *, sessions=()):
    return {
        "Development": backtest(data, strategy, settings, split.development_start, split.development_end, sessions=sessions),
        "Holdout": backtest(data, strategy, settings, split.test_start, split.test_end, sessions=sessions),
    }


@dataclass(frozen=True)
class Fold:
    window: Split
    development: object
    tests: dict


@dataclass(frozen=True)
class CurveStudy:
    folds: tuple
    development_sessions: int
    test_sessions: int
    extra_slippage: float

    def windows(self):
        rows = []
        for i, fold in enumerate(self.folds, 1):
            for scenario, result in fold.tests.items():
                rows.append({
                    "Window": i, "Dev start": fold.window.development_start, "Dev end": fold.window.development_end,
                    "Test start": fold.window.test_start, "Test end": fold.window.test_end, "Scenario": scenario,
                    "Development P&L": fold.development.metrics["Net P&L"] if scenario == BASE else None,
                    "Net P&L": result.metrics["Net P&L"], "Trades": result.metrics["Trades"],
                    "Max drawdown": result.metrics["Max drawdown"], "Trading costs": result.metrics["Costs"],
                })
        return pd.DataFrame(rows)

    def ledgers(self, table, scenario=BASE):
        return pd.concat(
            [getattr(fold.tests[scenario], table).assign(Window=i) for i, fold in enumerate(self.folds, 1)],
            ignore_index=True,
        )

    def regimes(self):
        trades = self.ledgers("round_trips")
        return trades.groupby("entry_regime", as_index=False).agg(
            Trades=("net_pnl", "size"), Net_PnL=("net_pnl", "sum"),
            Avg_PnL=("net_pnl", "mean"), Costs=("costs", "sum"),
        ).rename(columns={
            "entry_regime": "Signal curve", "Net_PnL": "Net P&L", "Avg_PnL": "Average per trade",
        })


def walk_forward_windows(dates, development, test):
    if min(development, test) < 2:
        raise ValueError("Development and test windows each require at least two sessions.")
    index = pd.DatetimeIndex(dates).unique().sort_values()
    bounds = range(development, len(index) - test + 1, test)
    return tuple(
        Split(index[i - development], index[i - 1], index[i], index[i + test - 1])
        for i in bounds
    )


def study_curve(dataset, strategy, settings, development, test, extra_slippage, start=None, end=None):
    """Evaluate frozen rules over non-overlapping test windows with independent accounts."""
    if strategy.name != "Calendar spread":
        raise ValueError("Curve research requires Calendar spread parameters.")
    if development < strategy.window:
        raise ValueError("Development must cover at least the configured Z-score lookback.")
    if not 0 < extra_slippage <= 50:
        raise ValueError("Use a positive slippage increase of at most 50 ticks per side.")
    dates = dataset.frame.date
    first = pd.Timestamp(start) if start is not None else dates.min()
    last = pd.Timestamp(end) if end is not None else dates.max()
    windows = walk_forward_windows(dates[dates.between(first, last)], development, test)
    if len(windows) < 2:
        raise ValueError("Select enough data for at least two complete test windows.")

    stress_settings = replace(settings, slippage_ticks=settings.slippage_ticks + extra_slippage)
    benchmark = Strategy(LONG_SPREAD)
    folds = []
    for window in windows:
        working = dataset.frame.loc[dates.between(window.development_start, window.test_end)]
        dev = backtest(working, strategy, settings, window.development_start, window.development_end, sessions=dataset.calendar)
        test_setups = {BASE: (strategy, settings), STRESS: (strategy, stress_settings), BENCHMARK: (benchmark, settings)}
        tests = {
            label: backtest(working, spec, setup, window.test_start, window.test_end, sessions=dataset.calendar)
            for label, (spec, setup) in test_setups.items()
        }
        folds.append(Fold(window, dev, tests))
    return CurveStudy(tuple(folds), development, test, extra_slippage)


def study_brief(dataset, study, notes=""):
    rows = study.windows()
    base = rows.loc[rows.Scenario.eq(BASE)]
    high = base.loc[base["Net P&L"].idxmax()]
    low = base.loc[base["Net P&L"].idxmin()]
    total = rows.groupby("Scenario")["Net P&L"].sum()
    params = study.folds[0].tests[BASE].strategy
    settings = study.folds[0].tests[BASE].settings
    trades = study.ledgers("round_trips")
    source_context = (
        "The source provides daily published settlements. Fills use the next settlement as a proxy. "
        "Intraday prices, traded volume, and executable bid/ask spreads are unavailable."
        if dataset.settlement_only else
        "Daily OHLCV drives next-open simulation. Slippage and margin are researcher assumptions."
    )

    def dollars(value):
        return f"-${abs(value):,.0f}" if value < 0 else f"${value:,.0f}"

    lines = [
        "# WTI calendar spread | Analyst brief", "",
        f"**Data** — {dataset.label} · {dataset.calendar_label} · `{dataset.fingerprint[:12]}`", "",
        "## Research question", "",
        HYPOTHESIS, "",
        "Compare synchronized maturities. Contango is nearby below deferred. Backwardation is nearby above deferred. "
        "Storage constraints and expiry pressure can change the relationship between the legs.", "",
        "## Method", "",
        f"{len(study.folds)} disjoint test windows from {base['Test start'].min():%Y-%m-%d} to {base['Test end'].max():%Y-%m-%d}. "
        f"Each uses {study.development_sessions} preceding development sessions and {study.test_sessions} test sessions. "
        f"Frozen rules use a {params['window']}-session Z-score, entry {params['entry']:g}, exit {params['exit']:g}, "
        f"and a {settings['roll_days']}-session buffer under the {settings['roll_rule'].lower()} rule. "
        f"The benchmark stays long nearby and short deferred under the same constraints. Each test starts with "
        f"{dollars(settings['capital'])} and {settings['contracts']} contract(s) per leg. Commission is "
        f"${settings['commission']:g} per side. Base slippage is {settings['slippage_ticks']:g} tick(s), "
        f"with {study.extra_slippage:g} added in the stress run.", "",
        "## Findings", "",
        f"- Net P&L summed across independent tests was **{dollars(total[BASE])}** for spread reversion, "
        f"**{dollars(total[BENCHMARK])}** for the benchmark, and **{dollars(total[STRESS])}** under higher slippage.",
        f"- The base case completed **{base.Trades.sum()}** spreads. Best window {int(high.Window)} returned "
        f"{dollars(high['Net P&L'])}. Weakest window {int(low.Window)} returned {dollars(low['Net P&L'])}. "
        f"The deepest single-window drawdown was {base['Max drawdown'].min():.1%}.",
    ]
    for _, regime in study.regimes().iterrows():
        lines.append(
            f"- {regime['Signal curve']} produced {int(regime['Trades'])} completed spreads at "
            f"{dollars(regime['Average per trade'])} average net P&L per spread."
        )
    if len(trades):
        trade = trades.loc[trades.net_pnl.idxmin()]
        lines.append(
            f"- Worst base-case spread, {trade.contracts}, entered {trade.entry_date:%Y-%m-%d}, "
            f"exited {trade.exit_date:%Y-%m-%d}, and returned {dollars(trade.net_pnl)} after {dollars(trade.costs)} in costs."
        )
    lines.extend([
        "", "## Decision and limitations", "",
        "Compare consistency across windows, trade counts by regime, and sensitivity to execution before trusting a signal. "
        "The regime summaries describe entries in each state. Sample sizes and exposure can differ.", "",
        source_context,
    ])
    if dataset.label == "EIA WTI settlement archive":
        lines.extend(["", "[EIA NYMEX futures data and methodology](https://www.eia.gov/dnav/pet/PET_PRI_FUT_S1_W.htm)"])
    if dataset.synthetic:
        lines.extend(["", "Prices and volumes are synthetic. Findings illustrate the workflow."])
    if notes.strip():
        lines.extend(["", "## Analyst decision", "", notes.strip()])
    return "\n".join(lines) + "\n"


def study_evidence(dataset, study, brief):
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as bundle:
        bundle.writestr("analyst-brief.md", brief)
        bundle.writestr("walk-forward.csv", study.windows().to_csv(index=False))
        bundle.writestr("entry-regimes.csv", study.regimes().to_csv(index=False))
        bundle.writestr("base-spread-legs.csv", study.ledgers("trades").to_csv(index=False))
        bundle.writestr("base-spread-round-trips.csv", study.ledgers("round_trips").to_csv(index=False))
        if dataset.calendar:
            bundle.writestr("session-calendar.csv", pd.Series(dataset.calendar, name="date").to_csv(index=False))
        setup = {
            "data": dataset.label, "sha256": dataset.fingerprint, "execution": dataset.execution,
            "calendar": dataset.calendar_label, "development_sessions": study.development_sessions,
            "test_sessions": study.test_sessions, "extra_slippage_ticks": study.extra_slippage,
            "strategy": asdict(Strategy(**study.folds[0].tests[BASE].strategy)),
            "settings": {label: result.settings for label, result in study.folds[0].tests.items()},
        }
        bundle.writestr("study.json", json.dumps(setup, indent=2))
    return output.getvalue()
