"""Streamlit dashboard for the local WTI futures research engine."""
from dataclasses import replace
from html import escape
from pathlib import Path
import sqlite3

import pandas as pd
import streamlit as st

from src.charts import MUTED, comparison_chart, market_chart, series_chart
from src.contract_specs import SPECS
from src.dashboard_advanced import advanced_futures_page
from src.dashboard_live import live_page, options_page
from src.dashboard_research import curve_page, replay_page
from src.dashboard_ui import download_frame, hero, money, plot, show_metrics, show_table
from src.data import demo_data, diagnostics, eia_archive, load_csv, make_dataset
from src.engine import Settings
from src.reports import (
    Run, evidence_bundle, format_metric, list_experiments, load_experiment, markdown, save_experiment,
)
from src.research import chronological_split, evaluate
from src.rollover import ROLL_RULES
from src.strategies import STRATEGY_NAMES, Strategy

st.set_page_config(page_title="Futures Research Engine", page_icon=":material/monitoring:", layout="wide")
CSS_PATH = Path(__file__).resolve().parent / ".streamlit" / "dashboard.css"


@st.cache_data(show_spinner=False)
def synthetic_dataset():
    return make_dataset(demo_data(), "WTI demo · seed 42")


@st.cache_data(show_spinner=False)
def imported_dataset(content, filename, product, calendar):
    return load_csv(content, filename, product, calendar)


@st.cache_data(show_spinner=False)
def historical_dataset():
    return eia_archive()


@st.cache_data(show_spinner=False)
def data_quality(frame):
    return diagnostics(frame)


def period_text(periods):
    return "   ·   ".join(
        f"{label} {result.daily.index[0]:%Y-%m-%d} to {result.daily.index[-1]:%Y-%m-%d}"
        for label, result in periods.items()
    )


def market_page(data, spec, curve_item):
    df = data.frame
    quality = data_quality(df)
    price = data.price_column
    price_name = "Settlement" if data.settlement_only else "Close"
    hero("Market Explorer", "Review prices and volatility by WTI delivery month.", f"NYMEX  /  {spec.code}  /  MARKET DATA")
    st.page_link(curve_item, label="Open Calendar Spread Lab", icon=":material/arrow_forward:")
    latest_session = df.date.max()
    available = df.loc[df.date.eq(latest_session) & df.expiry.gt(latest_session)]
    default_contract = available.contract.iloc[0] if not available.empty else df.contract.iloc[-1]
    contracts = df[["contract", "expiry"]].drop_duplicates().sort_values("expiry", ascending=False)
    expiry_labels = {row.contract: f"{row.contract} · expires {row.expiry:%Y-%m-%d}" for row in contracts.itertuples()}
    controls = st.columns([1.35, 1] if data.settlement_only else [1.2, 1, 1])
    key = f"{spec.code}-{data.fingerprint[:12]}"
    contract = controls[0].selectbox("Contract month", list(expiry_labels), index=list(expiry_labels).index(default_contract),
                                     format_func=expiry_labels.get, key=f"market-contract-{key}")
    candles = False
    if not data.settlement_only:
        candles = controls[1].selectbox("Price chart", ["Closing price", "Candlesticks"], key="market-chart-style") == "Candlesticks"
    averages = controls[-1].multiselect("Moving averages", [10, 20, 50], default=[20], key="market-averages")
    bars = df.loc[df.contract.eq(contract)].copy()
    first, last = bars.date.min().date(), bars.date.max().date()
    period = st.date_input("Chart dates", (first, last), min_value=first, max_value=last, key=f"market-dates-{key}-{contract}")
    if len(period) != 2:
        st.info("Select both chart dates.")
        return
    for window in averages:
        bars[f"sma_{window}"] = bars[price].rolling(window).mean()
    bars["volatility"] = bars[price].diff().rolling(20).std() * 252 ** 0.5
    bars = bars.loc[bars.date.between(pd.Timestamp(period[0]), pd.Timestamp(period[1]))]
    if bars.empty:
        st.info("There are no contract quotes in the selected date range.")
        return
    quote = bars.iloc[-1]
    change = quote[price] - bars.iloc[-2][price] if len(bars) > 1 else None
    cols = st.columns(4)
    cols[0].metric(f"{price_name} / bbl", money(quote[price]), money(change) if change is not None else None, delta_color="off")
    if data.settlement_only:
        cols[1].metric("Quote date", f"{quote.date:%Y-%m-%d}")
    else:
        cols[1].metric("Latest volume", f"{int(quote.volume):,}")
    cols[2].metric("Value per tick", money(spec.tick_value))
    cols[3].metric("Contract size", f"{spec.multiplier:,} bbl")
    with st.container(border=True):
        st.subheader(f"{contract} settlements" if data.settlement_only else f"{contract} price and volume")
        st.caption(f"{spec.settlement} settlement · {len(bars):,} displayed sessions")
        plot(market_chart(bars, averages, candles), "market-prices")
    with st.expander("20-session price volatility"):
        st.caption(f"Annualized standard deviation of daily {price_name.lower()} changes in USD per barrel.")
        plot(series_chart(bars.date, bars.volatility, "Price volatility", color=MUTED), "market-volatility")
    with st.expander("Dataset coverage"):
        st.write(f"{quality['rows']:,} quotes across {quality['sessions']:,} sessions and {quality['contracts']} contracts.")
        weekday_gaps = quality["weekday_gaps"]
        gaps = quality["contract_gaps"]
        if data.calendar:
            dates = pd.DatetimeIndex(df.date.unique())
            supplied = pd.DatetimeIndex(data.calendar)
            weekday_gaps = supplied[(supplied >= dates.min()) & (supplied <= dates.max())].difference(dates)
        st.write(f"{len(weekday_gaps)} missing {'calendar sessions' if data.calendar else 'weekdays'} and {len(gaps)} gaps within individual contract histories.")
        st.caption(f"Calendar · {data.calendar_label}. Individual gaps use observed dataset sessions. Prices stay unfilled.")
        if data.was_reordered:
            st.info("The input arrived out of date order and was sorted by session and maturity.")
        if len(weekday_gaps):
            show_table(pd.DataFrame({"Absent session": weekday_gaps}), "weekday-gaps")
        if len(gaps):
            show_table(gaps, "contract-gaps")
        if data.label == "EIA WTI settlement archive":
            st.markdown("[EIA futures archive and source notes](https://www.eia.gov/dnav/pet/PET_PRI_FUT_S1_W.htm)")
        download_frame("Download selected prices", bars.drop(columns=["volatility"] + [f"sma_{v}" for v in averages]), "contract-prices")


def default_config(data, spec):
    df = data.frame
    return {
        "strategies": {name: Strategy(name) for name in STRATEGY_NAMES},
        "focus": STRATEGY_NAMES[0],
        "dates": (df.date.min().date(), df.date.max().date()),
        "share": 70,
        "settings": Settings(multiplier=spec.multiplier, tick=spec.tick, margin=spec.illustrative_margin),
    }


def make_run(data, spec, strategies, split):
    results = {
        label: evaluate(data.frame, strategy, settings, split, sessions=data.calendar)
        for label, (strategy, settings) in strategies.items()
    }
    return Run(spec.code, data.label, data.fingerprint, data.synthetic, results, split,
               data.execution, data.calendar_label, data.calendar)


def strategy_page(data, spec, state, results_page):
    hero("Strategy Builder", "Set the signal parameters, costs and risk limits for a chronological backtest.", f"{spec.code}  /  RESEARCH")
    config = state["config"]
    cfg = config["settings"]
    momentum, reversion, spread = config["strategies"].values()
    dates = data.frame.date
    with st.form("strategy-settings"):
        st.subheader("Backtest window")
        a, b, c = st.columns([1.5, 1, 1])
        period = a.date_input("Date range", config["dates"], min_value=dates.min().date(), max_value=dates.max().date())
        share = b.slider("Development share", min_value=40, max_value=90, value=config["share"], format="%d%%")
        focus = c.selectbox("Strategy to run", STRATEGY_NAMES, index=STRATEGY_NAMES.index(config["focus"]))
        st.caption("The earliest sessions form the development period. Remaining sessions form the holdout.")
        st.subheader("Signal parameters")
        with st.expander("Momentum · SMA crossover", expanded=True):
            a, b = st.columns(2)
            fast = a.number_input("Fast SMA · sessions", min_value=2, max_value=300, value=momentum.fast)
            slow = b.number_input("Slow SMA · sessions", min_value=3, max_value=400, value=momentum.slow)
        with st.expander("Mean reversion · contract close"):
            a, b, c = st.columns(3)
            mean_window = a.number_input("Mean-reversion window", min_value=3, value=reversion.window)
            mean_entry = b.number_input("Mean-reversion entry Z", min_value=0.05, value=reversion.entry, step=0.05)
            mean_exit = c.number_input("Mean-reversion exit Z", min_value=0.0, value=reversion.exit, step=0.05)
        with st.expander("Calendar spread · nearby minus deferred"):
            a, b, c = st.columns(3)
            spread_window = a.number_input("Spread window", min_value=3, value=spread.window)
            spread_entry = b.number_input("Spread entry Z", min_value=0.05, value=spread.entry, step=0.05)
            spread_exit = c.number_input("Spread exit Z", min_value=0.0, value=spread.exit, step=0.05)

        st.subheader("Execution and risk")
        a, b, c = st.columns(3)
        balance = a.number_input("Starting equity · USD", min_value=1.0, value=float(cfg.capital), step=1_000.0)
        commission = b.number_input("Commission per side · USD", min_value=0.0, value=cfg.commission, step=0.25)
        slippage = c.number_input("Slippage per side · ticks", min_value=0.0, value=float(cfg.slippage_ticks), step=0.25)
        a, b, c = st.columns(3)
        lots = a.number_input("Contracts per leg", min_value=1, value=cfg.contracts)
        margin = b.number_input("Assumed margin per contract · USD", min_value=1.0, value=float(cfg.margin), step=100.0)
        leverage = c.number_input("Gross leverage cap", min_value=0.1, value=float(cfg.max_leverage), step=0.1)
        a, b, c = st.columns(3)
        max_drawdown = a.number_input("Drawdown halt · %", min_value=0.1, max_value=100.0, value=100 * cfg.max_drawdown, step=1.0)
        rules = ROLL_RULES[:1] if data.settlement_only else ROLL_RULES
        rule = b.selectbox("Roll rule", rules, index=rules.index(cfg.roll_rule))
        buffer_days = c.number_input("Expiry buffer · sessions" if data.calendar else "Expiry buffer · weekdays",
                                     min_value=1 if data.settlement_only else 0, max_value=60, value=cfg.roll_days)
        st.caption(f"{data.execution} · {data.calendar_label}. Margin and slippage are simulation inputs.")
        submitted = st.form_submit_button("Run backtest", type="primary")

    if submitted:
        parameters = (
            Strategy("Momentum", fast=int(fast), slow=int(slow)),
            Strategy("Mean reversion", window=int(mean_window), entry=mean_entry, exit=mean_exit),
            Strategy("Calendar spread", window=int(spread_window), entry=spread_entry, exit=spread_exit),
        )
        strategies = {s.name: s for s in parameters}
        settings = Settings(
            capital=balance, multiplier=spec.multiplier, tick=spec.tick, commission=commission,
            slippage_ticks=slippage, contracts=int(lots), margin=margin, max_leverage=leverage,
            max_drawdown=max_drawdown / 100, roll_days=int(buffer_days), roll_rule=rule,
        )
        try:
            if len(period) != 2:
                raise ValueError("Select a start and an end date.")
            for strategy in strategies.values():
                strategy.validate()
            split = chronological_split(data.frame, *period, share / 100)
            with st.spinner("Simulating development and holdout…"):
                run = make_run(data, spec, {focus: (strategies[focus], settings)}, split)
        except ValueError as error:
            st.error(str(error))
        else:
            state["config"] = {"strategies": strategies, "focus": focus, "dates": period, "share": share, "settings": settings}
            state["run"] = run
            state.pop("comparison", None)
            state.pop("curve_study", None)
            state.pop("advanced_study", None)
            st.success("Backtest completed.")
    if state.get("run"):
        st.page_link(results_page, label="Open Backtest Results", icon=":material/arrow_forward:")


def results_page(spec, state, builder):
    run = state.get("run")
    if not run:
        hero("Backtest Results", "Run a strategy to inspect its account performance and fills.", f"{spec.code}  /  SIMULATION")
        st.page_link(builder, label="Open Strategy Builder", icon=":material/arrow_forward:")
        return
    (label, periods), = run.results.items()
    hero("Backtest Results", f"{label} · {period_text(periods)}", f"{spec.code}  /  SIMULATION")
    period = st.radio("Evaluation period", ["Holdout", "Development"], horizontal=True, key="results-period")
    result = periods[period]
    daily = result.daily
    show_metrics(("Net P&L", "Sharpe", "Max drawdown", "Win rate"), result)
    accounts, trades, risk = st.tabs(["Account performance", "Trades & rolls", "Risk statistics"])
    with accounts:
        with st.container(border=True):
            st.subheader("Account equity")
            plot(series_chart(daily.index, daily.equity, "Equity"), "result-equity")
        a, b = st.columns(2)
        with a:
            st.subheader("Daily P&L")
            plot(series_chart(daily.index, daily.pnl, "Daily P&L", kind="bar"), "result-pnl")
        with b:
            st.subheader("Drawdown")
            plot(series_chart(daily.index, daily.drawdown, "Drawdown", color=MUTED, percent=True, kind="area"), "result-drawdown")
        download_frame("Download daily ledger", daily.reset_index(), "daily-ledger")
    with trades:
        ledger = st.radio("Ledger", ["Round trips", "Contract legs", "Fills", "Rollover schedule"], horizontal=True)
        frames = {
            "Round trips": result.round_trips, "Contract legs": result.trades,
            "Fills": result.fills, "Rollover schedule": result.rolls,
        }
        if ledger == "Rollover schedule":
            st.caption("Changes to the scheduled nearby contract. See Fills for actual simulated transactions.")
        if frames[ledger].empty:
            st.info("There are no records in this ledger for the selected period.")
        else:
            show_table(frames[ledger], "results-ledger")
            download_frame(f"Download {ledger.lower()}", frames[ledger], ledger.lower().replace(" ", "-"))
    with risk:
        show_table(pd.DataFrame({
            "Metric": list(result.metrics),
            "Value": [format_metric(k, v) for k, v in result.metrics.items()],
        }), "risk-metrics")
        st.caption("Sharpe uses a zero risk-free return and 252 sessions. Exposure is the share of simulated sessions with an open position.")
        if daily.halted.any():
            st.warning(f"The drawdown halt activated on {daily.index[daily.halted][0]:%Y-%m-%d}.")


def comparison_page(data, spec, state):
    hero("Strategy Comparison", "Evaluate all three strategies on the same development and holdout windows.", f"{spec.code}  /  RESEARCH")
    config = state["config"]
    share, settings = config["share"], config["settings"]
    st.caption(f"{config['dates'][0]} to {config['dates'][1]} · {share}% development · {settings.roll_rule} · {money(settings.capital)} starting equity")
    alternate = next(rule for rule in ROLL_RULES if rule != settings.roll_rule)
    include_alternate = False
    if not data.settlement_only:
        include_alternate = st.checkbox(f"Also check {config['focus'].lower()} with the {alternate.lower()} roll rule")
    if st.button("Compare strategies", type="primary"):
        try:
            split = chronological_split(data.frame, *config["dates"], share / 100)
            setups = {label: (strategy, settings) for label, strategy in config["strategies"].items()}
            if include_alternate:
                focus = config["focus"]
                setups[f"{focus} / {alternate}"] = (config["strategies"][focus], replace(settings, roll_rule=alternate))
            with st.spinner("Evaluating the strategies…"):
                state["comparison"] = make_run(data, spec, setups, split)
        except ValueError as error:
            st.error(str(error))
    run = state.get("comparison")
    if not run:
        st.caption("Comparison uses the last submitted Strategy Builder settings, with defaults available for a first run.")
        return
    period = st.radio("Comparison period", ["Holdout", "Development"], horizontal=True)
    with st.container(border=True):
        st.subheader(f"{period} account equity")
        plot(comparison_chart(run.results, period), "comparison-equity")
    rows = []
    for label, periods in run.results.items():
        result = periods[period]
        rows.append({"Strategy": label, "Roll rule": result.settings["roll_rule"], **result.metrics})
    table = pd.DataFrame(rows)
    shown = ["Strategy", "Roll rule", "Net P&L", "Sharpe", "Max drawdown", "Win rate", "Trades", "Costs", "Exposure"]
    st.dataframe(
        table[shown], hide_index=True, width="stretch",
        column_config={
            "Net P&L": st.column_config.NumberColumn(format="dollar"),
            "Costs": st.column_config.NumberColumn(format="dollar"),
            "Sharpe": st.column_config.NumberColumn(format="%.2f"),
            **{field: st.column_config.NumberColumn(format="percent") for field in ("Max drawdown", "Win rate", "Exposure")},
        },
    )
    download_frame("Download comparison metrics", table, "comparison-metrics")


def report_page(spec, state):
    hero("Research Report", "Export the simulation methodology, development and holdout results, and supporting ledgers.", f"{spec.code}  /  EXPORT")
    runs = {label: state[key] for label, key in (("Backtest", "run"), ("Strategy comparison", "comparison")) if key in state}
    current, archive = st.tabs(["Current report", "Saved experiments"])
    with current:
        if runs:
            choice = st.selectbox("Report source", list(runs))
            run = runs[choice]
            hypothesis = st.text_area("Custom hypothesis", placeholder="Leave empty to use the strategy hypothesis.", key=f"{choice}-hypothesis")
            notes = st.text_area("Analyst notes", placeholder="Add observations about the results and caveats.", key=f"{choice}-notes")
            report = markdown(run, hypothesis, notes)
            bundle = evidence_bundle(run, report)
            a, b, c = st.columns(3)
            a.download_button("Download Markdown", report, "research-report.md", mime="text/markdown")
            b.download_button("Download evidence ZIP", bundle, "research-evidence.zip", mime="application/zip")
            if c.button("Save experiment to SQLite", type="primary"):
                try:
                    experiment_id = save_experiment(run, report, bundle)
                except (OSError, sqlite3.Error) as error:
                    st.error(f"Could not save the experiment. {error}")
                else:
                    st.success(f"Experiment saved · {experiment_id[:8]}")
            with st.container(border=True):
                st.markdown(report)
        else:
            st.info("Run a backtest or comparison to create a research report.")
    with archive:
        try:
            saved = list_experiments()
            if not saved:
                st.caption("Saved experiments appear here.")
                return
            choice = st.selectbox("Saved experiment", saved, format_func=lambda row: f"{row[1]} UTC · {row[2]} · {row[4]} · {row[0][:8]}")
            st.caption(choice[3])
            stored_report, stored_bundle = load_experiment(choice[0])
            a, b = st.columns(2)
            a.download_button("Download saved Markdown", stored_report, "research-report.md", mime="text/markdown")
            b.download_button("Download saved evidence ZIP", stored_bundle, "research-evidence.zip", mime="application/zip")
            with st.expander("Saved report preview"):
                st.markdown(stored_report)
        except sqlite3.Error as error:
            st.error(f"Could not read saved experiments. {error}")


def main():
    st.html(CSS_PATH)
    context = {}

    def market():
        market_page(context["dataset"], context["spec"], curve_item)

    def builder():
        strategy_page(context["dataset"], context["spec"], context["state"], results_item)

    def results():
        results_page(context["spec"], context["state"], builder_item)

    def compare():
        comparison_page(context["dataset"], context["spec"], context["state"])

    def report():
        report_page(context["spec"], context["state"])

    def curve():
        curve_page(context["dataset"], context["spec"], context["state"])

    def replay():
        replay_page(historical_dataset())

    def advanced():
        advanced_futures_page(context["dataset"], context["spec"], context["state"])

    def live():
        live_page(context["spec"], options_item, context["dataset"])

    def options():
        options_page(context["spec"], live_item)

    builder_item = st.Page(builder, title="Strategy Builder", icon=":material/tune:", url_path="strategy")
    results_item = st.Page(results, title="Backtest Results", icon=":material/monitoring:", url_path="backtest")
    market_item = st.Page(market, title="Market Explorer", icon=":material/candlestick_chart:", default=True)
    curve_item = st.Page(curve, title="Calendar Spread Lab", icon=":material/show_chart:", url_path="curve")
    replay_item = st.Page(replay, title="Expiry Replay", icon=":material/history:", url_path="replay")
    compare_item = st.Page(compare, title="Strategy Comparison", icon=":material/stacked_line_chart:", url_path="comparison")
    report_item = st.Page(report, title="Research Report", icon=":material/description:", url_path="report")
    advanced_item = st.Page(advanced, title="Advanced Futures", icon=":material/query_stats:", url_path="advanced-futures")
    live_item = st.Page(live, title="Live Markets", icon=":material/sensors:", url_path="live")
    options_item = st.Page(options, title="Options Lab", icon=":material/timeline:", url_path="options")
    pages = [market_item, live_item, advanced_item, options_item, curve_item, replay_item,
             builder_item, results_item, compare_item, report_item]
    page = st.navigation(pages, position="hidden")

    with st.sidebar:
        st.html('<div class="brand-lockup"><div class="brand-name">Futures Research Engine</div>'
                '<div class="brand-meta">NYMEX WTI · Analyst workspace</div></div>')
        for item in pages:
            st.page_link(item)
        st.divider()
        product = st.selectbox("Futures product", list(SPECS), format_func=lambda code: f"{code} · {SPECS[code].name}")
        sources = ["EIA settlement archive", "Contract CSV", "Synthetic demo"] if product == "CL" else ["Synthetic demo", "Contract CSV"]
        source = st.selectbox("Market data", sources, key=f"data-source-{product}")
        dataset = None
        if source == "Contract CSV":
            upload = st.file_uploader("Daily contract CSV", type="csv")
            calendar = st.file_uploader("Exchange sessions · optional", type="csv")
            st.caption("The calendar CSV uses one date column with exchange session labels.")
            if upload is None:
                st.info("Upload a CSV to continue.")
            else:
                try:
                    calendar_bytes = calendar.getvalue() if calendar is not None else None
                    dataset = imported_dataset(upload.getvalue(), upload.name, product, calendar_bytes)
                except ValueError as error:
                    st.error(str(error))
        elif source == "EIA settlement archive":
            dataset = historical_dataset()
        else:
            dataset = synthetic_dataset()
        if dataset is not None:
            st.divider()
            st.caption(f"{dataset.frame.date.min():%Y-%m-%d} to {dataset.frame.date.max():%Y-%m-%d}")
    spec = SPECS[product]
    if page.url_path in {replay_item.url_path, live_item.url_path, options_item.url_path}:
        context.update(spec=spec, dataset=dataset)
        page.run()
        return
    if dataset is None:
        st.stop()

    data_line = f"{dataset.label}  ·  {dataset.execution}  ·  {dataset.calendar_label}"
    st.html(f'<div class="data-line">{escape(data_line)}</div>')
    if dataset.synthetic:
        st.info("Synthetic demonstration data. Prices, volume, and simulated returns are generated.")
    elif dataset.settlement_only:
        st.caption("Published daily settlements. Simulated fills use the next settlement as a proxy for execution.")

    key = (product, dataset.fingerprint, dataset.label, dataset.calendar)
    workspaces = st.session_state.setdefault("workspaces", {})
    if key not in workspaces:
        workspaces[key] = {"config": default_config(dataset, spec)}
    context.update(dataset=dataset, spec=spec, state=workspaces[key])
    page.run()


main()
