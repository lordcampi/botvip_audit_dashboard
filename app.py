from __future__ import annotations

from datetime import datetime, timedelta, time
from typing import Dict, List, Tuple

import pandas as pd
import streamlit as st

from src.db import connect_readonly, db_exists, db_size_mb, get_db_path, get_max_rows, normalize_db_path, quote_ident, read_sql_df
from src.schema import discover_schema, missing_columns, schema_dataframe
from src.parsers import parse_event_metadata_dataframe, parse_metrics_dataframe
from src.metrics import F4_T11A_EVENTS, compute_basic_f4_metrics, event_counts, metrics_to_dataframe
from src.charts import bar_chart_from_counts, dataframe_or_info
from src.reports import audit_markdown, dataframe_to_csv_bytes, summary_to_json_bytes

st.set_page_config(
    page_title="BotVIP Audit Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

EXPECTED_EVENTS_COLUMNS = ["id", "signal_id", "event_type", "price", "metadata", "created_at"]
EXPECTED_SIGNALS_COLUMNS = [
    "id", "symbol", "signal_type", "side", "status", "engine_name", "setup_type", "entry_price",
    "tp_price", "sl_price", "opened_at", "closed_at", "created_at", "pnl_r", "gross_r", "net_r",
    "estimated_cost", "metrics_json",
]

DATE_CANDIDATES = ["created_at", "opened_at", "closed_at", "updated_at"]


def pct(value):
    if value is None or pd.isna(value):
        return "N/A"
    return f"{float(value) * 100:.1f}%"


def num(value):
    if value is None or pd.isna(value):
        return "N/A"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


# --- F5_T01_SIMPLE_VIEW_START ---

def _first_existing_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    if df is None or df.empty:
        return None
    for col in candidates:
        if col in df.columns:
            return col
    return None


def _event_signal_count(events: pd.DataFrame, event_names: list[str]) -> int:
    if events is None or events.empty or "event_type" not in events.columns:
        return 0
    df = events[events["event_type"].astype(str).isin(event_names)]
    if df.empty:
        return 0
    if "signal_id" in df.columns:
        return int(df["signal_id"].dropna().astype(str).nunique())
    return int(len(df))


def compute_simple_public_summary(signals: pd.DataFrame, events: pd.DataFrame, account_usd: float, risk_pct: float, leverage: float) -> dict:
    # Beginner-friendly performance summary.
    # ROI model used here:
    # - Account example: account_usd.
    # - Risk per signal: risk_pct of account.
    # - If net_r exists, PnL per trade = net_r * risk_amount.
    # - If net_r does not exist, fallback approximation: WIN=+1R, LOSS=-1R, cancelled=0R.
    # - Leverage is shown as exposure context: notional = margin_used * leverage.
    # - Leverage does not multiply R automatically, because R is already normalized by risk.
    account_usd = float(account_usd or 100.0)
    risk_pct = float(risk_pct or 1.0)
    leverage = float(leverage or 10.0)
    risk_amount = account_usd * (risk_pct / 100.0)
    notional_per_signal = risk_amount * leverage

    total_sent = 0
    won = 0
    lost = 0
    cancelled = 0
    expired = 0
    breakeven = 0
    total_r = 0.0
    used_real_r = False

    if signals is not None and not signals.empty:
        id_col = "id" if "id" in signals.columns else None
        total_sent = int(signals[id_col].dropna().nunique()) if id_col else int(len(signals))

        if "official_result" in signals.columns:
            official = signals["official_result"].astype(str).str.upper()
            won = int((official == "WIN").sum())
            lost = int(official.isin(["LOSS", "LOST"]).sum())
            breakeven = int(official.isin(["BREAKEVEN", "BE", "FLAT"]).sum())

        status_col = _first_existing_column(signals, ["status", "signal_status", "state"])
        if status_col:
            status = signals[status_col].astype(str).str.lower()
            cancelled = int(status.str.contains("cancel", na=False).sum())
            expired = int(status.str.contains("expir", na=False).sum())
            if won == 0:
                won = int(status.isin(["won", "win"]).sum())
            if lost == 0:
                lost = int(status.isin(["lost", "loss"]).sum())

        r_col = _first_existing_column(signals, ["net_r", "pnl_r", "gross_r"])
        if r_col:
            r_values = pd.to_numeric(signals[r_col], errors="coerce").dropna()
            if not r_values.empty:
                total_r = float(r_values.sum())
                used_real_r = True

    # Event-based fallback and enrichment.
    if events is not None and not events.empty and "event_type" in events.columns:
        event_created = _event_signal_count(events, ["SIGNAL_CREATED", "SIGNAL_SENT", "SIGNAL_PUBLISHED"])
        if total_sent == 0:
            total_sent = event_created if event_created else _event_signal_count(events, ["SIGNAL_ACTIVATED", "PRIMARY_TP_HIT", "STOP_LOSS_HIT", "SIGNAL_CANCELLED"])

        event_wins = _event_signal_count(events, ["PRIMARY_TP_HIT", "TAKE_PROFIT_HIT", "OFFICIAL_RESULT_LOCKED"])
        event_losses = _event_signal_count(events, ["STOP_LOSS_HIT"])
        event_cancelled = _event_signal_count(events, ["SIGNAL_CANCELLED"])
        event_expired = _event_signal_count(events, ["SIGNAL_EXPIRED", "TIME_STOP_EXIT"])
        event_be = _event_signal_count(events, ["BREAKEVEN_STOP_HIT", "RUNNER_BREAKEVEN_STOP_HIT"])

        if won == 0:
            won = event_wins
        if lost == 0:
            lost = event_losses
        if cancelled == 0:
            cancelled = event_cancelled
        if expired == 0:
            expired = event_expired
        if breakeven == 0:
            breakeven = event_be

    if not used_real_r:
        total_r = float(won - lost)

    pnl_usd = total_r * risk_amount
    final_balance = account_usd + pnl_usd
    roi_pct = (pnl_usd / account_usd * 100.0) if account_usd else 0.0
    win_rate = (won / max(1, won + lost)) * 100.0

    return {
        "total_sent": int(total_sent),
        "won": int(won),
        "lost": int(lost),
        "cancelled": int(cancelled),
        "expired": int(expired),
        "breakeven": int(breakeven),
        "win_rate_pct": win_rate,
        "account_usd": account_usd,
        "risk_pct": risk_pct,
        "risk_amount_usd": risk_amount,
        "leverage": leverage,
        "notional_per_signal_usd": notional_per_signal,
        "total_r": total_r,
        "pnl_usd": pnl_usd,
        "final_balance_usd": final_balance,
        "roi_pct": roi_pct,
        "used_real_r": used_real_r,
    }
# --- F5_T01_SIMPLE_VIEW_END ---

def selected_window() -> Tuple[str, datetime | None, datetime | None]:
    st.sidebar.header("Ventana de auditoria")
    window = st.sidebar.radio("Selecciona ventana", ["12h", "24h", "7d", "Custom"], index=1)
    now = datetime.now()
    if window == "12h":
        return window, now - timedelta(hours=12), now
    if window == "24h":
        return window, now - timedelta(hours=24), now
    if window == "7d":
        return window, now - timedelta(days=7), now

    c1, c2 = st.sidebar.columns(2)
    start_date = c1.date_input("Desde", value=(now - timedelta(days=1)).date())
    end_date = c2.date_input("Hasta", value=now.date())
    start_dt = datetime.combine(start_date, time.min)
    end_dt = datetime.combine(end_date, time.max)
    return window, start_dt, end_dt


def date_filter_clause(columns: List[str], preferred: str, start_dt: datetime | None, end_dt: datetime | None) -> Tuple[str, List[str], str | None]:
    if start_dt is None or end_dt is None:
        return "", [], None
    date_col = preferred if preferred in columns else next((c for c in DATE_CANDIDATES if c in columns), None)
    if not date_col:
        return "", [], None
    clause = f" WHERE {quote_ident(date_col)} >= ? AND {quote_ident(date_col)} <= ?"
    return clause, [start_dt.isoformat(sep=" "), end_dt.isoformat(sep=" ")], date_col


def select_existing(columns: List[str], expected: List[str]) -> List[str]:
    selected = [c for c in expected if c in columns]
    if not selected:
        selected = columns[:20]
    return selected


@st.cache_data(ttl=60, show_spinner=False)
def load_schema_cached(db_path: str) -> Dict:
    with connect_readonly(db_path) as conn:
        return discover_schema(conn)


@st.cache_data(ttl=60, show_spinner="Leyendo eventos...")
def load_events_cached(db_path: str, start_text: str, end_text: str, max_rows: int) -> pd.DataFrame:
    with connect_readonly(db_path) as conn:
        schema = discover_schema(conn)
        if "signal_events" not in schema:
            return pd.DataFrame()
        cols = schema["signal_events"].columns
        selected = select_existing(cols, EXPECTED_EVENTS_COLUMNS)
        clause, params, _ = date_filter_clause(cols, "created_at", datetime.fromisoformat(start_text), datetime.fromisoformat(end_text))
        order_col = "id" if "id" in cols else ("created_at" if "created_at" in cols else selected[0])
        query = f"SELECT {', '.join(quote_ident(c) for c in selected)} FROM signal_events{clause} ORDER BY {quote_ident(order_col)} DESC LIMIT ?"
        df = read_sql_df(conn, query, [*params, max_rows])
    df = parse_event_metadata_dataframe(df)
    if "created_at" in df.columns:
        df["created_at_parsed"] = pd.to_datetime(df["created_at"], errors="coerce")
    return df


@st.cache_data(ttl=60, show_spinner="Leyendo senales...")
def load_signals_cached(db_path: str, start_text: str, end_text: str, max_rows: int) -> pd.DataFrame:
    with connect_readonly(db_path) as conn:
        schema = discover_schema(conn)
        if "signals" not in schema:
            return pd.DataFrame()
        cols = schema["signals"].columns
        selected = select_existing(cols, EXPECTED_SIGNALS_COLUMNS)
        clause, params, used_date_col = date_filter_clause(cols, "created_at", datetime.fromisoformat(start_text), datetime.fromisoformat(end_text))
        order_col = "id" if "id" in cols else (used_date_col or selected[0])
        query = f"SELECT {', '.join(quote_ident(c) for c in selected)} FROM signals{clause} ORDER BY {quote_ident(order_col)} DESC LIMIT ?"
        df = read_sql_df(conn, query, [*params, max_rows])
    df = parse_metrics_dataframe(df)
    for col in ["created_at", "opened_at", "closed_at"]:
        if col in df.columns:
            df[f"{col}_parsed"] = pd.to_datetime(df[col], errors="coerce")
    for col in ["entry_price", "tp_price", "sl_price", "pnl_r", "gross_r", "net_r", "estimated_cost"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def render_sidebar_status(db_path: str) -> None:
    st.sidebar.header("Conexion")
    st.sidebar.code(str(normalize_db_path(db_path)))
    if db_exists(db_path):
        st.sidebar.success(f"DB encontrada ({db_size_mb(db_path)} MB)")
    else:
        st.sidebar.error("DB_PATH no existe. Revisa .env o copia la DB localmente.")
    if st.sidebar.button("Refresh cache"):
        st.cache_data.clear()
        st.rerun()


def main() -> None:
    st.title("BotVIP / AlphaScalp Audit Dashboard")
    st.caption("Auditoria read-only para lifecycle, F4_T11a y calibracion observacional. No opera, no calibra automaticamente y no escribe en la DB.")

    db_path = get_db_path()
    max_rows = get_max_rows()
    render_sidebar_status(db_path)
    window_name, start_dt, end_dt = selected_window()

    if not db_exists(db_path):
        st.stop()

    try:
        schema = load_schema_cached(db_path)
    except Exception as exc:
        st.error(f"No se pudo abrir la DB en modo read-only: {exc}")
        st.stop()

    if not schema:
        st.error("La DB no tiene tablas visibles o parece incorrecta/vacia.")
        st.stop()

    if "signal_events" not in schema:
        st.warning("No se encontro la tabla confirmada signal_events.")
    if "signals" not in schema:
        st.warning("No se encontro la tabla esperada signals. El dashboard seguira usando eventos cuando sea posible.")

    if "signal_events" in schema:
        missing = missing_columns(schema, "signal_events", EXPECTED_EVENTS_COLUMNS)
        if missing:
            st.warning("Columnas faltantes en signal_events: " + ", ".join(missing))
    if "signals" in schema:
        missing = missing_columns(schema, "signals", EXPECTED_SIGNALS_COLUMNS)
        if missing:
            st.info("Columnas no encontradas en signals (se usaran fallbacks): " + ", ".join(missing))

    start_text = start_dt.isoformat(sep=" ") if start_dt else "1970-01-01 00:00:00"
    end_text = end_dt.isoformat(sep=" ") if end_dt else datetime.now().isoformat(sep=" ")

    events_df = load_events_cached(db_path, start_text, end_text, max_rows)
    signals_df = load_signals_cached(db_path, start_text, end_text, max_rows)
    counts_df = event_counts(events_df, F4_T11A_EVENTS)
    f4_metrics = compute_basic_f4_metrics(signals_df, events_df)

    tab_overview, tab_simple, tab_f4, tab_events, tab_signals, tab_exports = st.tabs([
        "Overview", "Resumen Simple", "F4_T11a Audit", "Events Explorer", "Signals Explorer", "Export Reports"
    ])

    with tab_overview:
        st.subheader("Estado general")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Ventana", window_name)
        c2.metric("Tablas", len(schema))
        c3.metric("Eventos filtrados", len(events_df))
        c4.metric("Senales filtradas", len(signals_df))

        c5, c6, c7, c8 = st.columns(4)
        c5.metric("Primary TP hit rate", pct(f4_metrics.get("primary_tp_hit_rate")))
        c6.metric("Official win rate", pct(f4_metrics.get("official_win_rate")))
        c7.metric("Real SL rate", pct(f4_metrics.get("real_stop_loss_rate")))
        c8.metric("Time stop rate", pct(f4_metrics.get("time_stop_rate")))

        st.markdown("### Ultima actividad")
        l1, l2 = st.columns(2)
        with l1:
            st.markdown("**Ultimo evento**")
            if not events_df.empty:
                st.dataframe(events_df.head(1), width="stretch", hide_index=True)
            else:
                st.info("No hay eventos en la ventana seleccionada.")
        with l2:
            st.markdown("**Ultima senal**")
            if not signals_df.empty:
                st.dataframe(signals_df.head(1), width="stretch", hide_index=True)
            else:
                st.info("No hay senales en la ventana seleccionada o no existe tabla signals.")

        st.markdown("### Schema discovery")
        dataframe_or_info(schema_dataframe(schema))

# --- F5_T01_SIMPLE_TAB_START ---

    with tab_simple:
        st.subheader("Resumen simple para usuario final")
        st.caption("Vista ejecutiva: señales enviadas, ganadas, perdidas, canceladas y ROI estimado. No modifica estrategia ni DB.")

        sim1, sim2, sim3 = st.columns(3)
        account_usd = sim1.number_input("Cuenta ejemplo USD", min_value=1.0, value=100.0, step=10.0)
        risk_pct = sim2.number_input("Riesgo por señal (% de cuenta)", min_value=0.1, max_value=100.0, value=1.0, step=0.1)
        leverage = sim3.number_input("Apalancamiento mostrado", min_value=1.0, max_value=125.0, value=10.0, step=1.0)

        simple = compute_simple_public_summary(signals_df, events_df, account_usd, risk_pct, leverage)

        a, b, c, d = st.columns(4)
        a.metric("Señales enviadas", simple["total_sent"])
        b.metric("Ganadas", simple["won"])
        c.metric("Perdidas", simple["lost"])
        d.metric("Canceladas", simple["cancelled"])

        e, f, g, h = st.columns(4)
        e.metric("Win rate", f"{simple['win_rate_pct']:.1f}%")
        f.metric("ROI estimado", f"{simple['roi_pct']:.2f}%")
        g.metric("PnL estimado", f"${simple['pnl_usd']:.2f}")
        h.metric("Balance final", f"${simple['final_balance_usd']:.2f}")

        st.markdown("### Supuesto de cálculo")
        st.write(
            "Cuenta de ejemplo: " + f"${simple['account_usd']:.2f}" +
            " | Riesgo por señal: " + f"{simple['risk_pct']:.2f}%" +
            " | Riesgo USD por señal: " + f"${simple['risk_amount_usd']:.2f}" +
            " | Apalancamiento: " + f"{simple['leverage']:.1f}x" +
            " | Exposición/notional aproximado por señal: " + f"${simple['notional_per_signal_usd']:.2f}"
        )
        if simple["used_real_r"]:
            st.success("ROI calculado con R real disponible en signals (net_r/pnl_r/gross_r).")
        else:
            st.warning("ROI aproximado: no se encontró R real utilizable. Se usa WIN=+1R, LOSS=-1R, cancelada=0R.")

        st.info(
            "Nota: el 10x se muestra como exposición aproximada. No multiplico automáticamente el ROI por 10, "
            "porque las métricas en R ya representan ganancia/pérdida contra el riesgo definido."
        )

        summary_df = pd.DataFrame([simple])
        st.download_button(
            "Descargar resumen simple CSV",
            dataframe_to_csv_bytes(summary_df),
            file_name=f"botvip_resumen_simple_{window_name}.csv",
            mime="text/csv",
        )
# --- F5_T01_SIMPLE_TAB_END ---

    with tab_f4:
        st.subheader("F4_T11a Lifecycle Audit")
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("PRIMARY_TP_HIT", int(f4_metrics.get("primary_tp_hit_count", 0) or 0))
        k2.metric("Official WIN rate", pct(f4_metrics.get("official_win_rate")))
        k3.metric("Avg net R", num(f4_metrics.get("official_avg_net_r")))
        k4.metric("Runner extra R avg", num(f4_metrics.get("runner_extra_r")))

        k5, k6, k7, k8 = st.columns(4)
        k5.metric("Runner BE rate", pct(f4_metrics.get("runner_breakeven_rate")))
        k6.metric("Runner TP rate", pct(f4_metrics.get("runner_tp_hit_rate")))
        k7.metric("BE stop rate", pct(f4_metrics.get("breakeven_stop_rate")))
        k8.metric("Geometry missing", int(f4_metrics.get("signals_missing_geometry", 0) or 0))

        st.markdown("### Conteo de eventos F4_T11a")
        bar_chart_from_counts(counts_df, "event_type", "count", "Eventos por tipo")
        dataframe_or_info(counts_df)

        st.markdown("### Metricas calculadas")
        dataframe_or_info(metrics_to_dataframe(f4_metrics))

        st.markdown("### Senales con official_result / runner_shadow")
        wanted = [c for c in ["id", "symbol", "side", "signal_type", "status", "engine_name", "setup_type", "official_result", "official_result_locked", "runner_shadow", "primary_tp_price", "net_r", "created_at"] if c in signals_df.columns]
        dataframe_or_info(signals_df[wanted] if wanted else pd.DataFrame())

    with tab_events:
        st.subheader("Events Explorer")
        filtered_events = events_df.copy()
        if not filtered_events.empty and "event_type" in filtered_events.columns:
            types = sorted(filtered_events["event_type"].dropna().astype(str).unique().tolist())
            selected_types = st.multiselect("Filtrar por event_type", types, default=[])
            if selected_types:
                filtered_events = filtered_events[filtered_events["event_type"].astype(str).isin(selected_types)]
        if not filtered_events.empty and "signal_id" in filtered_events.columns:
            signal_filter = st.text_input("Filtrar por signal_id exacto")
            if signal_filter.strip():
                filtered_events = filtered_events[filtered_events["signal_id"].astype(str) == signal_filter.strip()]

        st.markdown("### Timeline / tabla de eventos")
        dataframe_or_info(filtered_events)

    with tab_signals:
        st.subheader("Signals Explorer")
        filtered_signals = signals_df.copy()
        for col in ["symbol", "side", "signal_type", "status", "setup_type", "engine_name"]:
            if not filtered_signals.empty and col in filtered_signals.columns:
                options = sorted(filtered_signals[col].dropna().astype(str).unique().tolist())
                selected = st.multiselect(f"Filtrar por {col}", options, default=[], key=f"filter_{col}")
                if selected:
                    filtered_signals = filtered_signals[filtered_signals[col].astype(str).isin(selected)]
        dataframe_or_info(filtered_signals)

    with tab_exports:
        st.subheader("Export Reports")
        st.caption("Exporta el resultado filtrado de la ventana seleccionada. No escribe en la DB.")
        st.download_button(
            "Descargar eventos filtrados CSV",
            dataframe_to_csv_bytes(events_df),
            file_name=f"botvip_events_{window_name}.csv",
            mime="text/csv",
        )
        st.download_button(
            "Descargar senales filtradas CSV",
            dataframe_to_csv_bytes(signals_df),
            file_name=f"botvip_signals_{window_name}.csv",
            mime="text/csv",
        )
        st.download_button(
            "Descargar resumen JSON",
            summary_to_json_bytes(f4_metrics),
            file_name=f"botvip_audit_summary_{window_name}.json",
            mime="application/json",
        )
        md = audit_markdown(f4_metrics, counts_df)
        st.download_button(
            "Descargar auditoria Markdown",
            md.encode("utf-8"),
            file_name=f"botvip_audit_{window_name}.md",
            mime="text/markdown",
        )


if __name__ == "__main__":
    main()
