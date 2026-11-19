"""Shared Streamlit presentation components."""
from html import escape

import pandas as pd
import streamlit as st

from .reports import format_metric


def hero(title, description, context):
    st.html(f"""
      <div class="workspace-banner">
        <div class="market-context">{escape(context)}</div>
        <h1>{escape(title)}</h1>
        <p>{escape(description)}</p>
      </div>
    """)


def money(value):
    return format_metric("Net P&L", value)


def show_metrics(names, result):
    for column, name in zip(st.columns(len(names)), names):
        column.metric(name, format_metric(name, result.metrics[name]))


def plot(figure, key):
    st.plotly_chart(figure, theme=None, width="stretch", key=key, config={"displaylogo": False, "scrollZoom": False})


def show_table(frame, key, formats=None):
    config = dict(formats or {})
    for col in frame:
        if col in config:
            continue
        if pd.api.types.is_datetime64_any_dtype(frame[col]):
            config[col] = st.column_config.DateColumn(format="YYYY-MM-DD")
        elif pd.api.types.is_float_dtype(frame[col]):
            config[col] = st.column_config.NumberColumn(format="%.2f")
    st.dataframe(frame, hide_index=True, width="stretch", column_config=config, key=key)


def download_frame(label, frame, key):
    st.download_button(label, frame.to_csv(index=False), f"{key}.csv", mime="text/csv", key=key)
