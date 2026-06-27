"""F5_T12 Strategy Change Readiness Digest -- CORRECTED.

Compact AI-ready digest that summarizes whether Bot F5_T12 changes are justified.
Read-only/dashboard-local analytics only. Does not read/write the BotVIP DB,
send Telegram, modify strategy, thresholds, scanner runtime, lifecycle runtime,
Telegram runtime, TP/SL, DB schema, allowlist, or real trading.

Reuses already-generated JSON sections; does not recalculate everything from
scratch. All outputs are < 95,000 characters.

CORRECTIONS applied (F5_T12 Plan):
  - PF core reads from T02 profit_factor_diagnostics.sent_only
  - No-progress top_symbols reads segments.by_symbol.{symbol}.count (real counts)
  - Data quality separates scopes: raw T02, recovered F5_T09, official_signal_scope,
    facts/candidate scope
  - Added source_of_truth_note
  - Added digest_consistency_checks (C01-C10)
  - All outputs deterministically ordered, compact, parseable
"""
from __future__ import annotations

import json
from typing import Any

F5_T12_READINESS_JSON_FILENAME = "29_f5_t12_strategy_change_readiness.json"
F5_T12_READINESS_MD_FILENAME = "29_f5_t12_strategy_change_readiness.md"
F5_T12_READINESS_SCHEMA_VERSION = "f5_t12_strategy_change_readiness_v1"
MAX_DIGEST_CHARS = 95000
MAX_TOP_LOSSES = 5
MAX_GUARDS = 10
MAX_SYMBOLS = 10


# ---------------------------------------------------------------------------
# Safe helpers
# ---------------------------------------------------------------------------


def _safe_get(data: Any, path: list[Any], default: Any = None) -> Any:
    cur = data
    for key in path:
        if isinstance(cur, dict):
            cur = cur.get(key, default)
        elif isinstance(cur, list) and isinstance(key, int) and 0 <= key < len(cur):
            cur = cur[key]
        else:
            return default
    return cur


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        v = float(value)
        import math
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    except (TypeError, ValueError):
        return None


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str))


def _limit(value: Any, *, max_items: int = 8, depth: int = 2) -> Any:
    """Recursively limit list/dict sizes to keep output compact."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if depth <= 0:
        return {"_truncated": True, "_type": type(value).__name__, "_size_chars": _json_size(value)}
    if isinstance(value, list):
        return [_limit(item, max_items=max_items, depth=depth - 1) for item in value[:max_items]]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key in sorted(value.keys())[:max_items]:
            out[str(key)] = _limit(value.get(key), max_items=max_items, depth=depth - 1)
        if len(value) > max_items:
            out["_omitted_keys"] = len(value) - max_items
        return out
    return str(value)


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------


def _build_denominators(
    lifecycle: dict[str, Any],
    facts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Extract official denominators from lifecycle metrics."""
    signals_total = lifecycle.get("signals_total", 0)
    sent_to_telegram = lifecycle.get("sent_to_telegram", 0)
    candidates_total = lifecycle.get("candidates_total", 0)
    events_total = lifecycle.get("events_total", 0)
    facts_total = len(facts) if facts else lifecycle.get("facts_total", 0)

    return {
        "official_signals": signals_total,
        "sent_to_telegram": sent_to_telegram,
        "candidates": candidates_total,
        "events": events_total,
        "facts": facts_total,
        "note": "Denominators from lifecycle reconciliation. Do not double-count derived rows.",
    }


def _build_pf_core(t02_diagnostics: dict[str, Any]) -> dict[str, Any]:
    """Extract core Profit Factor metrics from T02 diagnostics.

    CORRECTED: reads profit_factor_diagnostics.sent_only and
    profit_factor_diagnostics.all_signals directly (not sent_signals.profit_factor_stats).
    """
    pf_data = _as_dict(t02_diagnostics.get("profit_factor_diagnostics", t02_diagnostics))

    sent_only = _as_dict(pf_data.get("sent_only", {}))
    all_pf = _as_dict(pf_data.get("all_signals", {}))

    return {
        "sent_only": {
            "count": sent_only.get("count", 0),
            "r_values_count": sent_only.get("r_values_count", 0),
            "gross_profit_r": sent_only.get("gross_profit_r", 0),
            "gross_loss_r": sent_only.get("gross_loss_r", 0),
            "avg_r": sent_only.get("avg_r"),
            "profit_factor": sent_only.get("profit_factor"),
            "status": sent_only.get("status"),
        },
        "all_signals": {
            "count": all_pf.get("count", 0),
            "r_values_count": all_pf.get("r_values_count", 0),
            "gross_profit_r": all_pf.get("gross_profit_r", 0),
            "gross_loss_r": all_pf.get("gross_loss_r", 0),
            "avg_r": all_pf.get("avg_r"),
            "profit_factor": all_pf.get("profit_factor"),
            "status": all_pf.get("status"),
        },
        "fallback_path_tried": [
            "t02_diagnostics.profit_factor_diagnostics.{sent_only,all_signals}",
        ],
        "note": "PF core from T02 diagnostics. sent_only = signals sent to Telegram. all_signals = all official signals.",
    }

def _build_loss_top(loss_contribution: dict[str, Any]) -> dict[str, Any]:
    """Extract top 5 loss contributors by outcome, symbol, side, zone."""
    by_dimension = _as_dict(loss_contribution.get("by_dimension", {}))
    top_losses: dict[str, list[dict[str, Any]]] = {}

    for dim in ("outcome", "symbol", "side", "zone"):
        dim_data = _as_dict(by_dimension.get(dim, {}))
        top_segments = _as_list(dim_data.get("top_loss_segments", []))
        top_losses[dim] = [
            {
                "segment": seg.get("segment"),
                "count": seg.get("count"),
                "gross_loss_abs_r": seg.get("gross_loss_abs_r"),
                "avg_r": seg.get("avg_r"),
                "loss_contribution_pct": seg.get("loss_contribution_pct"),
            }
            for seg in top_segments[:MAX_TOP_LOSSES]
        ]

    return {
        "total_loss_abs_r": loss_contribution.get("total_loss_abs_r"),
        "total_net_r": loss_contribution.get("total_net_r"),
        "official_signal_denominator": loss_contribution.get("official_signal_denominator"),
        "top_by_dimension": top_losses,
        "note": "Top loss contributors by dimension. Use for diagnostic prioritization only.",
    }


def _build_no_progress_core(no_progress_v3: dict[str, Any]) -> dict[str, Any]:
    """Extract no-progress core metrics from F5_T09b no_progress_root_cause_v3.

    CORRECTED: reads segments.by_symbol.{symbol}.count (actual count from
    F5_T09b's _segment function). Previously used sample_size/rows which
    returned 0.
    """
    segments = _as_dict(no_progress_v3.get("segments", {}))
    by_symbol = _as_dict(segments.get("by_symbol", {}))

    # Top symbols by count - _segment() returns items with 'count' key
    ranked_symbols = sorted(
        by_symbol.items(),
        key=lambda item: _safe_get(item[1], ["count"], 0) or 0,
        reverse=True,
    )

    return {
        "official_signal_denominator": no_progress_v3.get("official_signal_denominator"),
        "official_no_progress_count": no_progress_v3.get("official_no_progress_count"),
        "bucket_counts": _limit(no_progress_v3.get("bucket_counts", {}), max_items=12, depth=2),
        "avg_r": no_progress_v3.get("avg_r"),
        "top_symbols": [
            {
                "symbol": sym,
                "count": _safe_get(data, ["count"], 0),
                "avg_exit_r": data.get("avg_exit_r"),
                "avg_mfe_r": data.get("avg_mfe_r"),
                "avg_mae_r": data.get("avg_mae_r"),
            }
            for sym, data in ranked_symbols[:MAX_SYMBOLS]
        ],
        "note": "No-progress root cause core metrics. Full breakdown in 20_no_progress_root_cause_v3.json.",
    }


def _build_risk_context_candidates(
    guard_matrix: dict[str, Any],
    lifecycle_reconciliation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Count cases that would have been blocked by F5_T12 Risk Context Gate rules."""
    matched_guard_rows = guard_matrix.get("matched_guard_rows", 0)
    candidate_denominator = guard_matrix.get("candidate_shadow_denominator", 0)

    matrix = _as_dict(guard_matrix.get("matrix_by_guard", {}))
    guard_summaries: list[dict[str, Any]] = []
    for guard_name, guard_data in sorted(matrix.items()):
        guard_d = _as_dict(guard_data)
        guard_summaries.append({
            "guard": guard_name,
            "rows": guard_d.get("rows", 0),
            "avoided_losses_r": guard_d.get("avoided_losses_r"),
            "missed_winners_r": guard_d.get("missed_winners_r"),
            "net_guard_value_r": guard_d.get("net_guard_value_r"),
        })

    return {
        "candidate_shadow_denominator": candidate_denominator,
        "matched_guard_rows": matched_guard_rows,
        "guard_summaries": guard_summaries[:MAX_GUARDS],
        "note": "Risk Context Gate candidates that would have been blocked. Observational only.",
    }


def _build_guard_value(guard_matrix: dict[str, Any]) -> dict[str, Any]:
    """Extract top guards by net_guard_value (positive = avoided losses > missed winners)."""
    matrix = _as_dict(guard_matrix.get("matrix_by_guard", {}))
    guard_values: list[dict[str, Any]] = []

    for guard_name, guard_data in matrix.items():
        guard_d = _as_dict(guard_data)
        net_value = _num(guard_d.get("net_guard_value_r"))
        if net_value is not None:
            guard_values.append({
                "guard": guard_name,
                "rows": guard_d.get("rows", 0),
                "avoided_losses_r": guard_d.get("avoided_losses_r"),
                "missed_winners_r": guard_d.get("missed_winners_r"),
                "net_guard_value_r": net_value,
            })

    guard_values.sort(key=lambda g: -(g["net_guard_value_r"]))
    positive = [g for g in guard_values if g["net_guard_value_r"] > 0][:MAX_GUARDS]
    negative = [g for g in guard_values if g["net_guard_value_r"] < 0][:MAX_GUARDS]

    return {
        "positive_net_value": positive,
        "negative_net_value": negative,
        "note": "Positive net_guard_value = avoided losses exceed missed winners. Negative = guard may be too restrictive.",
    }

def _build_data_quality(
    t02_diagnostics: dict[str, Any],
    no_progress_v3: dict[str, Any],
    loss_contribution: dict[str, Any],
) -> dict[str, Any]:
    """Extract data quality indicators with separated scopes."""
    dq_by_signal = _as_dict(t02_diagnostics.get("data_quality_score_by_signal", {}))
    raw_t02 = {
        "score_counts": dq_by_signal.get("score_counts", {}),
        "reason_counts": dq_by_signal.get("reason_counts", {}),
    }

    mfe_mae = _as_dict(no_progress_v3.get("mfe_mae_recovery", {}))
    recovered_f5_t09 = {
        "mfe_known": mfe_mae.get("mfe_known"),
        "mae_known": mfe_mae.get("mae_known"),
        "missing_mfe_or_mae": mfe_mae.get("missing_mfe_or_mae"),
        "note": "MFE/MAE known from no_progress_root_cause_v3.mfe_mae_recovery.",
    }

    official_count = no_progress_v3.get("official_signal_denominator", 0)
    official_signal_scope = {
        "official_signal_denominator": official_count,
        "no_progress_count": no_progress_v3.get("official_no_progress_count"),
    }

    by_dim = _as_dict(loss_contribution.get("by_dimension", {}))
    data_gap_dim = _as_dict(by_dim.get("data_gap_bucket", {}))
    data_gap_segments = _as_list(data_gap_dim.get("segments", []))

    warnings: list[str] = []
    mfe_known = mfe_mae.get("mfe_known")
    mae_known = mfe_mae.get("mae_known")
    if official_count > 0 and mfe_known is not None and mfe_known < official_count * 0.5:
        warnings.append(f"MFE known rate is low ({mfe_known}/{official_count}).")
    if official_count > 0 and mae_known is not None and mae_known < official_count * 0.5:
        warnings.append(f"MAE known rate is low ({mae_known}/{official_count}).")

    return {
        "raw_t02_data_quality_score_by_signal": raw_t02,
        "recovered_f5_t09_mfe_mae_recovery": recovered_f5_t09,
        "official_signal_scope": official_signal_scope,
        "facts_candidate_scope_data_gaps": _limit(data_gap_segments, max_items=6, depth=2),
        "confidence_warnings": warnings,
        "note": "Data quality separated by scope: raw T02, recovered F5_T09, official signals, facts/candidates.",
    }


def _build_source_of_truth_note() -> dict[str, Any]:
    """Source-of-truth note identifying which generated sections feed this digest."""
    return {
        "denominators_source": "lifecycle reconciliation (entity_scope_reconciliation, report_manifest rows, lifecycle summary)",
        "pf_core_source": "t02_diagnostics.profit_factor_diagnostics.{sent_only, all_signals}",
        "loss_top_source": "f5_t04e_loss_contribution.by_dimension",
        "no_progress_core_source": "f5_t09bc_no_progress_root_cause_v3.segments.by_symbol",
        "risk_context_candidates_source": "f5_t09dfghi_guard_shadow_outcome_matrix",
        "guard_value_source": "f5_t09dfghi_guard_shadow_outcome_matrix.matrix_by_guard",
        "data_quality_raw_source": "t02_diagnostics.data_quality_score_by_signal",
        "data_quality_recovered_source": "f5_t09bc_no_progress_root_cause_v3.mfe_mae_recovery",
        "note": "This digest is observational-only. All sources are pre-computed JSON sections.",
    }

def _build_digest_consistency_checks(
    denominators: dict[str, Any],
    pf_core: dict[str, Any],
    no_progress_core: dict[str, Any],
) -> dict[str, Any]:
    """Run consistency checks C01-C10."""
    checks: list[dict[str, Any]] = []

    os_val = denominators.get("official_signals", 0)
    checks.append({
        "id": "C01", "name": "official_signals > 0",
        "passed": bool(os_val and os_val > 0),
        "value": os_val, "detail": "" if os_val and os_val > 0 else "WARNING: official_signals is 0 or missing",
    })

    stt_val = denominators.get("sent_to_telegram", 0)
    checks.append({
        "id": "C02", "name": "sent_to_telegram > 0",
        "passed": bool(stt_val and stt_val > 0),
        "value": stt_val, "detail": "" if stt_val and stt_val > 0 else "WARNING: sent_to_telegram is 0 or missing",
    })

    sent_count = _safe_get(pf_core, ["sent_only", "count"], 0)
    checks.append({
        "id": "C03", "name": "pf_core.sent_only.count == sent_to_telegram",
        "passed": sent_count == stt_val,
        "value": {"sent_only_count": sent_count, "sent_to_telegram": stt_val},
        "detail": "" if sent_count == stt_val else f"WARNING: sent_only.count ({sent_count}) != sent_to_telegram ({stt_val})",
    })

    gross_loss = _safe_get(pf_core, ["sent_only", "gross_loss_r"], 0)
    pf_val = _safe_get(pf_core, ["sent_only", "profit_factor"])
    checks.append({
        "id": "C04", "name": "profit_factor is not null when gross_loss != 0",
        "passed": gross_loss == 0 or pf_val is not None,
        "value": {"gross_loss_r": gross_loss, "profit_factor": pf_val},
        "detail": "" if gross_loss == 0 or pf_val is not None else "WARNING: profit_factor is null but gross_loss_r is non-zero",
    })

    np_count = no_progress_core.get("official_no_progress_count") or 0
    top_syms = no_progress_core.get("top_symbols", [])
    sum_top = sum(s.get("count", 0) for s in top_syms)
    checks.append({
        "id": "C05", "name": "official_no_progress_count == sum(top_symbols counts)",
        "passed": np_count == sum_top if top_syms else True,
        "value": {"official_no_progress_count": np_count, "sum_top_symbols_counts": sum_top, "top_symbols_count": len(top_syms)},
        "detail": "" if np_count == sum_top else f"WARNING: np_count ({np_count}) != sum_top ({sum_top})",
    })

    events_val = denominators.get("events", 0)
    checks.append({
        "id": "C06", "name": "events > 0",
        "passed": bool(events_val and events_val > 0),
        "value": events_val, "detail": "" if events_val and events_val > 0 else "WARNING: events is 0 or missing",
    })

    checks.append({
        "id": "C07", "name": "no generated JSON > 95000 chars",
        "passed": True, "value": MAX_DIGEST_CHARS,
        "detail": "Enforced at build time via MAX_DIGEST_CHARS guard",
    })
    checks.append({
        "id": "C08", "name": "JSON parseable",
        "passed": True, "value": None,
        "detail": "Enforced at build time via json.dumps round-trip",
    })
    checks.append({
        "id": "C09", "name": "read_only == true",
        "passed": True, "value": True,
        "detail": "read_only is hardcoded to True in the digest schema",
    })
    checks.append({
        "id": "C10", "name": "source notes populated",
        "passed": True, "value": None,
        "detail": "source_of_truth_note section is populated if present in output",
    })

    return {
        "consistency_checks": checks,
        "summary": {
            "total": len(checks),
            "passed": sum(1 for c in checks if c["passed"]),
            "failed": sum(1 for c in checks if not c["passed"]),
        },
        "note": "Consistency checks validate internal consistency. Warnings are informational.",
    }




def _build_digest_consistency_checks(
    denominators: dict[str, Any],
    pf_core: dict[str, Any],
    no_progress_core: dict[str, Any],
) -> dict[str, Any]:
    """Run consistency checks C01-C10."""
    checks: list[dict[str, Any]] = []

    os_val = denominators.get("official_signals", 0)
    checks.append({
        "id": "C01", "name": "official_signals > 0",
        "passed": bool(os_val and os_val > 0),
        "value": os_val, "detail": "" if os_val and os_val > 0 else "WARNING: official_signals is 0 or missing",
    })

    stt_val = denominators.get("sent_to_telegram", 0)
    checks.append({
        "id": "C02", "name": "sent_to_telegram > 0",
        "passed": bool(stt_val and stt_val > 0),
        "value": stt_val, "detail": "" if stt_val and stt_val > 0 else "WARNING: sent_to_telegram is 0 or missing",
    })

    sent_count = _safe_get(pf_core, ["sent_only", "count"], 0)
    checks.append({
        "id": "C03", "name": "pf_core.sent_only.count == sent_to_telegram",
        "passed": sent_count == stt_val,
        "value": {"sent_only_count": sent_count, "sent_to_telegram": stt_val},
        "detail": "" if sent_count == stt_val else f"WARNING: sent_only.count ({sent_count}) != sent_to_telegram ({stt_val})",
    })

    gross_loss = _safe_get(pf_core, ["sent_only", "gross_loss_r"], 0)
    pf_val = _safe_get(pf_core, ["sent_only", "profit_factor"])
    checks.append({
        "id": "C04", "name": "profit_factor is not null when gross_loss != 0",
        "passed": gross_loss == 0 or pf_val is not None,
        "value": {"gross_loss_r": gross_loss, "profit_factor": pf_val},
        "detail": "" if gross_loss == 0 or pf_val is not None else "WARNING: profit_factor is null but gross_loss_r is non-zero",
    })

    np_count = no_progress_core.get("official_no_progress_count") or 0
    top_syms = no_progress_core.get("top_symbols", [])
    sum_top = sum(s.get("count", 0) for s in top_syms)
    checks.append({
        "id": "C05", "name": "official_no_progress_count == sum(top_symbols counts)",
        "passed": np_count == sum_top if top_syms else True,
        "value": {"official_no_progress_count": np_count, "sum_top_symbols_counts": sum_top, "top_symbols_count": len(top_syms)},
        "detail": "" if np_count == sum_top else f"WARNING: np_count ({np_count}) != sum_top ({sum_top})",
    })

    events_val = denominators.get("events", 0)
    checks.append({
        "id": "C06", "name": "events > 0",
        "passed": bool(events_val and events_val > 0),
        "value": events_val, "detail": "" if events_val and events_val > 0 else "WARNING: events is 0 or missing",
    })

    checks.append({
        "id": "C07", "name": "no generated JSON > 95000 chars",
        "passed": True, "value": MAX_DIGEST_CHARS,
        "detail": "Enforced at build time via MAX_DIGEST_CHARS guard",
    })
    checks.append({
        "id": "C08", "name": "JSON parseable",
        "passed": True, "value": None,
        "detail": "Enforced at build time via json.dumps round-trip",
    })
    checks.append({
        "id": "C09", "name": "read_only == true",
        "passed": True, "value": True,
        "detail": "read_only is hardcoded to True in the digest schema",
    })
    checks.append({
        "id": "C10", "name": "source notes populated",
        "passed": True, "value": None,
        "detail": "source_of_truth_note section is populated if present in output",
    })

    return {
        "consistency_checks": checks,
        "summary": {
            "total": len(checks),
            "passed": sum(1 for c in checks if c["passed"]),
            "failed": sum(1 for c in checks if not c["passed"]),
        },
        "note": "Consistency checks validate internal consistency. Warnings are informational.",
    }


def _build_human_checklist() -> dict[str, Any]:
    """Generate a human-readable checklist for deploy/flag change review."""
    return {
        "checklist": [
            {"id": "C01", "category": "denominators", "check": "Verify official signal count matches expected window volume.",
             "why": "Low volume may produce unreliable PF and loss contribution metrics."},
            {"id": "C02", "category": "profit_factor", "check": "Confirm PF > 1.0 for sent signals.",
             "why": "Negative PF indicates the current strategy is losing R over the window."},
            {"id": "C03", "category": "loss_contribution", "check": "Review top loss contributors.",
             "why": "Concentrated losses may indicate a systemic issue rather than random variance."},
            {"id": "C04", "category": "no_progress", "check": "Check no-progress rate and top symbols.",
             "why": "High no-progress rate may indicate entry timing or filter issues."},
            {"id": "C05", "category": "risk_context_gate", "check": "Review guard net values.",
             "why": "Negative net guard value suggests the guard may be too restrictive."},
            {"id": "C06", "category": "data_quality", "check": "Check MFE/MAE known rates above 50%.",
             "why": "Low known rates reduce confidence in MFE/MAE-based diagnostics."},
            {"id": "C07", "category": "data_quality", "check": "Review data gap buckets.",
             "why": "High data gaps may indicate connectivity or logging issues."},
            {"id": "C08", "category": "deploy_readiness", "check": "Compare 24h before/after metrics.",
             "why": "Single-window metrics are weak evidence."},
            {"id": "C09", "category": "deploy_readiness", "check": "Confirm all JSON files in ZIP parse and are < 95,000 chars.",
             "why": "Oversized files may be truncated by Telegram or AI context limits."},
            {"id": "C10", "category": "deploy_readiness", "check": "Verify dashboard is read-only.",
             "why": "Dashboard must never modify bot runtime or strategy."},
        ],
        "note": "Review this checklist before deploying F5_T12 changes.",
    }


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------


def build_f5_t12_strategy_readiness(
    *,
    lifecycle: dict[str, Any],
    facts: list[dict[str, Any]] | None = None,
    t02_diagnostics: dict[str, Any],
    loss_contribution: dict[str, Any],
    no_progress_v3: dict[str, Any],
    guard_matrix: dict[str, Any],
    lifecycle_reconciliation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the F5_T12 Strategy Change Readiness digest.

    CORRECTED: PF core reads T02 sent_only/all_signals directly,
    no-progress reads correct count keys, data quality scopes separated.
    """
    _denominators = _build_denominators(lifecycle, facts)
    _pf_core = _build_pf_core(t02_diagnostics)
    _no_progress_core = _build_no_progress_core(no_progress_v3)

    digest = {
        "schema_version": F5_T12_READINESS_SCHEMA_VERSION,
        "section": "f5_t12_strategy_change_readiness",
        "read_only": True,
        "mode": "shadow_observational_only",
        "purpose": "Compact digest to determine if F5_T12 Bot changes are justified.",
        "sections": {
            "denominators": _denominators,
            "pf_core": _pf_core,
            "loss_top": _build_loss_top(loss_contribution),
            "no_progress_core": _no_progress_core,
            "risk_context_candidates": _build_risk_context_candidates(guard_matrix, lifecycle_reconciliation),
            "guard_value": _build_guard_value(guard_matrix),
            "data_quality": _build_data_quality(t02_diagnostics, no_progress_v3, loss_contribution),
            "source_of_truth_note": _build_source_of_truth_note(),
            "digest_consistency_checks": _build_digest_consistency_checks(
                _denominators, _pf_core, _no_progress_core,
            ),
            "human_checklist": _build_human_checklist(),
        },
        "guardrails": [
            "Do not recommend real trading or live operation changes from this digest.",
            "Do not propose automatic threshold, TP/SL, no-progress timeout, MFE-stall, guard, or allowlist changes.",
            "Candidate snapshots and shadow diagnostics are not official trades.",
            "Single-window metrics are weak evidence. Require multi-window comparison before deploying changes.",
            "Full JSON evidence remains on the server report folder; request only targeted full sections if needed.",
        ],
    }

    # Enforce size limit
    json_str = json.dumps(digest, ensure_ascii=False, default=str)
    if len(json_str) > MAX_DIGEST_CHARS:
        for section_key in ("loss_top", "no_progress_core", "guard_value", "data_quality"):
            if section_key in digest.get("sections", {}):
                digest["sections"][section_key] = _limit(
                    digest["sections"][section_key],
                    max_items=4,
                    depth=2,
                )
        json_str = json.dumps(digest, ensure_ascii=False, default=str)

    if len(json_str) > MAX_DIGEST_CHARS:
        digest["sections"]["human_checklist"] = _limit(
            digest["sections"]["human_checklist"],
            max_items=5,
            depth=2,
        )
        json_str = json.dumps(digest, ensure_ascii=False, default=str)

    # Update consistency checks with actual values
    if "digest_consistency_checks" in digest.get("sections", {}):
        for c in digest["sections"]["digest_consistency_checks"].get("consistency_checks", []):
            if c["id"] == "C07":
                c["value"] = len(json_str)
                c["passed"] = len(json_str) <= MAX_DIGEST_CHARS
                c["detail"] = "" if len(json_str) <= MAX_DIGEST_CHARS else f"WARNING: JSON is {len(json_str)} chars, exceeds {MAX_DIGEST_CHARS}"
            if c["id"] == "C08":
                c["passed"] = True
                c["value"] = len(json_str)
                c["detail"] = "JSON valid and parseable"
            if c["id"] == "C09":
                c["passed"] = digest.get("read_only") is True
                c["value"] = digest.get("read_only")
            if c["id"] == "C10":
                c["passed"] = "source_of_truth_note" in digest.get("sections", {})
                c["value"] = "source_of_truth_note" in digest.get("sections", {})
        checks_summary = digest["sections"]["digest_consistency_checks"]["summary"]
        checks = digest["sections"]["digest_consistency_checks"]["consistency_checks"]
        checks_summary["passed"] = sum(1 for c in checks if c["passed"])
        checks_summary["failed"] = sum(1 for c in checks if not c["passed"])

    return {"json": digest, "markdown": _render_md(digest)}


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------


def _render_md(digest: dict[str, Any]) -> str:
    """Render the digest as a compact Markdown document."""
    lines = [
        "# F5_T12 Strategy Change Readiness (CORRECTED)",
        "",
        "Compact digest to determine if F5_T12 Bot changes are justified.",
        "Read-only observational analysis. Does not modify bot runtime.",
        "",
        "---",
        "",
        "## Denominators",
    ]

    denom = _safe_get(digest, ["sections", "denominators"], {})
    lines.append(f"- Official signals: {denom.get('official_signals')}")
    lines.append(f"- Sent to Telegram: {denom.get('sent_to_telegram')}")
    lines.append(f"- Candidates: {denom.get('candidates')}")
    lines.append(f"- Events: {denom.get('events')}")
    lines.append(f"- Facts: {denom.get('facts')}")

    lines.extend(["", "## PF Core (sent signals, CORRECTED)"])
    pf = _safe_get(digest, ["sections", "pf_core", "sent_only"], {})
    lines.append(f"- Count: {pf.get('count')}")
    lines.append(f"- R values count: {pf.get('r_values_count')}")
    lines.append(f"- Gross profit R: {pf.get('gross_profit_r')}")
    lines.append(f"- Gross loss R: {pf.get('gross_loss_r')}")
    lines.append(f"- Avg R: {pf.get('avg_r')}")
    lines.append(f"- Profit Factor: {pf.get('profit_factor')}")
    lines.append(f"- Status: {pf.get('status')}")

    lines.extend(["", "## PF Core (all signals)"])
    pf_all = _safe_get(digest, ["sections", "pf_core", "all_signals"], {})
    lines.append(f"- Count: {pf_all.get('count')}")
    lines.append(f"- R values count: {pf_all.get('r_values_count')}")
    lines.append(f"- Gross profit R: {pf_all.get('gross_profit_r')}")
    lines.append(f"- Gross loss R: {pf_all.get('gross_loss_r')}")
    lines.append(f"- Avg R: {pf_all.get('avg_r')}")
    lines.append(f"- Profit Factor: {pf_all.get('profit_factor')}")
    lines.append(f"- Status: {pf_all.get('status')}")


    lines.extend(["", "## Loss Top (top 5 by dimension)"])
    loss_top = _safe_get(digest, ["sections", "loss_top", "top_by_dimension"], {})
    for dim in ("outcome", "symbol", "side", "zone"):
        segs = loss_top.get(dim, [])
        if segs:
            lines.append(f"### By {dim}")
            for seg in segs[:3]:
                lines.append(
                    f"- {seg.get('segment')}: count={seg.get('count')}, "
                    f"loss={seg.get('gross_loss_abs_r')}R, "
                    f"contrib={seg.get('loss_contribution_pct')}"
                )

    lines.extend(["", "## No-Progress Core"])
    np = _safe_get(digest, ["sections", "no_progress_core"], {})
    lines.append(f"- Official no-progress count: {np.get('official_no_progress_count')}")
    lines.append(f"- Bucket counts: `{json.dumps(np.get('bucket_counts', {}), ensure_ascii=False)}`")
    top_syms = np.get("top_symbols", [])
    if top_syms:
        lines.append("- Top symbols (CORRECTED counts):")
        for sym in top_syms[:5]:
            lines.append(f"  - {sym.get('symbol')}: count={sym.get('count')}, avg_exit_r={sym.get('avg_exit_r')}")

    lines.extend(["", "## Risk Context Candidates"])
    rc = _safe_get(digest, ["sections", "risk_context_candidates"], {})
    lines.append(f"- Candidate shadow denominator: {rc.get('candidate_shadow_denominator')}")
    lines.append(f"- Matched guard rows: {rc.get('matched_guard_rows')}")
    guards = rc.get("guard_summaries", [])
    if guards:
        lines.append("- Guard summaries:")
        for g in guards[:5]:
            lines.append(
                f"  - {g.get('guard')}: rows={g.get('rows')}, "
                f"avoided_losses={g.get('avoided_losses_r')}R, "
                f"missed_winners={g.get('missed_winners_r')}R, "
                f"net={g.get('net_guard_value_r')}R"
            )

    lines.extend(["", "## Guard Value (top positive/negative)"])
    gv = _safe_get(digest, ["sections", "guard_value"], {})
    pos = gv.get("positive_net_value", [])
    neg = gv.get("negative_net_value", [])
    if pos:
        lines.append("### Positive net value (avoided losses > missed winners)")
        for g in pos[:3]:
            lines.append(f"  - {g.get('guard')}: net={g.get('net_guard_value_r')}R")
    if neg:
        lines.append("### Negative net value (guard may be too restrictive)")
        for g in neg[:3]:
            lines.append(f"  - {g.get('guard')}: net={g.get('net_guard_value_r')}R")

    lines.extend(["", "## Data Quality"])
    dq = _safe_get(digest, ["sections", "data_quality"], {})
    dq_recovered = dq.get("recovered_f5_t09_mfe_mae_recovery", {})
    lines.append(f"- MFE known (recovered): {dq_recovered.get('mfe_known')} / missing: {dq_recovered.get('missing_mfe_or_mae')}")
    lines.append(f"- MAE known (recovered): {dq_recovered.get('mae_known')}")
    warnings = dq.get("confidence_warnings", [])
    if warnings:
        lines.append("- Warnings:")
        for w in warnings:
            lines.append(f"  - {w}")


    lines.extend(["", "## Source of Truth Note"])
    sot = _safe_get(digest, ["sections", "source_of_truth_note"], {})
    for k, v in sot.items():
        if k != "note":
            lines.append(f"- {k}: {v}")
    lines.append(f"- Note: {sot.get('note', '')}")

    lines.extend(["", "## Consistency Checks (C01-C10)"])
    checks = _safe_get(digest, ["sections", "digest_consistency_checks", "consistency_checks"], [])
    chk_summary = _safe_get(digest, ["sections", "digest_consistency_checks", "summary"], {})
    lines.append(f"- Total: {chk_summary.get('total')}, Passed: {chk_summary.get('passed')}, Failed: {chk_summary.get('failed')}")
    for c in checks:
        icon = "PASS" if c.get("passed") else "FAIL"
        detail = c.get("detail", "")
        if detail:
            lines.append(f"  - [{icon}] {c.get('id')} {c.get('name')}: {detail}")
        else:
            lines.append(f"  - [{icon}] {c.get('id')} {c.get('name')}")

    lines.extend(["", "## Human Checklist"])
    checklist = _safe_get(digest, ["sections", "human_checklist", "checklist"], [])
    for item in checklist:
        lines.append(f"- [{item.get('id')}] {item.get('category').upper()}: {item.get('check')}")

    lines.extend([
        "",
        "---",
        "",
        "## Guardrails",
        "- Do not recommend real trading or live operation changes from this digest.",
        "- Do not propose automatic threshold, TP/SL, no-progress timeout, MFE-stall, guard, or allowlist changes.",
        "- Candidate snapshots and shadow diagnostics are not official trades.",
        "- Single-window metrics are weak evidence.",
        "- Full JSON evidence remains on the server report folder.",
        "",
    ])

    return "\n".join(lines)


