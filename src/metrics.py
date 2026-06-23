from __future__ import annotations

from typing import Dict, Iterable, Tuple

import pandas as pd

F4_T11A_EVENTS = [
    "PRIMARY_TP_HIT",
    "OFFICIAL_RESULT_LOCKED",
    "BREAKEVEN_ARMED",
    "SL_MOVED_TO_BREAKEVEN",
    "BREAKEVEN_STOP_HIT",
    "RUNNER_BREAKEVEN_STOP_HIT",
    "RUNNER_TP_HIT",
    "STOP_LOSS_HIT",
    "TAKE_PROFIT_HIT",
    "TIME_STOP_EXIT",
    "SIGNAL_ACTIVATED",
    "SIGNAL_CREATED",
    "SIGNAL_CANCELLED",
    "DATA_GAP_DETECTED",
]


def _nunique_signal_ids(df: pd.DataFrame) -> int:
    if df is None or df.empty or "signal_id" not in df.columns:
        return 0
    return int(df["signal_id"].dropna().nunique())


def event_counts(events: pd.DataFrame, event_types: Iterable[str] | None = None) -> pd.DataFrame:
    if events is None or events.empty or "event_type" not in events.columns:
        return pd.DataFrame(columns=["event_type", "count"])
    df = events.copy()
    if event_types is not None:
        df = df[df["event_type"].isin(list(event_types))]
    if df.empty:
        return pd.DataFrame(columns=["event_type", "count"])
    return df.groupby("event_type", dropna=False).size().reset_index(name="count").sort_values("event_type")


def lifecycle_event_signal_sets(events: pd.DataFrame) -> Dict[str, set]:
    result: Dict[str, set] = {}
    if events is None or events.empty or "event_type" not in events.columns or "signal_id" not in events.columns:
        return result
    for event_type, group in events.dropna(subset=["signal_id"]).groupby("event_type"):
        result[str(event_type)] = set(group["signal_id"].dropna().astype(str))
    return result


def safe_rate(num: int | float, den: int | float) -> float:
    try:
        if den == 0 or pd.isna(den):
            return 0.0
        return float(num) / float(den)
    except Exception:
        return 0.0


def compute_basic_f4_metrics(signals: pd.DataFrame, events: pd.DataFrame) -> Dict[str, float | int | None]:
    event_sets = lifecycle_event_signal_sets(events)

    if signals is not None and not signals.empty and "id" in signals.columns:
        total_signals = int(signals["id"].dropna().nunique())
        signal_ids = set(signals["id"].dropna().astype(str))
    else:
        total_signals = _nunique_signal_ids(events)
        signal_ids = set(events["signal_id"].dropna().astype(str)) if events is not None and "signal_id" in events.columns else set()

    primary_tp_ids = event_sets.get("PRIMARY_TP_HIT", set())
    real_sl_ids = event_sets.get("STOP_LOSS_HIT", set())
    time_stop_ids = event_sets.get("TIME_STOP_EXIT", set())
    be_ids = event_sets.get("BREAKEVEN_STOP_HIT", set())
    runner_be_ids = event_sets.get("RUNNER_BREAKEVEN_STOP_HIT", set())
    runner_tp_ids = event_sets.get("RUNNER_TP_HIT", set())

    if signal_ids:
        primary_tp_count = len(primary_tp_ids & signal_ids)
        real_sl_count = len(real_sl_ids & signal_ids)
        time_stop_count = len(time_stop_ids & signal_ids)
        be_count = len(be_ids & signal_ids)
        runner_be_count = len(runner_be_ids & signal_ids)
        runner_tp_count = len(runner_tp_ids & signal_ids)
    else:
        primary_tp_count = len(primary_tp_ids)
        real_sl_count = len(real_sl_ids)
        time_stop_count = len(time_stop_ids)
        be_count = len(be_ids)
        runner_be_count = len(runner_be_ids)
        runner_tp_count = len(runner_tp_ids)

    metrics: Dict[str, float | int | None] = {
        "total_signals": total_signals,
        "primary_tp_hit_count": primary_tp_count,
        "primary_tp_hit_rate": safe_rate(primary_tp_count, total_signals),
        "real_stop_loss_count": real_sl_count,
        "real_stop_loss_rate": safe_rate(real_sl_count, total_signals),
        "time_stop_count": time_stop_count,
        "time_stop_rate": safe_rate(time_stop_count, total_signals),
        "breakeven_stop_count": be_count,
        "breakeven_stop_rate": safe_rate(be_count, total_signals),
        "runner_breakeven_count": runner_be_count,
        "runner_breakeven_rate": safe_rate(runner_be_count, total_signals),
        "runner_tp_hit_count": runner_tp_count,
        "runner_tp_hit_rate": safe_rate(runner_tp_count, total_signals),
    }

    if signals is not None and not signals.empty:
        s = signals.copy()
        if "official_result" in s.columns:
            official = s["official_result"].astype(str).str.upper()
            known = official[~official.isin(["", "NONE", "NAN", "NULL"])]
            wins = int((known == "WIN").sum())
            metrics["signals_with_official_result"] = int(len(known))
            metrics["official_win_count"] = wins
            metrics["official_win_rate"] = safe_rate(wins, len(known))
        else:
            metrics["signals_with_official_result"] = 0
            metrics["official_win_count"] = primary_tp_count
            metrics["official_win_rate"] = safe_rate(primary_tp_count, total_signals)

        for col in ["initial_geometry", "current_geometry", "runner_shadow"]:
            if col in s.columns:
                metrics[f"signals_with_{col}"] = int(s[col].apply(lambda x: isinstance(x, (dict, list)) or (pd.notna(x) and str(x).strip() not in {"", "None", "nan"})).sum())
            else:
                metrics[f"signals_with_{col}"] = 0
        metrics["signals_missing_geometry"] = int(max(0, total_signals - int(metrics.get("signals_with_initial_geometry", 0))))

        if "net_r" in s.columns:
            net_r = pd.to_numeric(s["net_r"], errors="coerce")
            metrics["official_avg_net_r"] = float(net_r.dropna().mean()) if not net_r.dropna().empty else None
        else:
            metrics["official_avg_net_r"] = None

        if "runner_extra_r" in s.columns:
            runner_extra = pd.to_numeric(s["runner_extra_r"], errors="coerce")
            metrics["runner_extra_r"] = float(runner_extra.dropna().mean()) if not runner_extra.dropna().empty else None
        else:
            metrics["runner_extra_r"] = None
    else:
        metrics.update({
            "signals_with_official_result": 0,
            "official_win_count": primary_tp_count,
            "official_win_rate": safe_rate(primary_tp_count, total_signals),
            "signals_with_initial_geometry": 0,
            "signals_with_current_geometry": 0,
            "signals_with_runner_shadow": 0,
            "signals_missing_geometry": 0,
            "official_avg_net_r": None,
            "runner_extra_r": None,
        })

    if events is not None and not events.empty and "metadata_parse_error" in events.columns:
        metrics["events_missing_metadata"] = int(events["metadata_parse_error"].fillna(False).sum())
    else:
        metrics["events_missing_metadata"] = 0

    return metrics


def compute_lifecycle_metrics(signals: pd.DataFrame, events: pd.DataFrame) -> Dict[str, float | int | None]:
    metrics = compute_basic_f4_metrics(signals, events)
    if signals is not None and not signals.empty:
        s = signals.copy()
        status = s["status"].astype(str).str.lower() if "status" in s.columns else pd.Series([], dtype=str)
        metrics["pending_signals"] = int(status.isin(["pending", "created", "new"]).sum()) if not status.empty else None
        metrics["open_signals"] = int(status.isin(["open", "active", "activated"]).sum()) if not status.empty else None
        metrics["closed_signals"] = int(status.isin(["closed", "completed", "done"]).sum()) if not status.empty else None
        metrics["won_signals"] = int(status.isin(["won", "win"]).sum()) if not status.empty else int(metrics.get("official_win_count", 0) or 0)
        metrics["lost_signals"] = int(status.isin(["lost", "loss"]).sum()) if not status.empty else int(metrics.get("real_stop_loss_count", 0) or 0)
        metrics["cancelled_signals"] = int(status.str.contains("cancel", na=False).sum()) if not status.empty else None
        metrics["expired_signals"] = int(status.str.contains("expir", na=False).sum()) if not status.empty else None
    return metrics


def metrics_to_dataframe(metrics: Dict[str, float | int | None]) -> pd.DataFrame:
    return pd.DataFrame([{"metric": key, "value": value} for key, value in metrics.items()])
