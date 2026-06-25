"""F5_T09b/F5_T09c read-only diagnostics for Daily AI Reporter."""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Iterable

from .parsers import first_path, parse_json_safe

F5_T09B_NO_PROGRESS_ROOT_CAUSE_V3_FILENAME = "20_no_progress_root_cause_v3.json"
F5_T09C_MFE_CAPTURE_EFFICIENCY_FILENAME = "21_mfe_capture_efficiency_by_exit_reason.json"
F5_T09BC_SCHEMA_VERSION = "f5_t09bc_no_progress_mfe_capture_v1"
SPREAD_SENSITIVE_BASE_SYMBOLS = {"SUI", "NEAR", "BCH", "LTC", "DOGE", "PEPE", "SHIB", "FLOKI", "BONK", "WIF"}


def _text(value: Any, default: str = "unknown") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _lower(value: Any, default: str = "unknown") -> str:
    return _text(value, default).lower()


def _upper(value: Any, default: str = "UNKNOWN") -> str:
    return _text(value, default).upper()


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on", "ok", "win", "won"}


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


def _avg(values: Iterable[Any]) -> float | None:
    clean = [v for v in (_num(item) for item in values) if v is not None]
    return None if not clean else round(sum(clean) / len(clean), 6)


def _profit_factor(rows: list[dict[str, Any]], r_key: str = "exit_r") -> dict[str, Any]:
    clean = [v for v in (_num(row.get(r_key)) for row in rows) if v is not None]
    wins = [v for v in clean if v > 0]
    losses = [v for v in clean if v < 0]
    gross_win = round(sum(wins), 6)
    gross_loss_abs = round(abs(sum(losses)), 6)
    if not clean:
        pf = None; note = "no_r_values"
    elif not losses:
        pf = None; note = "no_losses"
    elif not wins:
        pf = 0.0; note = "no_wins"
    else:
        pf = round(gross_win / gross_loss_abs, 6) if gross_loss_abs > 0 else None; note = "ok"
    return {"count": len(rows), "r_values_count": len(clean), "wins": len(wins), "losses": len(losses), "gross_win_r": gross_win, "gross_loss_abs_r": gross_loss_abs, "profit_factor": pf, "note": note}


def _parse_dt(value: Any) -> datetime | None:
    if value in {None, ""}:
        return None
    text = str(value).strip().replace("T", " ")
    if text.endswith("Z"):
        text = text[:-1]
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:26], fmt)
        except Exception:
            pass
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _minutes_between(start: Any, end: Any) -> float | None:
    a = _parse_dt(start); b = _parse_dt(end)
    if not a or not b:
        return None
    return round((b - a).total_seconds() / 60.0, 6)


def _signal_id(row: dict[str, Any]) -> str:
    return _text(row.get("signal_id") or row.get("id"), "unknown")


def _raw_signal_index(signals: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in signals:
        sid = _text(row.get("id") or row.get("signal_id"), "")
        if sid:
            out[sid] = row
    return out


def _metrics_for(row: dict[str, Any], raw_signals: dict[str, dict[str, Any]]) -> dict[str, Any]:
    metrics = parse_json_safe(raw_signals.get(_signal_id(row), {}).get("metrics_json"), default={})
    return metrics if isinstance(metrics, dict) else {}


def _metric(metrics: dict[str, Any], paths: list[str]) -> Any:
    return first_path(metrics, paths, default=None)


def _mfe(row: dict[str, Any], metrics: dict[str, Any]) -> float | None:
    direct = _num(row.get("mfe"))
    if direct is not None:
        return direct
    return _num(_metric(metrics, ["mfe", "mfe_r", "max_mfe_r", "max_favorable_excursion_r", "lifecycle.mfe_r", "progress.mfe_r", "tracking.mfe_r", "runner.mfe_r", "observability.mfe_r"]))


def _mae(row: dict[str, Any], metrics: dict[str, Any]) -> float | None:
    direct = _num(row.get("mae"))
    if direct is not None:
        return direct
    return _num(_metric(metrics, ["mae", "mae_r", "max_mae_r", "max_adverse_excursion_r", "lifecycle.mae_r", "progress.mae_r", "tracking.mae_r", "runner.mae_r", "observability.mae_r"]))


def _exit_r(row: dict[str, Any]) -> float | None:
    for key in ("net_r", "pnl_r", "gross_r"):
        value = _num(row.get(key))
        if value is not None:
            return value
    if _bool(row.get("real_stop_loss_hit")):
        return -1.0
    if _bool(row.get("primary_tp_hit")):
        return _num(row.get("gross_rr")) or 1.0
    return None


def _outcome_family(row: dict[str, Any]) -> str:
    if _bool(row.get("primary_tp_hit")):
        return "primary_tp_official_win"
    if _bool(row.get("real_stop_loss_hit")):
        return "real_stop_loss"
    if _bool(row.get("no_progress_exit")):
        return "no_progress"
    if _bool(row.get("mfe_stall_exit")):
        return "mfe_stall"
    if _bool(row.get("time_stop_exit")):
        return "time_stop"
    if _bool(row.get("breakeven_stop_hit")):
        return "breakeven_stop"
    if _bool(row.get("runner_breakeven_stop_hit")):
        return "runner_breakeven_stop"
    if _bool(row.get("cancelled")):
        return "cancelled_or_expired"
    return _lower(row.get("exit_reason"), "other_or_open")


def _zone(row: dict[str, Any]) -> str:
    if _bool(row.get("weekend")):
        return "weekend"
    if _bool(row.get("killzone")):
        return "killzone"
    if row.get("killzone") is False or str(row.get("killzone")).strip().lower() in {"0", "false", "no"}:
        return "outside_killzone"
    mode = _lower(row.get("operating_mode"), "")
    if "killzone" in mode or "institucional activa" in mode:
        return "killzone"
    if "weekend" in mode or "fin de semana" in mode:
        return "weekend"
    if mode and mode not in {"unknown", "none", "null"}:
        return "outside_killzone"
    return "unknown_zone"


def _score_bucket(row: dict[str, Any]) -> str:
    score = _num(row.get("score"))
    if score is None: return "score_unknown"
    if score < 70: return "score_lt_70"
    if score < 75: return "score_70_74"
    if score < 80: return "score_75_79"
    if score < 89: return "score_80_88"
    return "score_89_plus"


def _duration_bucket(minutes: float | None) -> str:
    if minutes is None: return "duration_unknown"
    if minutes < 3: return "duration_lt_3m"
    if minutes < 10: return "duration_3_to_10m"
    if minutes < 30: return "duration_10_to_30m"
    if minutes < 60: return "duration_30_to_60m"
    return "duration_ge_60m"


def _net_r_bucket(value: float | None) -> str:
    if value is None: return "net_r_unknown"
    if value <= -1: return "net_r_lte_minus_1"
    if value < 0: return "net_r_minus_1_to_0"
    if value == 0: return "net_r_zero"
    if value < 0.5: return "net_r_0_to_0_5"
    if value < 1: return "net_r_0_5_to_1"
    return "net_r_ge_1"


def _mfe_bucket(mfe: float | None) -> str:
    if mfe is None: return "mfe_missing"
    if mfe <= 0: return "mfe_zero"
    if mfe < 0.15: return "mfe_lt_0_15R"
    if mfe < 0.35: return "mfe_0_15_to_0_35R"
    return "mfe_ge_0_35R"


def _btc_bias_conflict(row: dict[str, Any]) -> bool:
    side = _upper(row.get("side"), "")
    btc = _upper(row.get("btc_trend"), "")
    bearish = any(t in btc for t in ("BEAR", "DOWN", "SHORT", "SELL"))
    bullish = any(t in btc for t in ("BULL", "UP", "LONG", "BUY"))
    return (side == "LONG" and bearish) or (side == "SHORT" and bullish)


def _base_symbol(symbol: Any) -> str:
    text = _upper(symbol, "")
    if "/" in text: text = text.split("/", 1)[0]
    if ":" in text: text = text.split(":", 1)[0]
    return text


def _copyability_score(metrics: dict[str, Any]) -> float | None:
    return _num(_metric(metrics, ["copyability_score", "copyability.score", "copyability.final_score", "visible_policy.copyability_score", "copyability_gate.score"]))


def _reclaim_score(metrics: dict[str, Any]) -> float | None:
    return _num(_metric(metrics, ["reclaim_score", "ofa.reclaim_score", "ofa_funnel.reclaim_score", "reclaim.score", "reclaim_quality_score", "setup_quality.reclaim_score"]))


def _low_vol(row: dict[str, Any]) -> bool:
    regime = _upper(row.get("market_regime"), "")
    rvol = _num(row.get("rvol"))
    return "LOW_VOL" in regime or "LOW VOL" in regime or (rvol is not None and rvol < 1.0)


def _no_progress_rows(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in facts if row.get("record_type") == "signal" and (_bool(row.get("no_progress_exit")) or _lower(row.get("exit_reason"), "") == "no_progress")]


def _closed_signal_rows(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for row in facts:
        if row.get("record_type") != "signal":
            continue
        if _lower(row.get("status"), "") in {"open", "pending", "active"} and not row.get("closed_at"):
            continue
        if _outcome_family(row) == "cancelled_or_expired":
            continue
        rows.append(row)
    return rows


def _capture_ratio(exit_r: float | None, mfe: float | None) -> float | None:
    if exit_r is None or mfe is None or mfe <= 0:
        return None
    return round(exit_r / mfe, 6)


def _compact(row: dict[str, Any], metrics: dict[str, Any], mfe: float | None, mae: float | None) -> dict[str, Any]:
    exit_r = _exit_r(row)
    duration = _num(row.get("time_to_close_minutes"))
    if duration is None:
        duration = _minutes_between(row.get("opened_at") or row.get("created_at"), row.get("closed_at"))
    return {"signal_id": _signal_id(row), "symbol": row.get("symbol"), "side": row.get("side"), "setup_type": row.get("setup_type"), "market_regime": row.get("market_regime"), "btc_trend": row.get("btc_trend"), "zone": _zone(row), "score": _num(row.get("score")), "score_bucket": _score_bucket(row), "exit_reason": row.get("exit_reason"), "outcome_family": _outcome_family(row), "exit_r": exit_r, "net_r_bucket": _net_r_bucket(exit_r), "mfe_r": mfe, "mae_r": mae, "mfe_bucket": _mfe_bucket(mfe), "capture_ratio": _capture_ratio(exit_r, mfe), "duration_minutes": duration, "duration_bucket": _duration_bucket(duration), "time_to_entry_minutes": _num(row.get("time_to_entry_minutes")), "data_gap_events": int(_num(row.get("data_gap_events")) or 0), "copyability_score": _copyability_score(metrics), "reclaim_score": _reclaim_score(metrics), "rvol": _num(row.get("rvol")), "estimated_cost": _num(row.get("estimated_cost"))}


def _root_buckets(row: dict[str, Any], metrics: dict[str, Any], mfe: float | None, mae: float | None) -> list[str]:
    buckets = [_mfe_bucket(mfe)]
    if _btc_bias_conflict(row): buckets.append("btc_bias_conflict")
    if _low_vol(row) and (mfe is None or mfe < 0.35): buckets.append("low_vol_no_expansion")
    reclaim_score = _reclaim_score(metrics)
    if reclaim_score is not None and reclaim_score <= 1: buckets.append("reclaim_score_1")
    copyability_score = _copyability_score(metrics)
    if copyability_score is not None and copyability_score < 80: buckets.append("copyability_degraded")
    if (_num(row.get("time_to_entry_minutes")) or 0) > 3: buckets.append("entered_too_late")
    cost = _num(row.get("estimated_cost")); net_rr = _num(row.get("net_rr"))
    if _base_symbol(row.get("symbol")) in SPREAD_SENSITIVE_BASE_SYMBOLS or (cost is not None and net_rr is not None and net_rr > 0 and cost / net_rr >= 0.15): buckets.append("spread_sensitive_symbol")
    if mae is not None and mae <= -0.15 and (mfe is None or mfe < 0.15): buckets.append("adverse_first_minutes")
    return sorted(set(buckets))


def _segment(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[_text(row.get(key))].append(row)
    out = {}
    for name, items in sorted(buckets.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        out[name] = {"count": len(items), "avg_exit_r": _avg(r.get("exit_r") for r in items), "avg_mfe_r": _avg(r.get("mfe_r") for r in items), "avg_mae_r": _avg(r.get("mae_r") for r in items), "avg_capture_ratio": _avg(r.get("capture_ratio") for r in items), "profit_factor": _profit_factor(items)}
    return out


def build_no_progress_root_cause_v3(*, facts: list[dict[str, Any]], events: list[dict[str, Any]] | None = None, signals: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    raw = _raw_signal_index(signals or [])
    rows = []
    bucket_counts: Counter[str] = Counter()
    missing = []
    for row in _no_progress_rows(facts):
        metrics = _metrics_for(row, raw)
        mfe = _mfe(row, metrics); mae = _mae(row, metrics)
        item = _compact(row, metrics, mfe, mae)
        buckets = _root_buckets(row, metrics, mfe, mae)
        item["root_cause_buckets"] = buckets
        item["primary_bucket"] = buckets[0] if buckets else "unclassified"
        item["data_quality_flags"] = []
        if mfe is None: item["data_quality_flags"].append("mfe_missing")
        if mae is None: item["data_quality_flags"].append("mae_missing")
        if item["data_gap_events"] > 0: item["data_quality_flags"].append("data_gap_events_present")
        for bucket in buckets: bucket_counts[bucket] += 1
        if mfe is None or mae is None:
            missing.append({"signal_id": item["signal_id"], "symbol": item["symbol"], "missing_mfe": mfe is None, "missing_mae": mae is None, "note": "Missing MFE/MAE is a data-quality issue; values are not invented."})
        rows.append(item)
    rows.sort(key=lambda item: (len(item.get("data_quality_flags") or []), item.get("exit_r") is None, item.get("exit_r") or 0))
    return {"schema_version": F5_T09BC_SCHEMA_VERSION, "section": "no_progress_root_cause_v3", "read_only": True, "mode": "shadow_observational_only", "purpose": "Diagnose entry-touched low-continuation no-progress exits without changing strategy or lifecycle.", "official_signal_denominator": sum(1 for row in facts if row.get("record_type") == "signal"), "official_no_progress_count": len(rows), "mfe_mae_recovery": {"source_order": ["daily_facts", "signal_records.metrics_json"], "mfe_known": sum(1 for row in rows if row.get("mfe_r") is not None), "mae_known": sum(1 for row in rows if row.get("mae_r") is not None), "missing_mfe_or_mae": len(missing), "examples": missing[:80]}, "bucket_counts": dict(bucket_counts.most_common()), "segments": {"by_symbol": _segment(rows, "symbol"), "by_side": _segment(rows, "side"), "by_market_regime": _segment(rows, "market_regime"), "by_zone": _segment(rows, "zone"), "by_score_bucket": _segment(rows, "score_bucket"), "by_duration_bucket": _segment(rows, "duration_bucket"), "by_net_r_bucket": _segment(rows, "net_r_bucket")}, "top_loss_contributors": sorted([row for row in rows if isinstance(row.get("exit_r"), (int, float)) and row.get("exit_r") < 0], key=lambda item: abs(item.get("exit_r") or 0), reverse=True)[:80], "representative_examples": rows[:300], "summary": {"rows_available": len(rows), "rows_emitted": min(len(rows), 300), "truncated": len(rows) > 300}, "guardrails": ["Reporting-only/read-only: no threshold, timeout, TP/SL, strategy, scanner, lifecycle, Telegram runtime, DB schema, or allowlist changes.", "Missing MFE/MAE is reported as data-quality evidence and is never invented.", "Buckets are observational multi-label classifiers; they are not automatic tuning decisions."]}


def build_mfe_capture_efficiency_by_exit_reason(*, facts: list[dict[str, Any]], events: list[dict[str, Any]] | None = None, signals: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    raw = _raw_signal_index(signals or [])
    rows = []
    missing = []
    for row in _closed_signal_rows(facts):
        metrics = _metrics_for(row, raw)
        mfe = _mfe(row, metrics); mae = _mae(row, metrics)
        item = _compact(row, metrics, mfe, mae)
        item["capture_quality"] = "ok" if item.get("capture_ratio") is not None else "missing_or_nonpositive_mfe"
        if mfe is None or mae is None:
            missing.append({"signal_id": item["signal_id"], "symbol": item["symbol"], "outcome_family": item["outcome_family"], "exit_reason": item["exit_reason"], "missing_mfe": mfe is None, "missing_mae": mae is None})
        rows.append(item)
    leak_rows = [row for row in rows if row.get("mfe_r") is not None and row.get("mfe_r") >= 0.35 and (row.get("capture_ratio") is None or row.get("capture_ratio") < 0.35)]
    leak_rows.sort(key=lambda item: ((item.get("mfe_r") or 0), -(item.get("capture_ratio") or -999)), reverse=True)
    return {"schema_version": F5_T09BC_SCHEMA_VERSION, "section": "mfe_capture_efficiency_by_exit_reason", "read_only": True, "mode": "shadow_observational_only", "purpose": "Measure how much favorable excursion was captured by exit reason without changing exits or strategy.", "definitions": {"capture_ratio": "exit_R / max_MFE_R when exit_R is known and max_MFE_R > 0.", "exit_R_source_order": ["net_r", "pnl_r", "gross_r", "fallback_stop_loss_minus_1", "fallback_primary_tp_rr_or_1"], "mfe_mae_source_order": ["daily_facts", "signal_records.metrics_json"]}, "official_signal_denominator": sum(1 for row in facts if row.get("record_type") == "signal"), "closed_rows_evaluated": len(rows), "data_quality": {"mfe_known": sum(1 for row in rows if row.get("mfe_r") is not None), "mae_known": sum(1 for row in rows if row.get("mae_r") is not None), "capture_ratio_known": sum(1 for row in rows if row.get("capture_ratio") is not None), "missing_mfe_or_mae": len(missing), "missing_examples": missing[:80]}, "segments": {"by_exit_reason": _segment(rows, "exit_reason"), "by_outcome_family": _segment(rows, "outcome_family"), "by_symbol": _segment(rows, "symbol"), "by_zone": _segment(rows, "zone"), "by_score_bucket": _segment(rows, "score_bucket"), "by_duration_bucket": _segment(rows, "duration_bucket")}, "mfe_capture_leak_examples": leak_rows[:100], "rows": rows[:300], "summary": {"rows_available": len(rows), "rows_emitted": min(len(rows), 300), "truncated": len(rows) > 300, "exit_reason_counts": dict(Counter(_text(row.get("exit_reason")) for row in rows).most_common()), "outcome_family_counts": dict(Counter(_text(row.get("outcome_family")) for row in rows).most_common())}, "guardrails": ["Capture efficiency is descriptive only and must not alter TP/SL, no-progress timeout, MFE-stall logic, strategy, or runtime.", "Official PRIMARY_TP_HIT remains the protected WIN; runner outcomes are segmented separately.", "Missing MFE/MAE is reported as data-quality evidence and is never invented."]}


def build_f5_t09bc_no_progress_mfe_outputs(*, facts: list[dict[str, Any]], events: list[dict[str, Any]], signals: list[dict[str, Any]], candidates: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"no_progress_root_cause_v3": build_no_progress_root_cause_v3(facts=facts, events=events, signals=signals), "mfe_capture_efficiency_by_exit_reason": build_mfe_capture_efficiency_by_exit_reason(facts=facts, events=events, signals=signals)}
