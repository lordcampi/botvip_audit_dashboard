from __future__ import annotations

import pandas as pd
import streamlit as st


def bar_chart_from_counts(df: pd.DataFrame, label_col: str, value_col: str, title: str) -> None:
    st.subheader(title)
    if df is None or df.empty or label_col not in df.columns or value_col not in df.columns:
        st.info("No hay datos suficientes para este grafico.")
        return
    chart_df = df.set_index(label_col)[value_col]
    st.bar_chart(chart_df)


def dataframe_or_info(df: pd.DataFrame, message: str = "No hay datos disponibles.") -> None:
    if df is None or df.empty:
        st.info(message)
    else:
        st.dataframe(df, width="stretch", hide_index=True)
