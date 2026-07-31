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
    "**Scope:** SWING_TREND_RECLAIM_V1 **SHORT only**  \n"
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
    excl_non_swing = data.get("excluded_non_swing", 0)
    excl_long = data.get("excluded_long", 0)
    st.metric("Rows excluded (non-SWING)", excl_non_swing if excl_non_swing else "N/A")
    st.caption(f"↳ of which LONG: {excl_long if excl_long else '0'}")

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
# SWING Shadow core pairs (Telegram + Binance Demo)
# ---------------------------------------------------------------------------
st.subheader("🧪 SWING SHADOW — CORE PAIRS (TELEGRAM + DEMO)")
st.caption(
    "NOT OFFICIAL — shadow signals from the core pairs that are sent to "
    "Telegram and executed on Binance Demo."
)

shadow = data.get("shadow", {})
if shadow.get("available"):
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("Shadow signals", shadow.get("rows", 0))
    with c2:
        st.metric("Pairs", shadow.get("pairs", 0))
    with c3:
        wins = int(shadow["table"]["wins"].sum()) if not shadow["table"].empty else 0
        losses = int(shadow["table"]["losses"].sum()) if not shadow["table"].empty else 0
        total_closed = wins + losses
        wr = round(wins / total_closed * 100, 1) if total_closed > 0 else None
        st.metric("Combined WR", f"{wr}%" if wr is not None else "N/A")

    shadow_table = shadow.get("table", pd.DataFrame())
    if not shadow_table.empty:
        st.dataframe(
            shadow_table,
            use_container_width=True,
            hide_index=True,
            column_config={
                "symbol": st.column_config.TextColumn("Symbol", width="medium"),
                "signals": st.column_config.NumberColumn("Signals", width="small"),
                "closed": st.column_config.NumberColumn("Closed", width="small"),
                "wins": st.column_config.NumberColumn("Wins", width="small"),
                "losses": st.column_config.NumberColumn("Losses", width="small"),
                "win_rate": st.column_config.NumberColumn("WR %", width="small"),
                "total_r": st.column_config.NumberColumn("Total R", width="small"),
            },
        )
else:
    st.info("No SWING shadow core-pair data available in this window.")

st.divider()

# ---------------------------------------------------------------------------
# INTERNAL UNIVERSE PROBE (experimental only — no Telegram, no Demo)
# ---------------------------------------------------------------------------
st.subheader("🧪 SWING EXPERIMENTAL / SHADOW — UNIVERSE PROBE (SHORT)")
st.caption(
    "NOT OFFICIAL — internal SHORT-only universe probe (`swing_short_universe_probe_v1`). "
    "Runs the same SWING_TREND_RECLAIM strategy across additional pairs. "
    "**Never** sent to Telegram. **Never** executed on Binance Demo."
)

exp = data.get("experiments", {})
if exp.get("available"):
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("Probe signals", exp.get("rows", 0))
    with c2:
        probe_table = exp.get("table", pd.DataFrame())
        tiers = sorted(probe_table["tier"].dropna().unique()) if not probe_table.empty and "tier" in probe_table.columns else []
        st.metric("Tiers", ", ".join(tiers) if tiers else "N/A")
    with c3:
        if not probe_table.empty and "result_r" in probe_table.columns:
            closed_mask = probe_table["status"].astype(str).str.upper().str.startswith("PROBE_")
            closed_r = pd.to_numeric(probe_table.loc[closed_mask, "result_r"], errors="coerce").dropna()
            st.metric("Closed evaluable", len(closed_r))
        else:
            st.metric("Closed evaluable", "N/A")

    if not probe_table.empty:
        st.dataframe(probe_table, use_container_width=True, hide_index=True)
else:
    st.info(
        "No universe probe data available in this window. "
        "The probe is enabled (`SWING_INTERNAL_UNIVERSE_PROBE_ENABLED=true`) "
        "but may have no rows yet."
    )

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
# R3C — Swing Review Pack for Copilot
# ---------------------------------------------------------------------------
st.subheader("📦 Export Review Pack for Copilot")

# Session-state keys for R3C
_R3C_KEYS = [
    "r3c_zip_bytes",
    "r3c_prompt_text",
    "r3c_review_id",
    "r3c_generated_at",
    "r3c_fingerprint",
    "r3c_scope",
]

# --- Determine generation validity -------------------------------------------
_quality = data.get("data_quality", {})
_ql = _quality.get("level", "UNKNOWN")
_has_signals = data.get("total_signals", 0) > 0
_has_fp = bool(selected_fp)
_no_error = not data.get("error")

_can_generate = _has_signals and _has_fp and _no_error
_blocked_invalid = _ql == "INVALID" and _can_generate
_allowed_quality = _can_generate and not _blocked_invalid

# --- Preview card ------------------------------------------------------------
if _can_generate:
    _scope = "latest_only" if sel_fp_mode == "Latest fingerprint only" else "all_mixed"
    _window_co = f"{start_dt.strftime('%Y-%m-%d %H:%M')} → {end_dt.strftime('%Y-%m-%d %H:%M')} CO"
    _fp_preview = selected_fp[:16] if selected_fp and selected_fp != "ALL" else "MIXED"
    _warnings = quality.get("reasons", [])
    _readiness = "INVALID" if _blocked_invalid else (
        "INSUFFICIENT" if _ql in ("INSUFFICIENT",) else "OK"
    )

    st.caption("**Preview** — data that will be packaged for Copilot")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("Window (CO)", _window_co[:40] + "…" if len(_window_co) > 40 else _window_co)
    with c2:
        st.metric("Fingerprint", _fp_preview)
    with c3:
        st.metric("Signals", data.get("total_signals", 0))
    with c4:
        st.metric("Quality", _ql)
    if _warnings:
        st.caption("Quality reasons: " + " · ".join(_warnings))

# --- INVALID blocker ---------------------------------------------------------
if _blocked_invalid:
    st.error(
        "🚫 Data quality is INVALID. The Review Pack has been blocked to avoid "
        "sending unreliable data to Copilot. Review the quality reasons above "
        "and adjust the window or fingerprint before retrying."
    )
elif _allowed_quality:
    # --- PARTIAL / INSUFFICIENT warning -------------------------------------
    if _ql in ("PARTIAL", "INSUFFICIENT"):
        st.warning(
            f"⚠️ Data quality is **{_ql}**. The Review Pack can be generated, "
            "but conclusions should be treated with conservative confidence. "
            "Copilot will see a DATA_QUALITY_PARTIAL readiness decision."
        )

    # --- Initialise session state keys ---------------------------------------
    for _k in _R3C_KEYS:
        if _k not in st.session_state:
            st.session_state[_k] = None

    # --- Detect if fingerprint/scope changed → invalidate cached review ------
    _current_fp_scope = f"{selected_fp}|{_scope}"
    if st.session_state.get("r3c_fingerprint") and st.session_state["r3c_fingerprint"] != _current_fp_scope:
        for _k in _R3C_KEYS:
            st.session_state[_k] = None

    # --- Generate button ----------------------------------------------------
    if st.session_state.get("r3c_zip_bytes") is None:
        if st.button("⚡ Generate review for Copilot", type="primary", use_container_width=True):
            with st.spinner("Building deterministic R3B ZIP (in memory)…"):
                try:
                    from src.swing_prompt_builder import build_swing_review_pack_for_download, build_copilot_prompt
                    from src.swing_review_pack_builder import build_review_contents

                    _gen_at = datetime.utcnow()
                    _start_utc = data.get("window_start_utc")
                    _end_utc = data.get("window_end_utc")
                    _start_co = data.get("window_start_co")
                    _end_co = data.get("window_end_co")

                    _zip_bytes = build_swing_review_pack_for_download(
                        data, selected_fp, _scope,
                        _start_utc, _end_utc, _start_co, _end_co, _gen_at,
                    )

                    # Extract prompt text from the ZIP for separate download
                    _draft = build_review_contents(
                        data, selected_fp, _scope,
                        _start_utc, _end_utc, _start_co, _end_co, _gen_at,
                    )
                    _prompt_text = build_copilot_prompt(_draft)

                    _review_id = f"SWING-{_gen_at.strftime('%Y%m%d-%H%M')}"

                    # --- R4B: Persist to history (best-effort, non-blocking) ---
                    try:
                        from src.swing_review_history import ReviewHistoryManager, generate_review_id, _compute_sha256_bytes
                        _content_hash = _compute_sha256_bytes(_zip_bytes)
                        _collision_free_id = generate_review_id(_gen_at, _content_hash)

                        _mgr = ReviewHistoryManager()
                        if not _mgr.is_index_valid():
                            st.warning(
                                "⚠️ Review history index is corrupt. The review was generated "
                                "but NOT persisted to history. Previous reviews may still be "
                                "recoverable — check data/swing_review_index.json.corrupted_*."
                            )
                        else:
                            _metadata = {
                                "generated_at_utc": _gen_at.isoformat(),
                                "data_loaded_at_utc": data.get("loaded_at").isoformat() if data.get("loaded_at") else None,
                                "window_start_utc": _start_utc.isoformat() if _start_utc else None,
                                "window_end_utc": _end_utc.isoformat() if _end_utc else None,
                                "window_start_colombia": _start_co.isoformat() if _start_co else None,
                                "window_end_colombia": _end_co.isoformat() if _end_co else None,
                                "strategy": "SWING_TREND_RECLAIM_V1",
                                "selected_fingerprint": selected_fp,
                                "fingerprint_scope": _scope,
                                "signal_count": data.get("total_signals", 0),
                                "closed_count": kpis.get("lifecycle_closed", 0),
                                "shadow_count": data.get("shadow", {}).get("rows", 0),
                                "experimental_count": data.get("experiments", {}).get("rows", 0),
                                "quality_level": _ql,
                                "quality_reasons": quality.get("reasons", []),
                                "readiness_decision": _draft.get("readiness", {}).get("decision", "UNKNOWN"),
                                "prompt_status": "READY",
                                "complete_for_copilot": True,
                                "source_commit": "190aed7",
                            }
                            _mgr.persist_review(
                                _collision_free_id,
                                _zip_bytes,
                                _prompt_text.encode("utf-8"),
                                _metadata,
                            )
                            # Update the short review_id to the collision-free one
                            _review_id = _collision_free_id
                    except Exception:
                        pass  # history persistence failure is non-blocking

                    st.session_state["r3c_zip_bytes"] = _zip_bytes
                    st.session_state["r3c_prompt_text"] = _prompt_text
                    st.session_state["r3c_review_id"] = _review_id
                    st.session_state["r3c_generated_at"] = _gen_at.isoformat()
                    st.session_state["r3c_fingerprint"] = _current_fp_scope
                    st.session_state["r3c_scope"] = _scope

                except Exception as _exc:
                    st.error(f"❌ Generation failed: {_exc}")

    # --- Show generated review controls ------------------------------------
    if st.session_state.get("r3c_zip_bytes") is not None:
        _review_id = st.session_state.get("r3c_review_id", "Unknown")
        _gen_ts = st.session_state.get("r3c_generated_at", "")
        _zip_bytes = st.session_state["r3c_zip_bytes"]
        _prompt_text = st.session_state["r3c_prompt_text"]

        st.success(f"✅ Review pack ready — **{_review_id}**")

        col_dl1, col_dl2 = st.columns(2)
        with col_dl1:
            st.download_button(
                label="📥 Download Review Pack ZIP",
                data=_zip_bytes,
                file_name=f"SWING_REVIEW_PACK_R3B_{_review_id}.zip",
                mime="application/zip",
                use_container_width=True,
            )
        with col_dl2:
            if _prompt_text:
                st.download_button(
                    label="📄 Download Copilot Prompt (.md)",
                    data=_prompt_text,
                    file_name=f"10_prompt_for_copilot_{_review_id}.md",
                    mime="text/markdown",
                    use_container_width=True,
                )

        st.caption(
            f"**Review ID:** {_review_id}  ·  "
            f"**Generated:** {_gen_ts} UTC  ·  "
            f"**Fingerprint:** `{_fp_preview}`  ·  "
            f"**Scope:** {st.session_state.get('r3c_scope', 'N/A')}  ·  "
            "11 files (R3B manifest + 01-09 analytical + Copilot prompt)"
        )

        # --- Clear generated review button ---------------------------------
        if st.button("🗑️ Clear generated review", use_container_width=False):
            for _k in _R3C_KEYS:
                st.session_state[_k] = None

else:
    # --- Why ZIP is not available -------------------------------------------
    if not _has_signals and not data.get("error"):
        st.info("📭 No hay señales SWING en esta ventana. El Review Pack solo se habilita cuando existen señales.")
    elif not _has_fp:
        st.info("📭 No se detectó un fingerprint de configuración. Selecciona Latest fingerprint o All fingerprints.")
    elif data.get("error"):
        st.info("📭 Datos no disponibles. Corrige el error de conexión para habilitar la descarga.")

st.divider()

# ---------------------------------------------------------------------------
# R4B — Review History
# ---------------------------------------------------------------------------
with st.expander("📜 Review History", expanded=False):
    try:
        from src.swing_review_history import ReviewHistoryManager, _history_enabled

        if not _history_enabled():
            st.info("📭 Review history is disabled (SWING_HISTORY_ENABLED=false).")
        else:
            _mgr = ReviewHistoryManager()

            # --- Index corruption check ------------------------------------------
            if not _mgr.is_index_valid():
                st.error(
                    "🚫 **Review history index is corrupt.** Persist, delete, and "
                    "cleanup operations are blocked. The generated review was NOT "
                    "persisted. Previous reviews may still be recoverable — check "
                    "`data/swing_review_index.json.corrupted_*` for the archived "
                    "corrupt file."
                )
            else:
                # --- Normal operation --------------------------------------------
                _reviews = _mgr.list_reviews()
                _total_size = (
                    sum(e.get("zip_size_bytes", 0) + e.get("prompt_size_bytes", 0)
                        for e in _reviews)
                    if _reviews else 0
                )

                if not _reviews:
                    st.info("No reviews in history yet. Generate a review above to persist it.")
                else:
                    col_sm, col_btn1, col_btn2 = st.columns([3, 1, 1])
                    with col_sm:
                        pct = min(100, int(len(_reviews) / 250 * 100))
                        st.caption(
                            f"**{len(_reviews)}** reviews stored · "
                            f"**{_total_size / 1024:.0f} KB** total · "
                            f"Oldest: {_reviews[-1].get('generated_at_utc', 'N/A')[:10]}"
                        )
                        st.progress(pct, text=f"Storage: {len(_reviews)}/250")
                    with col_btn1:
                        if st.button("🧹 Cleanup expired", help="Delete reviews past retention date"):
                            _removed = _mgr.cleanup_expired()
                            if _removed:
                                st.success(f"Removed {_removed} expired reviews.")
                            else:
                                st.info("No expired reviews.")
                    with col_btn2:
                        if st.button("🗜️ FIFO cleanup", help="Delete oldest reviews if over 250 limit"):
                            _removed = _mgr.cleanup_fifo()
                            if _removed:
                                st.success(f"FIFO removed {_removed} oldest reviews.")
                            else:
                                st.info("Below 250 limit, nothing removed.")

                    # --- Review table --------------------------------------------
                    _rows = []
                    for _e in _reviews:
                        _rows.append({
                            "Review ID": _e.get("review_id", "?"),
                            "Date (CO)": _e.get("window_start_colombia", "")[:10] if _e.get("window_start_colombia") else "?",
                            "Fingerprint": (_e.get("selected_fingerprint", "?") or "?")[:12] + "…",
                            "Scope": _e.get("fingerprint_scope", "?"),
                            "Signals": _e.get("signal_count", 0),
                            "Quality": _e.get("quality_level", "?"),
                            "Readiness": _e.get("readiness_decision", "?"),
                            "Size": f"{(_e.get('zip_size_bytes', 0) + _e.get('prompt_size_bytes', 0)) / 1024:.0f} KB",
                        })

                    st.dataframe(
                        _rows,
                        use_container_width=True,
                        hide_index=True,
                        column_config={
                            "Review ID": st.column_config.TextColumn(width="small"),
                            "Date (CO)": st.column_config.TextColumn(width="small"),
                            "Fingerprint": st.column_config.TextColumn(width="small"),
                            "Scope": st.column_config.TextColumn(width="small"),
                            "Signals": st.column_config.NumberColumn(width="small"),
                            "Quality": st.column_config.TextColumn(width="small"),
                            "Readiness": st.column_config.TextColumn(width="small"),
                            "Size": st.column_config.TextColumn(width="small"),
                        },
                    )

                    # --- Per-review actions -----------------------------------
                    st.caption("**Actions** — select a review to re-download or delete")
                    _review_ids = [e.get("review_id") for e in _reviews]
                    _sel_review = st.selectbox(
                        "Review ID",
                        options=[""] + _review_ids,
                        format_func=lambda x: x if x else "Select a review…",
                        label_visibility="collapsed",
                    )
                    if _sel_review:
                        _col_a1, _col_a2, _col_a3 = st.columns(3)
                        with _col_a1:
                            try:
                                _zip_b, _prompt_b, _meta = _mgr.get_review(_sel_review)
                                if _zip_b:
                                    st.download_button(
                                        f"📥 Re-download ZIP",
                                        data=_zip_b,
                                        file_name=f"SWING_REVIEW_PACK_R3B_{_sel_review}.zip",
                                        mime="application/zip",
                                        use_container_width=True,
                                    )
                            except ValueError as _ve:
                                st.error(f"⚠️ Integrity check failed: {_ve}")
                            except Exception:
                                st.warning("⚠️ Could not retrieve ZIP.")
                        with _col_a2:
                            try:
                                if not st.session_state.get("_r4b_prompt_cache"):
                                    st.session_state["_r4b_prompt_cache"] = {}
                                _cache = st.session_state["_r4b_prompt_cache"]
                                if _sel_review not in _cache:
                                    _zip_b2, _prompt_b2, _ = _mgr.get_review(_sel_review)
                                    _cache[_sel_review] = _prompt_b2.decode("utf-8") if _prompt_b2 else None
                                _prompt_str = _cache.get(_sel_review)
                                if _prompt_str:
                                    st.download_button(
                                        f"📄 Re-download Prompt",
                                        data=_prompt_str,
                                        file_name=f"10_prompt_for_copilot_{_sel_review}.md",
                                        mime="text/markdown",
                                        use_container_width=True,
                                    )
                            except ValueError:
                                st.warning("⚠️ Prompt integrity check failed.")
                            except Exception:
                                st.warning("⚠️ Could not retrieve prompt.")
                        with _col_a3:
                            _confirm_key = f"_r4b_delete_confirm_{_sel_review}"
                            if _confirm_key not in st.session_state:
                                st.session_state[_confirm_key] = False
                            if not st.session_state[_confirm_key]:
                                if st.button(f"🗑️ Delete", use_container_width=True, key=f"del_btn_{_sel_review}"):
                                    st.session_state[_confirm_key] = True
                            else:
                                st.warning(f"Delete **{_sel_review}**? This cannot be undone.")
                                _cc1, _cc2 = st.columns(2)
                                with _cc1:
                                    if st.button("✅ Confirm", use_container_width=True, key=f"confirm_{_sel_review}"):
                                        _ok = _mgr.delete_review(_sel_review)
                                        if _ok:
                                            st.success(f"Deleted {_sel_review}.")
                                            if "r3c_review_id" in st.session_state and st.session_state["r3c_review_id"] == _sel_review:
                                                st.session_state["r3c_review_id"] = "Unknown"
                                        else:
                                            st.info("Already deleted (idempotent).")
                                        st.session_state[_confirm_key] = False
                                        # Clear prompt cache
                                        st.session_state.get("_r4b_prompt_cache", {}).pop(_sel_review, None)
                                with _cc2:
                                    if st.button("❌ Cancel", use_container_width=True, key=f"cancel_{_sel_review}"):
                                        st.session_state[_confirm_key] = False

        st.caption(
            "History is stored in `data/swing_reviews/`. "
            "Index: `data/swing_review_index.json`. "
            "Retention: 90 days / 250 max. "
            "SHA-256 verified on re-download."
        )
    except Exception as _hist_exc:
        st.error(f"⚠️ History UI error: {_hist_exc}")

# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------
st.caption(
    "⚠️ This dashboard does NOT modify CONTROL. "
    "All data is read-only from PostgreSQL. "
    "Trading is OFF. "
    "Scanner diagnostics are stale, low-confidence, and non-official."
)
