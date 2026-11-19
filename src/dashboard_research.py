"""Curve study and historical decision replay pages."""
import pandas as pd
import streamlit as st

from .charts import curve_chart, replay_chart
from .dashboard_ui import download_frame, hero, money, plot, show_table
from .engine import spread_regime
from .replay import JUNE, MAY, SIDE_COST, advance, april_panel, choices, exit_deadline, policy_replay
from .research import BASE, BENCHMARK, HYPOTHESIS, STRESS, study_brief, study_curve, study_evidence


def curve_page(dataset, spec, state):
    hero("Calendar Spread Lab", "Study two WTI maturities, then test spread reversion across independent windows.", f"NYMEX  /  {spec.code}  /  CURVE RESEARCH")
    price = dataset.price_column
    frame = dataset.frame
    prices = frame.pivot(index="date", columns="contract", values=price)
    contracts = frame.groupby("contract").expiry.first().sort_values().index
    pairs = [(near, far) for near, far in zip(contracts[:-1], contracts[1:]) if prices[[near, far]].dropna().shape[0] > 1]
    if not pairs:
        st.info("This study requires two adjacent maturities with overlapping daily quotes.")
        return

    a, b = st.columns([1.6, 1])
    default = (MAY, JUNE) if (MAY, JUNE) in pairs else pairs[-1]
    pair = a.selectbox("Adjacent maturities", pairs, index=pairs.index(default), format_func=lambda pair: " / ".join(pair))
    near, far = pair
    data = prices[list(pair)].dropna()
    spread = data[near].iloc[-1] - data[far].iloc[-1]
    b.metric("Last nearby − deferred", f"{money(spread)} / bbl", spread_regime(spread), delta_color="off")
    with st.container(border=True):
        st.subheader("Contract prices and calendar spread")
        st.caption("Positive spread indicates backwardation. Negative spread indicates contango.")
        plot(curve_chart(data, near, far), "curve-pair")

    config = state["config"]
    strategy = config["strategies"]["Calendar spread"]
    settings = config["settings"]
    robustness, trade_tab, brief_tab = st.tabs(["Walk-forward results", "Trade walkthrough", "Analyst brief"])
    with robustness:
        st.markdown(HYPOTHESIS)
        st.caption(
            f"Uses Strategy Builder settings · Z-score {strategy.window} sessions, entry {strategy.entry:g}, "
            f"exit {strategy.exit:g} · {settings.roll_days} sessions to expiry · {dataset.execution}"
        )
        dates = frame.date
        n_dates = dates.nunique()
        with st.form("curve-study-setup"):
            study_dates = st.date_input("Study date range", config["dates"], min_value=dates.min().date(), max_value=dates.max().date())
            a, b, c = st.columns(3)
            development = a.number_input("Development sessions", min_value=2, max_value=1200, value=min(252, max(2, n_dates // 3)))
            test = b.number_input("Test sessions per window", min_value=2, max_value=756, value=min(252, max(2, n_dates // 5)))
            extra = c.number_input("Stress · extra ticks per side", min_value=0.25, max_value=50.0, value=2.0, step=0.25)
            submitted = st.form_submit_button("Run walk-forward study", type="primary")
        if submitted:
            try:
                if len(study_dates) != 2:
                    raise ValueError("Select both dates in the study range.")
                with st.spinner("Running the base case, benchmark, and higher-slippage tests…"):
                    state["curve_study"] = study_curve(dataset, strategy, settings, development, test, extra, *study_dates)
            except ValueError as error:
                st.error(str(error))
        study = state.get("curve_study")
        if study:
            show_walk_forward(study)
        else:
            st.caption("Each window starts flat. Signal rules are frozen across the study and incomplete final windows are discarded.")

    with trade_tab:
        if study:
            walkthrough(study, spec)
        else:
            st.info("Run the walk-forward study to inspect simulated spread trades.")

    with brief_tab:
        if study:
            notes = st.text_area(
                "Analyst decision",
                placeholder="What would make you reject the hypothesis or stay flat?",
                key="curve-brief-notes",
            )
            brief = study_brief(dataset, study, notes)
            a, b = st.columns(2)
            a.download_button("Download analyst brief", brief, "wti-analyst-brief.md", mime="text/markdown")
            b.download_button(
                "Download study evidence", study_evidence(dataset, study, brief), "wti-curve-study.zip", mime="application/zip",
            )
            with st.container(border=True):
                st.markdown(brief)
        else:
            st.info("The brief becomes available after a walk-forward run.")


def show_walk_forward(study):
    windows = study.windows()
    totals = windows.groupby("Scenario")["Net P&L"].sum()
    a, b, c = st.columns(3)
    a.metric("Spread reversion", money(totals[BASE]))
    b.metric("Always-long spread", money(totals[BENCHMARK]))
    c.metric("Higher slippage", money(totals[STRESS]))
    st.caption("Totals sum test P&L across independent accounts. The benchmark holds long nearby and short deferred.")
    st.subheader("Test windows")
    show_table(windows, "curve-windows", {
        "Net P&L": st.column_config.NumberColumn(format="dollar"),
        "Development P&L": st.column_config.NumberColumn(format="dollar"),
        "Max drawdown": st.column_config.NumberColumn(format="percent"),
        "Trading costs": st.column_config.NumberColumn(format="dollar"),
    })
    st.subheader("Results by curve at signal")
    regimes = study.regimes()
    if regimes.empty:
        st.caption("The base case closed no calendar spreads.")
    else:
        show_table(regimes, "curve-regimes", {
            label: st.column_config.NumberColumn(format="dollar")
            for label in ["Net P&L", "Average per trade", "Costs"]
        })
        st.caption("Curve state is measured on the signal date for each completed spread. Counts and market exposure can differ by state.")
    download_frame("Download window metrics", windows, "walk-forward-results")


def walkthrough(study, spec):
    round_trips = study.ledgers("round_trips").sort_values("net_pnl").reset_index(drop=True)
    if round_trips.empty:
        st.info("There are no completed base-case spreads in the test windows.")
        return

    def display_trade(i):
        trade = round_trips.iloc[i]
        return f"Window {trade.Window} · {trade.contracts} · {trade.entry_date:%Y-%m-%d} · {money(trade.net_pnl)}"

    selected = st.selectbox("Completed spread · lowest P&L first", list(round_trips.index), format_func=display_trade)
    trade = round_trips.iloc[selected]
    legs = study.folds[int(trade.Window) - 1].tests[BASE].trades
    legs = legs.loc[legs.group.eq(trade.group), [
        "contract", "direction", "quantity", "entry_price", "exit_price", "gross_pnl", "costs", "net_pnl",
    ]]
    a, b, c = st.columns(3)
    a.metric("Signal curve", trade.entry_regime)
    b.metric("Signal spread per barrel", money(trade.entry_spread))
    c.metric("Net spread P&L", money(trade.net_pnl))
    st.caption(f"Signal {trade.entry_signal_date:%Y-%m-%d} · Entry {trade.entry_date:%Y-%m-%d} · Exit {trade.exit_date:%Y-%m-%d} · {trade.reason}")
    if legs.iloc[0].quantity > 0:
        st.write("The long spread expected nearby to strengthen relative to deferred. A further drop in nearby relative to deferred hurt the position.")
    else:
        st.write("The short spread expected nearby to weaken relative to deferred. A further rise in nearby relative to deferred hurt the position.")
    show_table(legs, "spread-leg-walkthrough")
    st.caption(f"For each leg, signed contracts × (exit price − entry price) × {spec.multiplier:,} barrels gives gross P&L. Both legs pay costs.")


def replay_page(dataset):
    panel = april_panel(dataset)
    hero("April 2020 Expiry Replay", "Manage a long May WTI contract through the final trading week, one settlement at a time.", "NYMEX  /  CL  /  HISTORICAL REPLAY")
    a, b = st.columns([1, 2])
    buffer = a.selectbox("May contract expiry buffer", [1, 3, 5], index=2, format_func=lambda n: f"{n} session{'s' if n != 1 else ''}")
    deadline = exit_deadline(dataset, buffer)
    b.caption(f"May last trade · 2020-04-21. Your position leaves the May contract by the {deadline:%Y-%m-%d} settlement. Changing the buffer restarts the replay.")
    state = st.session_state.get("expiry_replay")
    if state is None or state["buffer"] != buffer:
        state = {"buffer": buffer, "decisions": []}
        st.session_state["expiry_replay"] = state
    decisions = state["decisions"]
    visible = panel.iloc[:len(decisions) + 1]
    date = visible.index[-1]
    current = decisions[-1][0].contract if decisions else MAY
    net = decisions[-1][0].cumulative_pnl if decisions else 0
    c1, c2, c3 = st.columns(3)
    c1.metric("Revealed session", f"{date:%Y-%m-%d}")
    c2.metric("Position", f"Long 1 {current}" if current else "Flat")
    c3.metric("Cumulative P&L", money(net))
    if st.button("Restart replay", key="replay-reset"):
        decisions.clear()
        st.rerun()
    if len(visible) < len(panel):
        st.caption(f"Start with one May contract already held. Actions execute at the next revealed settlement and cost {money(SIDE_COST)} per contract side.")
        with st.form("expiry-replay-decision", clear_on_submit=True):
            action = st.radio("Decision for the next settlement", choices(current), horizontal=True)
            note = st.text_input("Reason for your decision", placeholder="Optional reasoning recorded before revealing prices")
            commit = st.form_submit_button("Record decision and reveal next session", type="primary")
        if commit:
            outcome = advance(panel, [record[0] for record in decisions], action, deadline)
            decisions.append((outcome, note))
            st.rerun()
    else:
        st.success("Replay complete.")
    with st.container(border=True):
        st.subheader("Revealed May and June settlements")
        plot(replay_chart(visible), "april-expiry-prices")
        last_row = visible.iloc[-1]
        st.caption(" · ".join(f"{contract} {money(value)} / bbl" for contract, value in last_row.dropna().items()))
    if decisions:
        table = pd.DataFrame([{
            "Settlement date": decision.date, "Decision": decision.requested, "Outcome": decision.outcome,
            "Held after decision": decision.contract or "Flat", "Day P&L": decision.day_pnl,
            "Costs": decision.costs, "Cumulative P&L": decision.cumulative_pnl, "Reason": note,
        } for decision, note in decisions])
        st.subheader("Decision journal")
        show_table(table, "expiry-journal")
        download_frame("Download decision journal", table, "april-expiry-decisions")
    if len(visible) == len(panel):
        st.subheader("Compare automatic rolls")
        rows = []
        for days in [1, 3, 5]:
            roll = exit_deadline(dataset, days)
            trace = policy_replay(panel, roll)
            rows.append({"Buffer sessions": days, "May exit": roll, "Ending marked P&L": trace[-1].cumulative_pnl})
        show_table(pd.DataFrame(rows), "expiry-policy-comparison")
        st.caption("The examples roll the long May position into June and mark the June position at the final revealed settlement.")
    st.markdown("[CFTC staff report on the April 2020 episode](https://www.cftc.gov/media/5296/InterimStaffReportNYMEX_WTICrudeOil/download)")
