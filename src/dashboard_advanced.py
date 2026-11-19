"""Walk-forward evaluation of directional futures signals."""
import plotly.graph_objects as go
import streamlit as st

from .advanced_research import BASELINE, STRESS, compare_futures, futures_evidence
from .charts import price_trace, style
from .dashboard_ui import hero, money, plot, show_table
from .strategies import ADVANCED_STRATEGY_NAMES, Strategy

STRATEGY_TEXT = {
    "Donchian breakout": "Enter beyond the previous price channel and a trailing volatility buffer. Use a shorter opposite channel for exits.",
    "Curve carry": "Go long the nearby future when it is sufficiently above the deferred contract, or short when it is sufficiently below.",
}


def advanced_futures_page(dataset, spec, state):
    hero("Advanced Futures", "Compare breakout and curve-carry rules against the same benchmark and execution assumptions.",
         f"NYMEX  /  {spec.code}  /  STRATEGY RESEARCH")
    for label, description in STRATEGY_TEXT.items():
        st.markdown(f"**{label}** — {description}")
    if dataset.settlement_only:
        st.caption("Settlement inputs use a price-only channel and absolute daily settlement changes as the volatility proxy.")

    config = state["config"]
    settings = config["settings"]
    st.caption(f"{dataset.execution} · {money(settings.capital)} initial equity per window · {settings.roll_rule} · "
               f"{settings.slippage_ticks:g} base ticks per side. Change common settings in Strategy Builder.")
    dates = dataset.frame.date
    n = dates.nunique()
    with st.form("advanced-futures-settings"):
        period = st.date_input("Study dates", config["dates"], min_value=dates.min().date(), max_value=dates.max().date())
        a, b = st.columns(2)
        development = a.number_input("Development sessions", 2, 1500, min(252, max(2, n // 3)))
        test = b.number_input("Test sessions per window", 2, 1000, min(252, max(2, n // 5)))
        with st.expander("Signal parameters"):
            a, b, c = st.columns(3)
            entry = a.number_input("Breakout lookback", 2, 252, 20)
            exit_ = b.number_input("Exit lookback", 2, 251, 10)
            vol = c.number_input("Volatility lookback", 2, 252, 14)
            a, b = st.columns(2)
            buffer = a.number_input("Breakout volatility buffer", 0.0, 5.0, 0.25, step=0.05)
            threshold = b.number_input("Carry threshold · USD/bbl per 30 days", 0.0, 50.0, 0.25, step=0.05)
        a, b = st.columns(2)
        stress = a.selectbox("Strategy to stress", ADVANCED_STRATEGY_NAMES)
        extra = b.number_input("Stress · extra ticks per side", 0.25, 50.0, 2.0, step=0.25)
        run = st.form_submit_button("Run futures comparison", type="primary")
    if run:
        strategies = {
            "Donchian breakout": Strategy("Donchian breakout", breakout_window=entry, channel_exit_window=exit_,
                                          breakout_vol_window=vol, breakout_filter=buffer),
            "Curve carry": Strategy("Curve carry", carry_threshold=threshold),
        }
        try:
            if len(period) != 2:
                raise ValueError("Select both dates in the study range.")
            with st.spinner("Running matched out-of-sample simulations…"):
                state["advanced_study"] = compare_futures(
                    dataset, strategies, config["strategies"]["Momentum"], settings, *period,
                    development, test, extra, stress,
                )
        except ValueError as error:
            st.error(str(error))
    research = state.get("advanced_study")
    if research is None:
        return
    rows = research.comparison()
    totals = rows.groupby("Scenario", sort=False)["Net P&L"].sum()
    for column, (label, value) in zip(st.columns(len(totals)), totals.items()):
        column.metric(label, money(value))
    baseline = totals[BASELINE]
    rows["Vs. benchmark"] = rows["Net P&L"] - rows["Window"].map(rows.loc[rows.Scenario.eq(BASELINE)].set_index("Window")["Net P&L"])
    candidates = totals.loc[list(ADVANCED_STRATEGY_NAMES)]
    lead = candidates.idxmax()
    delta = candidates[lead] - baseline
    st.caption(f"{lead} led the advanced candidates in this sample by net P&L. Its difference from the baseline was {money(delta)}.")
    st.caption("The indicators warm up before each test. Test accounts start flat with fresh capital, and displayed P&L sums the independent tests.")
    chart = go.Figure()
    for i, (name, pnl) in enumerate(research.paths().items()):
        chart.add_trace(price_trace(pnl.index, pnl, name if name != STRESS else f"{STRESS} · {research.scenarios[STRESS][0].name}", i))
    chart.update_yaxes(tickprefix="$", zeroline=True)
    with st.container(border=True):
        st.subheader("Out-of-sample P&L across windows")
        plot(style(chart, 380), "advanced-futures-pnl")
    st.subheader("Window results")
    formats = {
        label: st.column_config.NumberColumn(format="dollar")
        for label in ["Net P&L", "Vs. benchmark", "Trading costs"]
    }
    formats.update({
        label: st.column_config.NumberColumn(format="percent") for label in ["Max drawdown", "Exposure"]
    })
    show_table(rows, "advanced-futures-windows", formats)
    st.download_button("Download futures research ZIP", futures_evidence(dataset, research), "advanced-futures.zip", "application/zip")
