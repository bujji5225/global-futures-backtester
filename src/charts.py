"""Monochrome Plotly chart styles with distinct line types."""
import plotly.graph_objects as go
from plotly.subplots import make_subplots

INK = "#171717"
MUTED = "#616161"
STYLES = ((INK, "solid"), (MUTED, "dash"), ("#444444", "dot"), ("#888888", "dashdot"))


def style(figure, height=365):
    figure.update_layout(
        height=height, margin={"l": 22, "r": 26, "t": 40, "b": 24},
        template="plotly_white", paper_bgcolor="#ffffff", plot_bgcolor="#ffffff",
        font={"family": "Inter, Helvetica Neue, Arial, sans-serif", "color": "#404040", "size": 12},
        hovermode="x unified",
        legend={"orientation": "h", "y": 1.02, "yanchor": "bottom", "x": 0},
        hoverlabel={"bgcolor": "#181818", "font_color": "#ffffff"},
    )
    figure.update_xaxes(showgrid=False, zeroline=False)
    figure.update_yaxes(gridcolor="#e5e5e3", zerolinecolor="#bebebc", automargin=True)
    return figure


def price_trace(x, y, label, index=0):
    color, dash = STYLES[index % len(STYLES)]
    return go.Scatter(
        x=x, y=y, name=label, mode="lines", line={"color": color, "dash": dash, "width": 2.2},
        hovertemplate="$%{y:,.2f}<extra>%{fullData.name}</extra>",
    )


def market_chart(frame, indicators, candles):
    has_volume = "volume" in frame
    chart = make_subplots(
        rows=2, cols=1, shared_xaxes=True, row_heights=[0.77, 0.23], vertical_spacing=0.1,
    ) if has_volume else make_subplots(rows=1, cols=1)
    if candles:
        chart.add_trace(go.Candlestick(
            x=frame.date, open=frame.open, high=frame.high, low=frame.low, close=frame.close,
            increasing_line_color=INK, increasing_fillcolor="#ffffff",
            decreasing_line_color=INK, decreasing_fillcolor=INK, name="OHLC",
        ), row=1, col=1)
    else:
        label, column = ("NYMEX settlement", "settlement") if "settlement" in frame else ("Close", "close")
        chart.add_trace(price_trace(frame.date, frame[column], label), row=1, col=1)
    for i, window in enumerate(indicators, 1):
        chart.add_trace(price_trace(frame.date, frame[f"sma_{window}"], f"SMA {window}", i), row=1, col=1)
    if has_volume:
        chart.add_trace(go.Bar(
            x=frame.date, y=frame.volume, name="Volume", marker_color="#9d9d9d", showlegend=False,
            hovertemplate="%{y:,.0f} contracts<extra>Volume</extra>",
        ), row=2, col=1)
        chart.update_yaxes(title_text="Volume", row=2, col=1)
    chart.update_xaxes(rangeslider_visible=False)
    chart.update_yaxes(tickprefix="$", row=1, col=1)
    return style(chart, 455 if has_volume else 375)


def series_chart(index, values, label, color=INK, prefix="$", percent=False, kind="line"):
    chart = go.Figure()
    hover = "%{y:.1%}" if percent else f"{prefix}%{{y:,.2f}}"
    template = f"{hover}<extra>{label}</extra>"
    if kind == "bar":
        chart.add_trace(go.Bar(
            x=index, y=values, name=label, marker_color=[INK if v >= 0 else "#b3b3b3" for v in values],
            marker_line_width=0.6, marker_line_color="#494949", hovertemplate=template,
        ))
    else:
        kwargs = {"fill": "tozeroy", "fillcolor": "rgba(55,55,55,.13)"} if kind == "area" else {}
        chart.add_trace(go.Scatter(
            x=index, y=values, mode="lines", name=label, line={"color": color, "width": 2.2},
            hovertemplate=template, **kwargs,
        ))
    chart.update_yaxes(tickprefix="" if percent else prefix, tickformat=".1%" if percent else None)
    return style(chart, 305)


def comparison_chart(results, period):
    chart = go.Figure()
    for i, (label, split_results) in enumerate(results.items()):
        daily = split_results[period].daily
        chart.add_trace(price_trace(daily.index, daily.equity, label, i))
    chart.update_yaxes(tickprefix="$")
    return style(chart, 410)


def curve_chart(frame, near, far):
    figure = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.63, 0.37], vertical_spacing=0.12)
    figure.add_trace(price_trace(frame.index, frame[near], f"{near} nearby"), row=1, col=1)
    figure.add_trace(price_trace(frame.index, frame[far], f"{far} deferred", 1), row=1, col=1)
    figure.add_trace(price_trace(frame.index, frame[near] - frame[far], "Nearby − deferred"), row=2, col=1)
    figure.update_yaxes(tickprefix="$")
    figure.update_yaxes(zeroline=True, row=2, col=1)
    return style(figure, 480)


def replay_chart(frame):
    chart = go.Figure()
    for i, contract in enumerate(frame.columns):
        chart.add_trace(price_trace(frame.index, frame[contract], contract, i))
    chart.update_yaxes(tickprefix="$", zeroline=True)
    return style(chart, 350)
