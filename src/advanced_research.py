"""Compare pre-specified futures signals over matched out-of-sample windows."""
from dataclasses import asdict, dataclass, replace
from io import BytesIO
import json
from zipfile import ZIP_DEFLATED, ZipFile

import pandas as pd

from .engine import backtest
from .research import walk_forward_windows

BASELINE = "SMA momentum baseline"
STRESS = "Higher-slippage case"


@dataclass(frozen=True)
class FuturesResearch:
    windows: tuple
    results: tuple
    scenarios: dict

    def comparison(self):
        rows = []
        for i, (window, results) in enumerate(zip(self.windows, self.results), 1):
            for name, result in results.items():
                metrics = result.metrics
                rows.append({
                    "Window": i,
                    "Test start": window.test_start,
                    "Test end": window.test_end,
                    "Scenario": name,
                    "Net P&L": metrics["Net P&L"],
                    "Max drawdown": metrics["Max drawdown"],
                    "Trades": metrics["Trades"],
                    "Trading costs": metrics["Costs"],
                    "Exposure": metrics["Exposure"],
                })
        return pd.DataFrame(rows)

    def paths(self):
        """Sum successive independently funded test-account P&L paths."""
        paths = {}
        for scenario in self.scenarios:
            offset, segments = 0.0, []
            for results in self.results:
                result = results[scenario]
                pnl = result.daily.equity - result.settings["capital"]
                segments.append(pnl + offset)
                offset += pnl.iloc[-1]
            paths[scenario] = pd.concat(segments)
        return paths


def compare_futures(dataset, strategies, benchmark, settings, start, end, development, test, extra_slippage, stress_model):
    if stress_model not in strategies:
        raise ValueError("Select a strategy for the slippage scenario.")
    if not 0 < extra_slippage <= 50:
        raise ValueError("Choose a positive slippage increase of up to 50 ticks per side.")
    for strategy in strategies.values():
        strategy.validate()
    benchmark.validate()
    settings.validate()

    dates = dataset.frame.date
    use = dates.between(pd.Timestamp(start), pd.Timestamp(end))
    lookbacks = [benchmark.slow]
    lookbacks.extend(max(s.breakout_window, s.breakout_vol_window + 1)
                     for s in strategies.values() if s.name == "Donchian breakout")
    if development < max(lookbacks):
        raise ValueError("Development sessions must cover the configured indicator lookbacks.")
    windows = walk_forward_windows(dates[use], development, test)
    if not 2 <= len(windows) <= 32:
        raise ValueError("Choose a date range and window sizes that yield 2–32 complete test windows.")

    stressed = replace(settings, slippage_ticks=settings.slippage_ticks + extra_slippage)
    scenarios = {name: (strategy, settings) for name, strategy in strategies.items()}
    scenarios[BASELINE] = (benchmark, settings)
    scenarios[STRESS] = (strategies[stress_model], stressed)
    results = []
    for window in windows:
        working = dataset.frame.loc[dates.between(window.development_start, window.test_end)]
        results.append({
            name: backtest(working, strategy, setup, window.test_start, window.test_end, sessions=dataset.calendar)
            for name, (strategy, setup) in scenarios.items()
        })
    return FuturesResearch(windows, tuple(results), scenarios)


def futures_evidence(dataset, research):
    metadata = {
        "dataset": dataset.label,
        "dataset_sha256": dataset.fingerprint,
        "execution": dataset.execution,
        "calendar": dataset.calendar_label,
        "windows": [
            {key: str(date.date()) for key, date in asdict(window).items()} for window in research.windows
        ],
        "scenarios": {
            name: {"strategy": asdict(strategy), "settings": asdict(settings)}
            for name, (strategy, settings) in research.scenarios.items()
        },
    }
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("research.json", json.dumps(metadata, indent=2))
        archive.writestr("summary.csv", research.comparison().to_csv(index=False))
        if dataset.calendar:
            archive.writestr("sessions.csv", pd.Series(dataset.calendar, name="date").to_csv(index=False))
        for i, results in enumerate(research.results, 1):
            for name, result in results.items():
                folder = f"window-{i:02d}/{name.lower().replace(' ', '-')}"
                archive.writestr(f"{folder}/daily.csv", result.daily.reset_index().to_csv(index=False))
                archive.writestr(f"{folder}/fills.csv", result.fills.to_csv(index=False))
                archive.writestr(f"{folder}/trades.csv", result.trades.to_csv(index=False))
    return output.getvalue()
