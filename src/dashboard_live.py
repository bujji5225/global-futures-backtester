"""Live quote monitoring and WTI options payoff analysis."""
from datetime import timezone
import os

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from .charts import price_trace, style
from .dashboard_ui import hero, money, plot, show_table
from .live_market import LiveMarket
from .options_strategies import BEAR_PUT, BULL_CALL, IRON_CONDOR, LONG_STRADDLE, OPTION_STRATEGY_NAMES, build_strategy
from .quote_research import (
    csv_chain, grouped_chain, live_breakout_reference, live_carry, live_option_quotes,
    match_quote_times, sample_chain,
)

QUOTE_STALE_SECONDS = 45
DISPLAY = {
    "setup_required": "Setup required", "disconnected": "Disconnected", "connecting": "Connecting",
    "live": "Receiving live data", "stale": "Waiting for fresh data", "stopping": "Stopping", "error": "Connection issue",
}
LEG_LABELS = {
    BULL_CALL: (("Buy lower call", "C"), ("Sell higher call", "C")),
    BEAR_PUT: (("Sell lower put", "P"), ("Buy higher put", "P")),
    IRON_CONDOR: (("Buy lower put", "P"), ("Sell inner put", "P"), ("Sell inner call", "C"), ("Buy higher call", "C")),
    LONG_STRADDLE: (("Buy call", "C"), ("Buy put at same strike", "P")),
}


def display_time(value):
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC") if value is not None else "—"


@st.cache_resource(show_spinner=False)
def market_for(product):
    return LiveMarket(os.environ.get("DATABENTO_API_KEY"), product=product)


def connection_controls(market, snapshot, product):
    if snapshot.status == "setup_required":
        st.info(snapshot.message)
        st.markdown(
            "The live feed requires a Databento API key and CME live-data access. "
            "The local sample and historical studies are available without a subscription."
        )
        st.code("python -m pip install -r requirements-live.txt", language="bash")
        st.markdown("[Databento CME plans](https://databento.com/pricing/) · [CME delayed quotes](https://www.cmegroup.com/market-data/browse-data/delayed-quotes.html)")
        return
    a, b = st.columns([1, 3])
    active = snapshot.status in {"connecting", "live", "stale", "stopping"}
    if active:
        if a.button("Stop feed", key=f"stop-feed-{product}", disabled=snapshot.status == "stopping"):
            market.stop()
            st.rerun(scope="fragment")
    elif a.button("Connect feed", key=f"start-feed-{product}", type="primary"):
        market.start()
        st.rerun(scope="fragment")
    b.caption(f"{DISPLAY.get(snapshot.status, snapshot.status)} · {snapshot.message}")


def live_page(spec, options_item, dataset):
    hero("Live Markets", "Monitor dated WTI futures and options quotes with source and timestamp checks.",
         f"NYMEX  /  {spec.code}  /  DATABENTO")
    st.page_link(options_item, label="Open Options Lab", icon=":material/arrow_forward:")
    market_panel(spec.code, dataset)


@st.fragment(run_every="5s")
def market_panel(product, dataset):
    market = market_for(product)
    snapshot = market.snapshot(stale_after=QUOTE_STALE_SECONDS)
    connection_controls(market, snapshot, product)
    if snapshot.status == "setup_required":
        return
    a, b, c = st.columns(3)
    a.metric("Fresh futures", sum(row.quote_status == "fresh" for row in snapshot.futures))
    b.metric("Fresh options", sum(row.quote_status == "fresh" for row in snapshot.options))
    c.metric("Feed status", DISPLAY.get(snapshot.status, snapshot.status))
    st.caption(f"{snapshot.source} · Latest quote {display_time(snapshot.as_of)} · Display refresh every 5 seconds.")
    if not snapshot.futures:
        st.caption("Futures appear when the feed receives dated contracts and their quotes.")
        return
    futures = [row for row in snapshot.futures if row.expiry is not None]
    st.subheader("Dated futures order books")
    show_table(pd.DataFrame([{
        "Contract": row.symbol, "Expiry": row.expiry.date().isoformat(), "Bid": row.bid,
        "Ask": row.ask, "Bid size": row.bid_size, "Ask size": row.ask_size,
        "Quote UTC": display_time(row.timestamp), "Status": row.quote_status.replace("_", " "),
    } for row in futures]), "live-futures-books")
    fresh = [row for row in futures if row.quote_status == "fresh"]
    carry_panel(fresh if snapshot.status == "live" else [])
    if snapshot.status == "live" and fresh:
        with st.expander("Live Donchian breakout check"):
            by_symbol = {row.symbol: row for row in fresh}
            selection = st.selectbox("Dated futures contract", list(by_symbol), key="live-breakout-contract")
            try:
                top, bottom, signal, through = live_breakout_reference(by_symbol[selection], dataset)
            except ValueError as error:
                st.caption(str(error))
            else:
                a, b, c = st.columns(3)
                a.metric("Upper trigger", money(top))
                b.metric("Lower trigger", money(bottom))
                c.metric("Live midpoint", signal)
                st.caption(f"20-session entry channel through {through:%Y-%m-%d}. This intraday check is provisional until the session completes.")


def carry_panel(fresh):
    st.subheader("Live curve carry")
    if len(fresh) < 2:
        st.caption("Carry needs two fresh futures quotes with different expiries.")
        return
    fresh.sort(key=lambda row: (row.expiry, row.symbol))
    by_symbol = {row.symbol: row for row in fresh}
    near_options = [row.symbol for row in fresh if row.expiry < fresh[-1].expiry]
    if not near_options:
        st.caption("Carry needs two different futures expiries.")
        return
    a, b, c = st.columns(3)
    near = by_symbol[a.selectbox("Nearby futures", near_options)]
    far_options = [row.symbol for row in fresh if row.expiry > near.expiry]
    far = by_symbol[b.selectbox("Deferred futures", far_options)]
    threshold = c.number_input("Threshold · USD/bbl per 30 days", min_value=0.0, value=0.25, step=0.05)
    try:
        carry, lean, as_of = live_carry(near, far, threshold)
    except ValueError as error:
        st.warning(str(error))
    else:
        a, b, c = st.columns(3)
        a.metric("Normalized curve slope", money(carry))
        b.metric("Research signal", lean)
        c.metric("Nearby midpoint", money(near.mid))
        st.caption(f"Quotes through {display_time(as_of)}. Signal uses book midpoints and is a live observation of the configured carry rule.")


def options_page(spec, live_item):
    hero("WTI Options Lab", "Build same-expiry positions on WTI futures and inspect dollar payoff across expiry scenarios.",
         f"NYMEX  /  {'LO' if spec.code == 'CL' else 'MCO'}  /  OPTIONS RESEARCH")
    st.page_link(live_item, label="Open Live Markets", icon=":material/arrow_forward:")
    mode = st.selectbox("Option prices", ["Sample chain", "Live feed", "Upload CSV snapshot"])
    if mode == "Live feed":
        live_options_panel(spec)
        return
    if mode == "Sample chain":
        options_panel(spec, sample_chain(spec.code), "Illustrative sample premiums", 70.0)
        return
    upload = st.file_uploader("Options quote snapshot", type="csv")
    st.caption("CSV fields: symbol, underlying, expiry, right, strike, bid, ask. Premiums and strikes are USD per barrel.")
    if upload is None:
        return
    try:
        quotes = csv_chain(upload.getvalue(), spec.code)
    except ValueError as error:
        st.error(str(error))
        return
    options_panel(spec, quotes, f"Static upload · {upload.name}", None)


@st.fragment(run_every="5s")
def live_options_panel(spec):
    market = market_for(spec.code)
    snapshot = market.snapshot(stale_after=QUOTE_STALE_SECONDS)
    connection_controls(market, snapshot, spec.code)
    quotes, originals = live_option_quotes(snapshot)
    if not quotes:
        if snapshot.status == "live":
            st.caption("Waiting for fresh, matched option quotes with complete contract definitions.")
        return
    mids = {row.symbol: row.mid for row in snapshot.futures if row.quote_status == "fresh" and row.mid is not None}
    options_panel(spec, quotes.values(), snapshot.source, None, originals, mids)


def defaults_for(choices, center, strategy):
    calls = sorted((q for q in choices.values() if q.right == "C"), key=lambda q: q.strike)
    puts = sorted((q for q in choices.values() if q.right == "P"), key=lambda q: q.strike)
    if strategy == LONG_STRADDLE:
        call_at = {q.strike: q for q in calls}
        put_at = {q.strike: q for q in puts}
        common = call_at.keys() & put_at.keys()
        if common:
            selected = min(common, key=lambda strike: (abs(strike - center), strike))
            return call_at[selected], put_at[selected]
    if strategy == BULL_CALL:
        quoted = [q for q in calls if q.bid > 0]
        pairs = [(a, b) for a, b in zip(quoted, quoted[1:]) if a.strike < b.strike]
        if pairs:
            return min(pairs, key=lambda pair: abs(pair[0].strike - center))
    if strategy == BEAR_PUT:
        quoted = [q for q in puts if q.bid > 0]
        pairs = [(a, b) for a, b in zip(quoted, quoted[1:]) if a.strike < b.strike]
        if pairs:
            return min(pairs, key=lambda pair: abs(pair[1].strike - center))
    if strategy == IRON_CONDOR:
        below = [q for q in puts if q.strike < center]
        above = [q for q in calls if q.strike > center]
        if len(below) >= 2 and len(above) >= 2 and below[-1].bid > 0 and above[0].bid > 0:
            return below[-2], below[-1], above[0], above[1]
    return (None,) * len(LEG_LABELS[strategy])


def options_panel(spec, quote_list, source, center, live_rows=None, mids=None):
    groups = grouped_chain(quote_list)
    group = st.selectbox("Underlying and option expiry", list(groups),
                         format_func=lambda item: f"{item[0]} · {item[1]:%Y-%m-%d}")
    quotes = {quote.symbol: quote for quote in groups[group]}
    with st.expander("Option chain quotes"):
        show_table(pd.DataFrame([{
            "Symbol": q.symbol, "Right": q.right, "Strike": q.strike, "Bid": q.bid, "Ask": q.ask,
            **({"Quote UTC": display_time(live_rows[q.symbol].timestamp)} if live_rows is not None else {}),
        } for q in sorted(quotes.values(), key=lambda quote: (quote.strike, quote.right))]), "options-chain")
    midpoint = (mids or {}).get(group[0], center)
    raw_strikes = sorted({q.strike for q in quotes.values()})
    reference = midpoint if midpoint is not None else raw_strikes[len(raw_strikes) // 2]
    a, b, c = st.columns([1.5, 1, 1])
    name = a.selectbox("Options strategy", OPTION_STRATEGY_NAMES)
    contracts = b.number_input("Contracts per leg", min_value=1, max_value=500, value=1)
    commission = c.number_input("Opening fee · USD per contract", min_value=0.0, max_value=100.0, value=2.5, step=0.25)
    choose = defaults_for(quotes, reference, name)
    selected = []
    columns = st.columns(2)
    for i, (label, side) in enumerate(LEG_LABELS[name]):
        candidates = sorted((q for q in quotes.values() if q.right == side), key=lambda quote: (quote.strike, quote.symbol))
        if not candidates:
            st.warning(f"The selected series has no {'call' if side == 'C' else 'put'} quotes.")
            return
        symbols = [quote.symbol for quote in candidates]
        default = choose[i]
        select = columns[i % 2].selectbox(
            label, symbols, index=symbols.index(default.symbol) if default else min(i, len(symbols) - 1),
            key=f"option-leg-{source}-{name}-{group}-{i}",
            format_func=lambda symbol: f"{quotes[symbol].strike:g} {quotes[symbol].right} · {symbol}",
        )
        selected.append(quotes[select])
    try:
        as_of = match_quote_times([live_rows[q.symbol] for q in selected]) if live_rows is not None else None
        profile = build_strategy(name, selected, contracts=int(contracts), multiplier=spec.multiplier, commission=commission)
        if live_rows is not None:
            for leg in profile.legs:
                row = live_rows[leg.quote.symbol]
                depth = row.ask_size if leg.quantity > 0 else row.bid_size
                if abs(leg.quantity) > depth:
                    raise ValueError(f"{leg.quote.symbol} shows only {depth} contracts at the selected quote. Reduce contracts per leg.")
    except ValueError as error:
        st.warning(str(error))
        return
    st.caption(f"{source} · {display_time(as_of) if as_of else 'Static snapshot'} · Buys at ask, sells at bid.")
    a, b, c = st.columns(3)
    a.metric("Net entry cash flow", money(profile.net_entry_cashflow))
    b.metric("Maximum expiry loss", money(profile.max_loss))
    c.metric("Maximum expiry P&L", "Unbounded" if profile.max_profit == float("inf") else money(profile.max_profit))
    levels = ", ".join(money(price) for price in profile.breakevens)
    st.caption(f"Expiry break-even per barrel · {levels or 'None at these prices'}")
    with st.container(border=True):
        st.subheader("P&L at option expiry")
        curve = profile.curve(reference)
        chart = go.Figure(price_trace(curve.futures_price, curve.pnl, "Expiry P&L"))
        chart.update_xaxes(title_text="Underlying futures price at expiry · USD/bbl", tickprefix="$")
        chart.update_yaxes(title_text="Position P&L · USD", tickprefix="$", zeroline=True)
        plot(style(chart, 395), "options-expiry-payoff")
        st.caption("Expiry value includes opening fees. Early exercise, assignment into futures, and post-expiry exposure are outside this model.")
    chosen_price = st.number_input("Scenario futures price · USD/bbl", value=float(reference), step=1.0, format="%.2f")
    st.metric("Expiry P&L at scenario price", money(profile.payoff(chosen_price)))
    st.subheader("Legs and quoted premiums")
    show_table(pd.DataFrame([{
        "Option": leg.quote.symbol, "Side": "Buy" if leg.quantity > 0 else "Sell",
        "Contracts": abs(leg.quantity), "Strike": leg.quote.strike, "Bid": leg.quote.bid,
        "Ask": leg.quote.ask, "Entry price": leg.premium,
    } for leg in profile.legs]), "options-strategy-legs")
