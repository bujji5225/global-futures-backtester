"""Render and archive reproducible research runs."""
from contextlib import closing
from dataclasses import dataclass
from io import BytesIO
import json
from pathlib import Path
import re
import sqlite3
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

MONEY = {
    "Net P&L", "Costs", "Max drawdown (USD)", "Average win", "Average loss",
    "Average gross notional", "Peak gross notional",
}
RATES = {"Return", "Win rate", "Max drawdown", "Annualized volatility", "Exposure"}
HYPOTHESES = {
    "Momentum": "A crossover of short and long moving averages can capture directional persistence in the selected maturity.",
    "Mean reversion": "Large deviations from a contract's recent mean can revert before transaction costs absorb the move.",
    "Calendar spread": "Large deviations in the nearby-minus-deferred price spread can revert across synchronized maturities.",
}
DB_PATH = Path(__file__).resolve().parents[1] / "data" / "experiments.sqlite3"


@dataclass(frozen=True)
class Run:
    product: str
    dataset_label: str
    fingerprint: str
    synthetic: bool
    results: dict
    split: object
    execution: str = "Next session open"
    calendar_label: str = "Weekday approximation"
    calendar: tuple = ()

    @property
    def source(self):
        if self.dataset_label == "EIA WTI settlement archive":
            return self.dataset_label
        prefix = "Synthetic demonstration" if self.synthetic else "User-supplied contract CSV"
        return f"{prefix} · {self.dataset_label}"


def format_metric(name, value):
    if value is None:
        return "—"
    if name in MONEY:
        return f"-${abs(value):,.2f}" if value < -0.005 else f"${abs(value):,.2f}"
    if name in RATES:
        return f"{value:.1%}"
    if name in {"Trades", "Risk-blocked sessions"}:
        return f"{value:,}"
    if value == float("inf"):
        return "∞"
    return f"{value:,.2f}"


def markdown(run, hypothesis="", notes=""):
    split = run.split
    settlements = run.execution == "Next settlement proxy"
    timing = (
        "Signals use information through each published daily settlement. Targets execute at the following settlement as a proxy."
        if settlements else
        "Strategies use information through each session close. Targets execute at the next available open."
    )
    lines = [
        "# WTI futures research report", "",
        f"- **Product** — {run.product}",
        f"- **Data** — {run.source.replace(chr(10), ' ')}",
        f"- **Execution** — {run.execution}",
        f"- **Calendar** — {run.calendar_label}",
        f"- **Dataset SHA-256** — `{run.fingerprint}`",
        f"- **Development** — {split.development_start.date()} to {split.development_end.date()}",
        f"- **Holdout** — {split.test_start.date()} to {split.test_end.date()}", "",
        "## Hypothesis", "",
    ]
    default_hypotheses = dict.fromkeys(
        periods["Holdout"].strategy["name"] for periods in run.results.values()
    )
    lines.extend([hypothesis.strip()] if hypothesis.strip() else [
        f"- **{name}** — {HYPOTHESES[name]}" for name in default_hypotheses
    ])
    lines.extend([
        "", "## Methodology", "",
        f"{timing} Both periods start flat with the same initial capital and can use earlier observations to warm up indicators. "
        f"Any open position is liquidated at the final {'settlement' if settlements else 'session close'} of each period.", "",
        "Contract selection uses an expiry buffer and, when enabled, prior-session volume. "
        "Quotes and dollar P&L remain separate by maturity. Every leg pays commission and modeled tick slippage on both sides. "
        "A calendar trade groups the two opposite legs for win-rate and profit-factor calculations.", "",
    ])
    for label, periods in run.results.items():
        result = periods["Holdout"]
        s, p = result.settings, result.strategy
        params = f"SMA {p['fast']}/{p['slow']}" if p["name"] == "Momentum" else (
            f"Z-score window {p['window']}, entry {p['entry']:g}, exit {p['exit']:g}"
        )
        lines.extend([
            f"### {label}", "",
            f"{params}. {s['contracts']} contract(s) per leg, {s['multiplier']:g} units per contract, "
            f"${s['tick']:g} tick, ${s['commission']:g} commission and {s['slippage_ticks']:g} tick(s) slippage per side. "
            f"Starting equity ${s['capital']:,.2f}, assumed margin ${s['margin']:,.2f} per contract, "
            f"{s['max_leverage']:g}× gross leverage cap and {s['max_drawdown']:.1%} drawdown halt. "
            f"Roll rule {s['roll_rule'].lower()} with a {s['roll_days']}-{'session' if run.calendar else 'weekday'} expiry buffer.", "",
            "| Metric | Development | Holdout |", "| --- | ---: | ---: |",
        ])
        for name in periods["Development"].metrics:
            values = [format_metric(name, periods[key].metrics[name]) for key in ("Development", "Holdout")]
            lines.append(f"| {name} | {values[0]} | {values[1]} |")
        lines.append("")

    lines.extend([
        "## Risk analysis", "",
        "Sharpe and account volatility use daily equity returns and 252 sessions per year, with zero risk-free return. "
        "Drawdown uses closing equity and the starting capital as the initial high-water mark. "
        "Gross notional sums absolute leg exposures, and the margin check assumes no spread offsets.", "",
        ("The archive contains settlement prices and delivery months are reconstructed from the published rank series. "
         "Intraday price and volume observations are unavailable. " if settlements else
         "The execution model uses daily opens and closes. Holiday gaps and liquidity require review against the provider's calendar. ")
        + "The engine uses assumed fixed slippage. "
        "Signal fills and margin constraints are simulated.", "",
        "## Conclusions", "",
    ])
    for label, periods in run.results.items():
        holdout = periods["Holdout"].metrics
        lines.append(
            f"- **{label}** recorded {format_metric('Net P&L', holdout['Net P&L'])} holdout net P&L, "
            f"{format_metric('Max drawdown', holdout['Max drawdown'])} maximum drawdown, "
            f"and {holdout['Trades']} completed trade(s)."
        )
    lines.extend(["", (
        "The dataset uses generated prices and volumes. These outcomes illustrate the simulation workflow."
        if run.synthetic else "These observations describe the selected dataset, dates, and assumptions."
    )])
    if notes.strip():
        lines.extend(["", "### Analyst notes", "", notes.strip()])
    return "\n".join(lines) + "\n"


def evidence_bundle(run, report):
    metadata = {
        "schema_version": 1, "product": run.product, "source": run.source, "synthetic": run.synthetic,
        "execution": run.execution, "calendar": run.calendar_label,
        "dataset_sha256": run.fingerprint,
        "split": {key: str(value.date()) for key, value in vars(run.split).items()},
        "strategies": {},
    }
    buffer = BytesIO()
    with ZipFile(buffer, mode="w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("research-report.md", report)
        if run.calendar:
            archive.writestr("session-calendar.csv", "date\n" + "".join(f"{day:%Y-%m-%d}\n" for day in run.calendar))
        for number, (label, periods) in enumerate(run.results.items(), start=1):
            slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
            folder = f"{number:02d}-{slug}"
            result = periods["Holdout"]
            metadata["strategies"][label] = {"folder": folder, "strategy": result.strategy, "settings": result.settings}
            for period, result in periods.items():
                files = {
                    "daily": result.daily.reset_index(), "trades": result.trades,
                    "round-trips": result.round_trips, "fills": result.fills, "rolls": result.rolls,
                }
                for name, frame in files.items():
                    archive.writestr(f"{folder}/{period.lower()}/{name}.csv", frame.to_csv(index=False))
        archive.writestr("experiment.json", json.dumps(metadata, indent=2))
    return buffer.getvalue()


def save_experiment(run, report, bundle, path=DB_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    experiment_id = uuid4().hex
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS experiments (
                id TEXT PRIMARY KEY,
                saved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                product TEXT NOT NULL,
                source TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                strategies TEXT NOT NULL,
                report TEXT NOT NULL,
                bundle BLOB NOT NULL
            )
        """)
        connection.execute(
            "INSERT INTO experiments (id, product, source, fingerprint, strategies, report, bundle) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (experiment_id, run.product, run.source, run.fingerprint, ", ".join(run.results), report, bundle),
        )
    return experiment_id


def list_experiments(path=DB_PATH):
    if not path.exists():
        return []
    with closing(sqlite3.connect(path)) as connection:
        return connection.execute(
            "SELECT id, saved_at, product, source, strategies FROM experiments ORDER BY saved_at DESC, rowid DESC"
        ).fetchall()


def load_experiment(experiment_id, path=DB_PATH):
    with closing(sqlite3.connect(path)) as connection:
        return connection.execute(
            "SELECT report, bundle FROM experiments WHERE id = ?", (experiment_id,),
        ).fetchone()
