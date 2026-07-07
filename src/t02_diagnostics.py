from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def _is_true(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except Exception:
        return None


def _avg(values: list[float | None]) -> float | None:
    clean = [v for v in values if v is not None]
    if not clean:
        return None
    return round(sum(clean) / len(clean), 6)


def _safe_key(value: Any) -> str:
    if value is None or value == "":
        return "unknown"
    return str(value)


def _counter(rows: list[dict[str, Any]], key: str, limit: int = 20) -> dict[str, int]:
    return dict(Counter(_safe_key(row.get(key)) for row in rows).most_common(limit))


def _has_entry(row: dict[str, Any]) -> bool:
    return _num(row.get("entry_price")) is not None


def _has_tp_sl(row: dict[str, Any]) -> bool:
    return _num(row.get("tp_price")) is not None and _num(row.get("sl_price")) is not None


def _has_geometry(row: dict[str, Any]) -> bool:
    return _has_entry(row) and _has_tp_sl(row)


def _has_mfe_mae(row: dict[str, Any]) -> bool:
    return _num(row.get("mfe")) is not None and _num(row.get("mae")) is not None


def _mfe_bucket(mfe: float | None) -> str:
    if mfe is None:
        return "NO_PROGRESS_UNKNOWN"
    if mfe < 0.25:
        return "NO_PROGRESS_DEAD"
    if mfe < 0.75:
        return "NO_PROGRESS_WEAK"
    if mfe < 1.0:
        return "NO_PROGRESS_ALMOST"
    return "NO_PROGRESS_MISMANAGED"


def _data_quality(row: dict[str, Any], require_mfe_mae_for_closed: bool = False) -> tuple[str, str]:
    gaps = _num(row.get("data_gap_events"))
    if gaps is None:
        gaps = 0.0
    closed_like = any(
        _is_true(row.get(k))
        for k in [
            "primary_tp_hit",
            "real_stop_loss_hit",
            "breakeven_stop_hit",
            "runner_breakeven_stop_hit",
            "time_stop_exit",
            "no_progress_exit",
            "mfe_stall_exit",
        ]
    )
    if require_mfe_mae_for_closed and closed_like and not _has_mfe_mae(row):
        return "DATA_BAD", "closed_record_missing_mfe_mae"
    if gaps <= 1:
        return "DATA_OK", "data_gap_events_0_to_1"
    if gaps <= 5:
        return "DATA_WARNING", "data_gap_events_2_to_5"
    return "DATA_BAD", "data_gap_events_gt_5"


def _zone(row: dict[str, Any]) -> str:
    if _is_true(row.get("weekend")):
        return "WEEKEND"
    if _is_true(row.get("killzone")):
        return "KILLZONE"
    mode = _safe_key(row.get("operating_mode")).lower()
    if "killzone" in mode:
        return "KILLZONE"
    if "weekend" in mode or "fin de semana" in mode:
        return "WEEKEND"
    if mode not in {"unknown", "none", ""}:
        return "OUT_OF_KILLZONE"
    return "UNKNOWN"


def _outcome_label(row: dict[str, Any]) -> str:
    if _is_true(row.get("primary_tp_hit")):
        return "primary_tp_hit"
    if _is_true(row.get("real_stop_loss_hit")):
        return "real_stop_loss_hit"
    if _is_true(row.get("no_progress_exit")):
        return "no_progress_exit"
    if _is_true(row.get("breakeven_stop_hit")):
        return "breakeven_stop_hit"
    if _is_true(row.get("runner_breakeven_stop_hit")):
        return "runner_breakeven_stop_hit"
    if _is_true(row.get("time_stop_exit")):
        return "time_stop_exit"
    if _is_true(row.get("mfe_stall_exit")):
        return "mfe_stall_exit"
    if _is_true(row.get("cancelled")):
        return "cancelled_or_expired"
    return "other_or_open"


def _profit_factor(rows: list[dict[str, Any]], r_key: str = "net_r") -> dict[str, Any]:
    values = [_num(row.get(r_key)) for row in rows]
    clean = [v for v in values if v is not None]
    wins = [v for v in clean if v > 0]
    losses = [v for v in clean if v < 0]
    gross_profit = round(sum(wins), 6)
    gross_loss = round(sum(losses), 6)
    if not clean:
        pf: float | str | None = None
        status = "insufficient_sample"
    elif gross_loss == 0 and gross_profit > 0:
        pf = "no_losses"
        status = "profitable_no_losses_in_sample"
    elif gross_loss == 0 and gross_profit == 0:
        pf = None
        status = "no_profit_no_loss"
    else:
        pf = round(gross_profit / abs(gross_loss), 6)
        status = "losing_segment" if isinstance(pf, float) and pf < 1 else "profitable_segment"
    return {
        "count": len(rows),
        "r_values_count": len(clean),
        "positive_count": len(wins),
        "negative_count": len(losses),
        "gross_profit_r": gross_profit,
        "gross_loss_r": gross_loss,
        "profit_factor": pf,
        "status": status,
        "avg_r": _avg(clean),
    }


def _group_pf(rows: list[dict[str, Any]], key_func, limit: int = 30) -> dict[str, Any]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[_safe_key(key_func(row))].append(row)
    ordered = sorted(buckets.items(), key=lambda kv: len(kv[1]), reverse=True)[:limit]
    return {key: _profit_factor(items) for key, items in ordered}


def _near_miss_rows(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    interesting_reasons = {
        "near_miss_chop",
        "ofa_shadow_reclaim_blocked",
        "ofa_shadow_sweep_blocked",
        "rvol_low",
        "atr_extension_high",
        "adx_low",
        "live_guard:ofa_live_rvol_too_low",
        "live_guard:ofa_live_regime_blocked",
        "live_guard:ofa_live_symbol_not_allowed",
        "copyability_rr_degraded",
    }
    rows = []
    for row in facts:
        if row.get("record_type") != "candidate":
            continue
        reason = _safe_key(row.get("blocked_reason"))
        if _is_true(row.get("near_miss")) or _is_true(row.get("would_send_signal")) or reason in interesting_reasons:
            rows.append(row)
    return rows


def _usability(row: dict[str, Any]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if not _safe_key(row.get("side")) or _safe_key(row.get("side")) == "unknown":
        reasons.append("NO_SIDE")
    if not _has_entry(row):
        reasons.append("NO_ENTRY")
    if not _has_tp_sl(row):
        reasons.append("NO_TP_SL")
    if not _has_geometry(row):
        reasons.append("NO_GEOMETRY")
    if not _has_mfe_mae(row):
        reasons.append("NO_MFE_MAE")
    if _safe_key(row.get("hypothetical_result")) in {"unknown", "None", "none"}:
        reasons.append("NO_RESULT")
    if reasons:
        return "NON_EVALUABLE", sorted(set(reasons))
    return "EVALUABLE", ["EVALUABLE"]


def no_progress_diagnostics_v2(facts: list[dict[str, Any]]) -> dict[str, Any]:
    signals = [row for row in facts if row.get("record_type") == "signal"]
    sent = [row for row in signals if _is_true(row.get("sent_to_telegram"))]
    rows = [row for row in signals if _is_true(row.get("no_progress_exit"))]
    with_mfe_mae = [row for row in rows if _has_mfe_mae(row)]
    missing_mfe_mae = [row for row in rows if not _has_mfe_mae(row)]
    bucket_counts = Counter(_mfe_bucket(_num(row.get("mfe"))) for row in rows)
    by_zone = defaultdict(list)
    for row in rows:
        by_zone[_zone(row)].append(row)
    return {
        "total_no_progress": len(rows),
        "sent_no_progress": sum(1 for row in rows if _is_true(row.get("sent_to_telegram"))),
        "no_progress_rate_over_sent": round(len(rows) / max(1, len(sent)), 6),
        "mfe_mae_available": len(with_mfe_mae),
        "mfe_mae_missing": len(missing_mfe_mae),
        "mfe_mae_available_rate": round(len(with_mfe_mae) / max(1, len(rows)), 6),
        "avg_mfe": _avg([_num(row.get("mfe")) for row in rows]),
        "avg_mae": _avg([_num(row.get("mae")) for row in rows]),
        "avg_net_r": _avg([_num(row.get("net_r")) for row in rows]),
        "avg_data_gap_events": _avg([_num(row.get("data_gap_events")) for row in rows]),
        "bucket_counts": dict(bucket_counts.most_common()),
        "by_symbol": _counter(rows, "symbol", 20),
        "by_side": _counter(rows, "side", 10),
        "by_setup_type": _counter(rows, "setup_type", 10),
        "by_market_regime": _counter(rows, "market_regime", 10),
        "by_operating_mode": _counter(rows, "operating_mode", 10),
        "by_zone": {zone: {"count": len(items), "avg_net_r": _avg([_num(r.get("net_r")) for r in items]), "bucket_counts": dict(Counter(_mfe_bucket(_num(r.get("mfe"))) for r in items).most_common())} for zone, items in by_zone.items()},
        "sample_missing_mfe_mae": [
            {"signal_id": r.get("signal_id"), "symbol": r.get("symbol"), "side": r.get("side"), "zone": _zone(r), "exit_reason": r.get("exit_reason"), "data_gap_events": r.get("data_gap_events"), "net_r": r.get("net_r")}
            for r in missing_mfe_mae[:40]
        ],
    }


def near_miss_usability_summary(facts: list[dict[str, Any]]) -> dict[str, Any]:
    rows = _near_miss_rows(facts)
    labels = []
    reason_counter: Counter[str] = Counter()
    by_blocked_reason: dict[str, Counter[str]] = defaultdict(Counter)
    by_blocked_reason_detail: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        label, reasons = _usability(row)
        labels.append(label)
        blocked_reason = _safe_key(row.get("blocked_reason"))
        by_blocked_reason[blocked_reason][label] += 1
        for reason in reasons:
            reason_counter[reason] += 1
            by_blocked_reason_detail[blocked_reason][reason] += 1
    evaluable = labels.count("EVALUABLE")
    return {
        "near_miss_total": len(rows),
        "evaluable_total": evaluable,
        "non_evaluable_total": len(rows) - evaluable,
        "evaluable_rate": round(evaluable / max(1, len(rows)), 6),
        "usability_reason_counts": dict(reason_counter.most_common()),
        "evaluable_by_blocked_reason": {k: v.get("EVALUABLE", 0) for k, v in sorted(by_blocked_reason.items(), key=lambda kv: sum(kv[1].values()), reverse=True)},
        "non_evaluable_by_blocked_reason": {k: v.get("NON_EVALUABLE", 0) for k, v in sorted(by_blocked_reason.items(), key=lambda kv: sum(kv[1].values()), reverse=True)},
        "detail_by_blocked_reason": {k: dict(v.most_common()) for k, v in sorted(by_blocked_reason_detail.items(), key=lambda kv: sum(kv[1].values()), reverse=True)[:30]},
        "guardrail": "Only EVALUABLE near-misses should be used for calibration hypotheses.",
    }


def _reclaim_class(row: dict[str, Any]) -> tuple[str, str]:
    dq, dq_reason = _data_quality(row, require_mfe_mae_for_closed=False)
    if not _has_geometry(row) or not _has_mfe_mae(row):
        return "RECLAIM_BLOCKED_UNKNOWN", "missing_geometry_or_mfe_mae"
    result = _safe_key(row.get("hypothetical_result")).lower()
    mfe = _num(row.get("mfe"))
    mae = _num(row.get("mae"))
    if dq == "DATA_BAD":
        return "RECLAIM_BLOCKED_UNKNOWN", dq_reason
    if result == "won" and (mfe or 0) >= 1.0 and (mae is None or mae > -1.0):
        return "RECLAIM_BLOCKED_GOOD_CANDIDATE", "won_mfe_ge_1_mae_gt_minus_1"
    if result == "lost" and (mae is not None and mae <= -1.0) and (mfe is None or mfe < 0.75):
        return "RECLAIM_BLOCKED_BAD_CANDIDATE", "lost_mae_le_minus_1_mfe_lt_0_75"
    return "RECLAIM_BLOCKED_UNKNOWN", "mixed_or_borderline"


def ofa_reclaim_blocked_classifier(facts: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [row for row in _near_miss_rows(facts) if _safe_key(row.get("blocked_reason")) == "ofa_shadow_reclaim_blocked"]
    classified = []
    class_counter: Counter[str] = Counter()
    reason_counter: Counter[str] = Counter()
    for row in rows:
        klass, reason = _reclaim_class(row)
        class_counter[klass] += 1
        reason_counter[reason] += 1
        if klass != "RECLAIM_BLOCKED_UNKNOWN" or len(classified) < 80:
            classified.append({
                "candidate_id": row.get("candidate_id"),
                "symbol": row.get("symbol"),
                "side": row.get("side"),
                "zone": _zone(row),
                "operating_mode": row.get("operating_mode"),
                "classification": klass,
                "classification_reason": reason,
                "hypothetical_result": row.get("hypothetical_result"),
                "hypothetical_exit_reason": row.get("hypothetical_exit_reason"),
                "mfe": row.get("mfe"),
                "mae": row.get("mae"),
                "net_rr": row.get("net_rr"),
                "estimated_cost": row.get("estimated_cost"),
                "primary_tp_distance": row.get("primary_tp_distance"),
                "sl_distance": row.get("sl_distance"),
            })
    return {
        "total": len(rows),
        "class_counts": dict(class_counter.most_common()),
        "classification_reason_counts": dict(reason_counter.most_common()),
        "with_geometry": sum(1 for row in rows if _has_geometry(row)),
        "with_mfe_mae": sum(1 for row in rows if _has_mfe_mae(row)),
        "hypothetical_result_counts": _counter(rows, "hypothetical_result", 20),
        "by_zone": _counter([{**row, "zone": _zone(row)} for row in rows], "zone", 10),
        "by_symbol": _counter(rows, "symbol", 20),
        "classified_sample": classified[:120],
        "guardrail": "This classifier is observational only and must not relax reclaim filters automatically.",
    }


def data_quality_score_by_signal(facts: list[dict[str, Any]]) -> dict[str, Any]:
    signals = [row for row in facts if row.get("record_type") == "signal"]
    scored = []
    for row in signals:
        score, reason = _data_quality(row, require_mfe_mae_for_closed=True)
        scored.append({**row, "data_quality_score": score, "data_quality_reason": reason, "zone": _zone(row), "outcome": _outcome_label(row)})
    return {
        "score_counts": _counter(scored, "data_quality_score", 10),
        "reason_counts": _counter(scored, "data_quality_reason", 20),
        "by_outcome": {k: _counter(v, "data_quality_score", 10) for k, v in _bucket(scored, "outcome").items()},
        "by_zone": {k: _counter(v, "data_quality_score", 10) for k, v in _bucket(scored, "zone").items()},
        "by_symbol": {k: _counter(v, "data_quality_score", 10) for k, v in list(_bucket(scored, "symbol").items())[:20]},
        "sample_bad": [
            {"signal_id": r.get("signal_id"), "symbol": r.get("symbol"), "side": r.get("side"), "outcome": r.get("outcome"), "zone": r.get("zone"), "data_gap_events": r.get("data_gap_events"), "reason": r.get("data_quality_reason")}
            for r in scored if r.get("data_quality_score") == "DATA_BAD"
        ][:80],
    }


def _bucket(rows: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[_safe_key(row.get(key))].append(row)
    return dict(sorted(buckets.items(), key=lambda kv: len(kv[1]), reverse=True))


def zone_diagnostics(facts: list[dict[str, Any]]) -> dict[str, Any]:
    signals = [row for row in facts if row.get("record_type") == "signal"]
    candidates = [row for row in facts if row.get("record_type") == "candidate"]
    signal_buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    candidate_buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in signals:
        signal_buckets[_zone(row)].append(row)
    for row in candidates:
        candidate_buckets[_zone(row)].append(row)
    out: dict[str, Any] = {}
    for zone in sorted(set(signal_buckets) | set(candidate_buckets)):
        srows = signal_buckets.get(zone, [])
        crows = candidate_buckets.get(zone, [])
        sent = [r for r in srows if _is_true(r.get("sent_to_telegram"))]
        tp = sum(1 for r in srows if _is_true(r.get("primary_tp_hit")))
        sl = sum(1 for r in srows if _is_true(r.get("real_stop_loss_hit")))
        no_progress = sum(1 for r in srows if _is_true(r.get("no_progress_exit")))
        out[zone] = {
            "signals_total": len(srows),
            "sent_to_telegram": len(sent),
            "primary_tp_hit": tp,
            "real_stop_loss_hit": sl,
            "breakeven_stop_hit": sum(1 for r in srows if _is_true(r.get("breakeven_stop_hit"))),
            "time_stop_exit": sum(1 for r in srows if _is_true(r.get("time_stop_exit"))),
            "mfe_stall_exit": sum(1 for r in srows if _is_true(r.get("mfe_stall_exit"))),
            "no_progress_exit": no_progress,
            "no_progress_rate_over_sent": round(no_progress / max(1, len(sent)), 6),
            "win_rate_tp_vs_sl": round(tp / max(1, tp + sl), 6),
            "avg_net_r": _avg([_num(r.get("net_r")) for r in srows]),
            "profit_factor": _profit_factor(srows),
            "avg_data_gap_events": _avg([_num(r.get("data_gap_events")) for r in srows]),
            "data_quality_distribution": _counter([{**r, "dq": _data_quality(r, True)[0]} for r in srows], "dq", 10),
            "top_symbols": _counter(srows, "symbol", 10),
            "candidates_total": len(crows),
            "top_blocked_reasons": _counter(crows, "blocked_reason", 15),
            "reclaim_blocked_total": sum(1 for r in crows if _safe_key(r.get("blocked_reason")) == "ofa_shadow_reclaim_blocked"),
            "reclaim_blocked_evaluable": sum(1 for r in crows if _safe_key(r.get("blocked_reason")) == "ofa_shadow_reclaim_blocked" and _has_geometry(r) and _has_mfe_mae(r)),
        }
    return out


def profit_factor_diagnostics(facts: list[dict[str, Any]]) -> dict[str, Any]:
    signals = [row for row in facts if row.get("record_type") == "signal"]
    sent = [row for row in signals if _is_true(row.get("sent_to_telegram"))]
    official = [row for row in signals if _is_true(row.get("primary_tp_hit")) or _is_true(row.get("real_stop_loss_hit"))]
    scored = []
    for row in signals:
        dq, _ = _data_quality(row, require_mfe_mae_for_closed=True)
        scored.append({**row, "zone": _zone(row), "outcome": _outcome_label(row), "data_quality_score": dq})
    return {
        "global": _profit_factor(signals),
        "sent_only": _profit_factor(sent),
        "official_tp_vs_sl_only": _profit_factor(official),
        "excluding_data_bad": _profit_factor([row for row in scored if row.get("data_quality_score") != "DATA_BAD"]),
        "by_zone": _group_pf(scored, lambda r: r.get("zone")),
        "by_setup_type": _group_pf(scored, lambda r: r.get("setup_type")),
        "by_symbol": _group_pf(scored, lambda r: r.get("symbol")),
        "by_side": _group_pf(scored, lambda r: r.get("side")),
        "by_exit_reason": _group_pf(scored, lambda r: r.get("exit_reason")),
        "by_outcome": _group_pf(scored, lambda r: r.get("outcome")),
        "by_data_quality": _group_pf(scored, lambda r: r.get("data_quality_score")),
        "interpretation": {
            "pf_lt_1": "Segment losing in the observed sample.",
            "pf_eq_1": "Break-even before practical frictions.",
            "pf_gt_1": "Segment profitable in the observed sample.",
            "guardrail": "Do not change thresholds from a single-day profit factor.",
        },
    }


def telegram_notified_consistency_check(facts: list[dict[str, Any]]) -> dict[str, Any]:
    signals = [row for row in facts if row.get("record_type") == "signal"]
    sent_count = sum(1 for row in signals if _is_true(row.get("sent_to_telegram")))
    notified_non_null = [row for row in signals if row.get("telegram_notified") is not None]
    notified_true = sum(1 for row in signals if _is_true(row.get("telegram_notified")))
    mismatches = [row for row in signals if _is_true(row.get("sent_to_telegram")) and not _is_true(row.get("telegram_notified"))]
    
    # Determine telegram_notified reliability status
    telegram_notified_status = "legacy_or_unreliable" if notified_true == 0 and sent_count > 0 else "ok"
    
    return {
        "source_of_truth": "sent_to_telegram",
        "sent_to_telegram_count": sent_count,
        "telegram_notified_true_count": notified_true,
        "telegram_notified_non_null_count": len(notified_non_null),
        "telegram_notified_status": telegram_notified_status,
        "mismatch_sent_true_notified_false": len(mismatches),
        "recommended_sent_metric": "sent_to_telegram",
        "likely_interpretation": "telegram_notified is not reliable in this window. It is derived from OFA funnel metadata (ofa_funnel.telegram_notified), a separate source from the lifecycle event stream. sent_to_telegram is driven by NOTIFIED events and is the authoritative source of truth for delivery metrics.",
        "note": "telegram_notified marked as legacy_or_unreliable because notified_true_count == 0 while sent_to_telegram > 0. Use sent_to_telegram for all delivery-rate, win-rate, PF, and other observability denominators.",
        "sample_mismatches": [{"signal_id": r.get("signal_id"), "symbol": r.get("symbol"), "status": r.get("status"), "sent_to_telegram": r.get("sent_to_telegram"), "telegram_notified": r.get("telegram_notified")} for r in mismatches[:40]],
    }


def compute_t02_diagnostics(facts: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "t02_name": "AI Reporter T02 - No-Progress, Reclaim, Zone & Profit Factor Diagnostics Upgrade",
        "mode": "read_only_observational_only",
        "no_progress_diagnostics_v2": no_progress_diagnostics_v2(facts),
        "near_miss_usability_summary": near_miss_usability_summary(facts),
        "ofa_reclaim_blocked_classifier": ofa_reclaim_blocked_classifier(facts),
        "data_quality_score_by_signal": data_quality_score_by_signal(facts),
        "zone_diagnostics": zone_diagnostics(facts),
        "profit_factor_diagnostics": profit_factor_diagnostics(facts),
        "telegram_notified_consistency_check": telegram_notified_consistency_check(facts),
        "guardrails": [
            "Do not modify BotVIP principal from this report.",
            "Do not change thresholds from a single-day sample.",
            "Do not convert near-misses into visible signals automatically.",
            "Use zone and profit factor diagnostics for observation and hypothesis design only.",
        ],
    }


def write_t02_diagnostics(diagnostics: dict[str, Any], path: str | Path) -> None:
    selected = Path(path)
    selected.parent.mkdir(parents=True, exist_ok=True)
    selected.write_text(json.dumps(diagnostics, indent=2, ensure_ascii=False, default=str), encoding="utf-8", newline="\n")
