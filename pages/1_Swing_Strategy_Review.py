from __future__ import annotations

"""
Swing Strategy Review Center — Streamlit MVP (R2).

Reads exclusively from PostgreSQL via the R1 read-only layer.
Does NOT touch SQLite, BotVIP, or legacy reporter.
"""

from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from src.swing_dashboard_service import (
    build_swing_dashboard,
    window_days,
    custom_window,
    assess_data_quality,
    _compute_signal_kpis,
    _build_executability,
    _build_signal_table,
    filter_signals_by_fingerprint,
    _determine_latest_fingerprint,
    _fingerprint_segmentation,
)

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Swing Strategy Review Center",
    page_icon="🔍",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
@st.cache_data(ttl=60, show_spinner="Loading data from PostgreSQL read-only...")
def _load_dashboard(start_str: str, end_str: str):
    """Cached wrapper around build_swing_dashboard."""
    start = datetime.fromisoformat(start_str)
    end = datetime.fromisoformat(end_str)
    return build_swing_dashboard(start, end)


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
st.title("🔍 Swing Strategy Review Center")
st.caption("Observational review only — no automatic strategy changes")

# ---------------------------------------------------------------------------
# Sidebar: window selector
# ---------------------------------------------------------------------------
st.sidebar.header("⏱️ Window")

window_option = st.sidebar.selectbox(
    "Period",
    ["Last 3 days", "Last 7 days", "Last 14 days", "Last 30 days", "Custom"],
    index=1,
)

if window_option == "Custom":
    col1, col2 = st.sidebar.columns(2)
    with col1:
        start_date = st.date_input("Start (CO)", value=datetime.utcnow() - timedelta(days=7))
    with col2:
        end_date = st.date_input("End (CO)", value=datetime.utcnow())
    start_dt = datetime.combine(start_date, datetime.min.time())
    end_dt = datetime.combine(end_date, datetime.min.time())
else:
    days = {"Last 3 days": 3, "Last 7 days": 7, "Last 14 days": 14, "Last 30 days": 30}[window_option]
    start_dt, end_dt = window_days(days)

st.sidebar.caption(
    f"🇨🇴 {start_dt.strftime('%Y-%m-%d %H:%M')} → {end_dt.strftime('%Y-%m-%d %H:%M')} Colombia\n\n"
    f"🌐 {(start_dt - timedelta(hours=-5)).strftime('%Y-%m-%d %H:%M')} → "
    f"{(end_dt - timedelta(hours=-5)).strftime('%Y-%m-%d %H:%M')} UTC"
)

if st.sidebar.button("🔄 Refresh data", use_container_width=True):
    st.cache_data.clear()

st.sidebar.divider()
st.sidebar.markdown(
    "**Scope:** SWING_TREND_RECLAIM_V1 official signals only  \n"
    "**Source:** PostgreSQL read-only  \n"
    "**Role:** botvip_readonly  \n"
    "**Trading:** OFF (observational)"
)

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
data = _load_dashboard(start_dt.isoformat(), end_dt.isoformat())

# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------
if data.get("error"):
    st.error(f"⚠️ {data['error']}")
    st.stop()

# ---------------------------------------------------------------------------
# Save window-level fingerprint metadata before it gets overwritten
# ---------------------------------------------------------------------------
data["_window_fingerprint_metadata"] = data.get("fingerprint_segmentation", {})

# ---------------------------------------------------------------------------
# Fingerprint selector (after data load, below sidebar scope info)
# ---------------------------------------------------------------------------
st.sidebar.divider()
st.sidebar.header("🔧 Configuration")

signals_raw = data.get("_signals_df")
fp_seg = data["_window_fingerprint_metadata"]

# Build fingerprint options
fp_options = ["Latest fingerprint only"]
if fp_seg.get("num_distinct", 0) > 1:
    fp_options.append("All fingerprints — MIXED CONFIG")

sel_fp_mode = st.sidebar.selectbox(
    "Configuration fingerprint",
    fp_options,
    index=0,
)

# Determine selected fingerprint
latest_fp = None
selected_fp = None
if signals_raw is not None and not signals_raw.empty:
    latest_fp = _determine_latest_fingerprint(signals_raw)

if sel_fp_mode == "Latest fingerprint only" and latest_fp:
    selected_fp = latest_fp
elif sel_fp_mode == "All fingerprints — MIXED CONFIG":
    selected_fp = "ALL"

# Apply fingerprint filter
if signals_raw is not None and not signals_raw.empty and selected_fp and selected_fp != "ALL":
    fp_signals, fp_incl, fp_excl = filter_signals_by_fingerprint(signals_raw, selected_fp)
    if fp_signals is not None and not fp_signals.empty:
        # Recompute KPIs and table for selected fingerprint
        data["total_signals"] = fp_incl
        data["signal_kpis"] = _compute_signal_kpis(fp_signals)
        data["signal_table"] = _build_signal_table(fp_signals)
        data["executability"] = _build_executability(fp_signals)
        data["fingerprint_segmentation"] = _fingerprint_segmentation(fp_signals, selected_fp)
        data["data_quality"] = assess_data_quality(fp_signals, selected_fp)
        data["excluded_by_fingerprint"] = fp_excl
    if latest_fp:
        st.sidebar.caption(f"Selected: `{latest_fp[:12]}…`")
        st.sidebar.metric("Signals included", fp_incl if fp_incl else data.get("total_signals", 0))
        if fp_excl:
            st.sidebar.metric("Excluded by fingerprint", fp_excl)
elif selected_fp == "ALL":
    st.sidebar.warning("⚠️ MIXED CONFIG — metrics are not directly comparable")
    data["total_signals"] = len(signals_raw) if signals_raw is not None else 0
    data["signal_kpis"] = _compute_signal_kpis(signals_raw)
    data["signal_table"] = _build_signal_table(signals_raw)
    data["executability"] = _build_executability(signals_raw)
    data["fingerprint_segmentation"] = _fingerprint_segmentation(signals_raw, None)
    data["data_quality"] = assess_data_quality(signals_raw, None)

# ---------------------------------------------------------------------------
# Metadata bar
# ---------------------------------------------------------------------------
col_fp, col_loaded, col_win, col_excl = st.columns(4)
with col_fp:
    fp = data.get("fingerprint")
    st.metric("Fingerprint", fp if fp else "N/A")
with col_loaded:
    t = data.get("loaded_at")
    st.metric("Loaded at", t.strftime("%Y-%m-%d %H:%M:%S UTC") if t else "N/A")
with col_win:
    st.metric("Signals in window", data.get("total_signals", 0))
with col_excl:
    excl = data.get("excluded_non_swing", 0)
    st.metric("Rows excluded (non-SWING)", excl if excl else "N/A")

# Fingerprint segmentation warning
fp_seg = data.get("fingerprint_segmentation", {})
if fp_seg.get("warning"):
    st.warning(fp_seg["warning"])
if fp_seg.get("num_distinct", 0) > 1:
    st.caption(f"Distinct fingerprints: {fp_seg['num_distinct']} — signals per fingerprint:")
    for fp_hash, count in sorted(fp_seg.get("fingerprints", {}).items(), key=lambda x: -x[1]):
        st.caption(f"  • `{fp_hash[:12]}…` → {count} signals")

quality = data.get("data_quality", {})
ql = quality.get("level", "UNKNOWN")
ql_color = {"GOOD": "green", "PARTIAL": "orange", "INSUFFICIENT": "red", "INVALID": "red"}.get(ql, "grey")
st.markdown(f"**Data quality:** :{ql_color}[{ql}]")
if quality.get("reasons"):
    for r in quality["reasons"]:
        st.caption(f"• {r}")

st.divider()

# ---------------------------------------------------------------------------
# KPI Cards
# ---------------------------------------------------------------------------
kpis = data.get("signal_kpis", {})
if kpis.get("available"):
    st.subheader("📊 Signal KPIs")

    st.caption("Lifecycle status (dimension A) and official result (dimension B)")

    c1, c2, c3, c4, c5, c6, c7 = st.columns(7)
    with c1:
        st.metric("Total", kpis.get("total", 0))
    with c2:
        st.metric("Pending", kpis.get("lifecycle_pending", 0))
    with c3:
        st.metric("Activated", kpis.get("lifecycle_activated", 0))
    with c4:
        st.metric("Closed", kpis.get("lifecycle_closed", 0))
    with c5:
        st.metric("Cancelled", kpis.get("lifecycle_cancelled", 0))
    with c6:
        st.metric("Expired", kpis.get("lifecycle_expired", 0))
    with c7:
        st.metric("Other", kpis.get("lifecycle_other", 0))

    st.markdown("**Official Result**")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("Win", kpis.get("result_win", 0))
    with c2:
        st.metric("Loss", kpis.get("result_loss", 0))
    with c3:
        st.metric("BE", kpis.get("result_be", 0))
    with c4:
        st.metric("Unknown", kpis.get("result_unknown", 0))

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        pf = kpis.get("profit_factor")
        st.metric("Profit Factor", str(pf) if pf is not None else "N/A")
        pw = kpis.get("pf_warning")
        if pw:
            st.caption(f"⚠️ {pw}")
    with c2:
        tr = kpis.get("total_r")
        st.metric("Total R", f"{tr:.2f}" if tr is not None else "N/A")
    with c3:
        ar = kpis.get("avg_r")
        st.metric("Avg R", f"{ar:.2f}" if ar is not None else "N/A")
    with c4:
        st.metric("Closed Evaluable", kpis.get("closed_evaluable", 0))
        st.metric("Latest Signal", kpis.get("latest_signal_id", "N/A"))

    st.divider()

# ---------------------------------------------------------------------------
# Executability Panel
# ---------------------------------------------------------------------------
exec_data = data.get("executability", {})
if exec_data.get("available"):
    st.subheader("⚡ Executability")
    st.caption("same_market_bar and execution_detached are different concepts")

    smb = exec_data.get("same_market_bar", {})
    ed = exec_data.get("execution_detached", {})
    demo = exec_data.get("demo_compatibility", {})
    rbf = exec_data.get("retroactive_bar_fill", {})

    c1, c2, c3, c4 = st.columns(4)

    with c1:
        st.markdown("**same_market_bar**")
        st.metric("True", smb.get("true", 0))
        st.metric("False", smb.get("false", 0))
        st.metric("N/A", smb.get("none", 0))
        st.caption(f"Derived: {smb.get('derived', 0)} | Canonical: {smb.get('canonical', 0)}")

    with c2:
        st.markdown("**Execution Detached**")
        st.metric("True", ed.get("true", 0))
        st.metric("False", ed.get("false", 0))
        st.metric("N/A", ed.get("none", 0))

    with c3:
        st.markdown("**Demo Compatibility**")
        for k, v in sorted(demo.items()):
            st.metric(k, v)

    with c4:
        st.markdown("**Retroactive Fill**")
        st.metric("True", rbf.get("true", 0))
        st.metric("False", rbf.get("false", 0))
        st.metric("N/A", rbf.get("none", 0))

    st.divider()

# ---------------------------------------------------------------------------
# Signal Table
# ---------------------------------------------------------------------------
st.subheader("📋 Signal Records")
table = data.get("signal_table", pd.DataFrame())
if not table.empty:
    # Filters
    fc1, fc2, fc3, fc4 = st.columns(4)
    with fc1:
        symbols = sorted(table["symbol"].dropna().unique()) if "symbol" in table.columns else []
        sel_symbol = st.multiselect("Symbol", symbols, default=[])
    with fc2:
        sides = sorted(table["side"].dropna().unique()) if "side" in table.columns else []
        sel_side = st.multiselect("Side", sides, default=[])
    with fc3:
        statuses = sorted(table["status"].dropna().unique()) if "status" in table.columns else []
        sel_status = st.multiselect("Status", statuses, default=[])
    with fc4:
        if "demo_classification" in table.columns:
            demos = sorted(table["demo_classification"].dropna().unique())
            sel_demo = st.multiselect("Demo", demos, default=[])
        else:
            sel_demo = []

    # Apply filters
    filtered = table.copy()
    if sel_symbol and "symbol" in filtered.columns:
        filtered = filtered[filtered["symbol"].isin(sel_symbol)]
    if sel_side and "side" in filtered.columns:
        filtered = filtered[filtered["side"].isin(sel_side)]
    if sel_status and "status" in filtered.columns:
        filtered = filtered[filtered["status"].isin(sel_status)]
    if sel_demo and "demo_classification" in filtered.columns:
        filtered = filtered[filtered["demo_classification"].isin(sel_demo)]

    st.dataframe(filtered, use_container_width=True, hide_index=True)

    # CSV download
    csv = filtered.to_csv(index=False)
    st.download_button(
        "📥 Download filtered CSV",
        data=csv,
        file_name=f"swing_signals_{start_dt.strftime('%Y%m%d')}_{end_dt.strftime('%Y%m%d')}.csv",
        mime="text/csv",
    )
else:
    st.info("No signal records in this window.")

st.divider()

# ---------------------------------------------------------------------------
# Experiments Panel
# ---------------------------------------------------------------------------
st.subheader("🧪 SWING EXPERIMENTAL / SHADOW")
st.caption("NOT OFFICIAL — shadow guard lifecycle tracking")

exp = data.get("experiments", {})
if exp.get("available"):
    st.metric("Experimental rows", exp.get("rows", 0))
    exp_table = exp.get("table", pd.DataFrame())
    if not exp_table.empty:
        st.dataframe(exp_table, use_container_width=True, hide_index=True)
else:
    st.info("No experimental lifecycle data available.")

st.divider()

# ---------------------------------------------------------------------------
# Scanner Diagnostics
# ---------------------------------------------------------------------------
st.subheader("📡 Scanner Shadow Diagnostics")
scanner = data.get("scanner", {})
if scanner.get("available"):
    st.metric("Rows", scanner.get("rows", 0))
else:
    st.info(f"Status: {scanner.get('status', 'No data available')}")
    st.caption(f"Confidence: {scanner.get('confidence', 'STALE / LOW CONFIDENCE / NON-OFFICIAL')}")

st.divider()

# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------
st.caption(
    "⚠️ This dashboard does NOT modify CONTROL. "
    "All data is read-only from PostgreSQL. "
    "Trading is OFF. "
    "Scanner diagnostics are stale, low-confidence, and non-official."
)