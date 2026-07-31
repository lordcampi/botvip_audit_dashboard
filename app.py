from __future__ import annotations

"""
Swing Strategy Review Center — Streamlit MVP (R2).
SWING-only landing page. Observational review only.

Does NOT connect to PostgreSQL. Does NOT use SQLite.
All analytics live in pages/1_Swing_Strategy_Review.py.
"""

import streamlit as st

st.set_page_config(
    page_title="Swing Strategy Review Center",
    page_icon="🔍",
    layout="wide",
)

st.title("🔍 Swing Strategy Review Center")
st.caption("Observational strategy review — no automatic changes")

st.divider()

st.markdown("## Strategy")
st.markdown("**SWING_TREND_RECLAIM_V1** — **SHORT only**")

st.markdown("## Data source")
st.markdown("PostgreSQL read-only (`botvip_readonly`) — no writes, no schema changes, no fallback to SQLite")

st.markdown("## Current mode")
st.markdown("**Observational** — review, audit and analysis only")

st.markdown("## CONTROL")
st.markdown("Protected / no automatic modifications — strategy is never altered by this dashboard")

st.markdown("## Real trading")
st.markdown("**OFF** — this review center does not operate the bot, submit orders, or change live signals")

st.markdown("## Dashboard access")
st.markdown("Private through SSH tunnel — not exposed on public internet")

st.markdown("## Current stage")
st.markdown("R2 Streamlit MVP — Swing Strategy Review Center deployed on Vultr")

st.divider()

st.success(
    "This dashboard analyzes official SWING signals, executability, "
    "Demo compatibility, and shadow experiments. "
    "It does **not** modify the bot or the strategy."
)

st.info(
    "Use the sidebar or navigate to **Swing Strategy Review** "
    "to explore signal KPIs, lifecycle status, official results, "
    "fingerprint selection, and the SHORT-only shadow multi-pair experiment."
)

st.divider()

st.caption(
    "⚠️ This dashboard does NOT modify CONTROL. "
    "All data is read-only from PostgreSQL. "
    "Trading is OFF. "
    "Scanner diagnostics are stale, low-confidence, and non-official."
)