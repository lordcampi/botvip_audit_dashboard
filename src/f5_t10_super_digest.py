"""F5_T10 AI-ready Super Digest for F5_T09 diagnostics.

This module creates compact AI-review summaries from the full F5_T09 JSON
sections. Full JSON files remain generated on the server report folder for audit
and debugging, but the AI_REVIEW ZIP can include this digest instead of dozens
of chunked evidence files.

Read-only/dashboard-local analytics only. It does not read/write the BotVIP DB,
send Telegram, modify strategy, thresholds, scanner runtime, lifecycle runtime,
Telegram runtime, TP/SL, DB schema, allowlist, or real trading.
"""
from __future__ import annotations

import json
from typing import Any

F5_T10_DIGEST_JSON_FILENAME = "28_f5_t09_ai_super_digest.json"
F5_T10_DIGEST_MD_FILENAME = "28_f5_t09_ai_super_digest.md"
F5_T10_SCHEMA_VERSION = "f5_t10_f5_t09_ai_super_digest_v1"
MAX_EXAMPLES = 10
MAX_SEGMENTS = 12
MAX_GUARDS = 12


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


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str))


def _limit(value: Any, *, max_items: int = MAX_EXAMPLES, depth: int = 3) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if depth <= 0:
        return {"summary_only": True, "type": type(value).__name__, "size_chars": _json_size(value)}
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


def _segment_brief(section: dict[str, Any], path: list[Any], *, max_segments: int = MAX_SEGMENTS) -> dict[str, Any]:
    data = _as_dict(_safe_get(section, path, {}))
    out: dict[str, Any] = {}
    ranked = sorted(data.items(), key=lambda item: (_safe_get(item[1], ["sample_size"], _safe_get(item[1], ["rows"], _safe_get(item[1], ["count"], 0))) or 0), reverse=True)
    for name, payload in ranked[:max_segments]:
        payload_d = _as_dict(payload)
        out[str(name)] = {
            "sample_size": payload_d.get("sample_size") or payload_d.get("rows") or payload_d.get("count"),
            "r_values_count": payload_d.get("r_values_count"),
            "confidence": payload_d.get("confidence"),
            "avg_r": payload_d.get("avg_r") or payload_d.get("avg_exit_r"),
            "net_sum_r": payload_d.get("net_sum_r"),
            "avg_mfe_r": payload_d.get("avg_mfe_r"),
            "avg_mae_r": payload_d.get("avg_mae_r"),
            "profit_factor": _limit(payload_d.get("profit_factor") or payload_d.get("profit_factor_if_allowed"), max_items=8, depth=2),
            "alpha_score": payload_d.get("alpha_score"),
            "avoided_losses_r": payload_d.get("avoided_losses_r"),
            "missed_winners_r": payload_d.get("missed_winners_r"),
            "net_guard_value_r": payload_d.get("net_guard_value_r"),
            "outcome_counts": _limit(payload_d.get("outcome_counts"), max_items=8, depth=2),
        }
    return out


def _examples(section: dict[str, Any], paths: list[list[Any]], *, max_items: int = MAX_EXAMPLES) -> list[Any]:
    for path in paths:
        value = _safe_get(section, path)
        rows = _as_list(value)
        if rows:
            return _limit(rows, max_items=max_items, depth=3)
    return []


def _lifecycle_summary(section: dict[str, Any]) -> dict[str, Any]:
    summary = _as_dict(section.get("summary"))
    return {
        "schema_version": section.get("schema_version"),
        "signals_total": summary.get("signals_total"),
        "rows_available": summary.get("rows_available"),
        "official_result_counts": summary.get("official_result_counts", {}),
        "runner_result_counts": summary.get("runner_result_counts", {}),
        "final_public_result_counts": summary.get("final_public_result_counts", {}),
        "visual_contradiction_count": summary.get("visual_contradiction_count"),
        "top_visual_contradiction_examples": _limit([row for row in _as_list(section.get("rows")) if row.get("visual_contradiction")][:MAX_EXAMPLES], max_items=MAX_EXAMPLES, depth=3),
    }


def _no_progress_summary(section: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": section.get("schema_version"),
        "official_signal_denominator": section.get("official_signal_denominator"),
        "official_no_progress_count": section.get("official_no_progress_count"),
        "bucket_counts": section.get("bucket_counts", {}),
        "mfe_mae_recovery": _limit(section.get("mfe_mae_recovery"), max_items=12, depth=3),
        "top_loss_contributors": _examples(section, [["top_loss_contributors"], ["representative_examples"]]),
        "segments": {
            "by_symbol": _segment_brief(section, ["segments", "by_symbol"]),
            "by_market_regime": _segment_brief(section, ["segments", "by_market_regime"]),
            "by_zone": _segment_brief(section, ["segments", "by_zone"]),
            "by_net_r_bucket": _segment_brief(section, ["segments", "by_net_r_bucket"]),
        },
    }


def _mfe_capture_summary(section: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": section.get("schema_version"),
        "closed_rows_evaluated": section.get("closed_rows_evaluated"),
        "data_quality": _limit(section.get("data_quality"), max_items=12, depth=3),
        "mfe_capture_leak_examples": _examples(section, [["mfe_capture_leak_examples"], ["rows"]]),
        "segments": {
            "by_exit_reason": _segment_brief(section, ["segments", "by_exit_reason"]),
            "by_outcome_family": _segment_brief(section, ["segments", "by_outcome_family"]),
            "by_symbol": _segment_brief(section, ["segments", "by_symbol"]),
            "by_zone": _segment_brief(section, ["segments", "by_zone"]),
        },
    }


def _guard_summary(section: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": section.get("schema_version"),
        "candidate_shadow_denominator": section.get("candidate_shadow_denominator"),
        "matched_guard_rows": section.get("matched_guard_rows"),
        "target_guards": section.get("target_guards", []),
        "matrix_by_guard": _segment_brief(section, ["matrix_by_guard"], max_segments=MAX_GUARDS),
    }


def _low_vol_summary(section: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": section.get("schema_version"),
        "official_signals": {
            "denominator": _safe_get(section, ["official_signals", "denominator"]),
            "low_vol_rows": _safe_get(section, ["official_signals", "low_vol_rows"]),
            "by_outcome": _segment_brief(section, ["official_signals", "by_outcome"]),
            "by_reclaim_ok": _segment_brief(section, ["official_signals", "by_reclaim_ok"]),
        },
        "candidate_shadow": {
            "denominator": _safe_get(section, ["candidate_shadow", "denominator"]),
            "low_vol_rows": _safe_get(section, ["candidate_shadow", "low_vol_rows"]),
            "by_outcome": _segment_brief(section, ["candidate_shadow", "by_outcome"]),
            "by_guard_reason": _segment_brief(section, ["candidate_shadow", "by_guard_reason"]),
        },
    }


def _copyability_summary(section: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": section.get("schema_version"),
        "buckets": section.get("buckets", []),
        "official_signals": _segment_brief(section, ["official_signals"], max_segments=10),
        "candidate_shadow": _segment_brief(section, ["candidate_shadow"], max_segments=10),
    }


def _atr_summary(section: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": section.get("schema_version"),
        "candidate_shadow": {
            "rows_with_atr_extension": _safe_get(section, ["candidate_shadow", "rows_with_atr_extension"]),
            "by_side": _segment_brief(section, ["candidate_shadow", "by_side"]),
            "by_btc_bias_conflict": _segment_brief(section, ["candidate_shadow", "by_btc_bias_conflict"]),
            "by_market_regime": _segment_brief(section, ["candidate_shadow", "by_market_regime"]),
            "by_outcome": _segment_brief(section, ["candidate_shadow", "by_outcome"]),
        },
        "official_signals_reference": {
            "rows_with_atr_extension": _safe_get(section, ["official_signals_reference", "rows_with_atr_extension"]),
            "by_outcome": _segment_brief(section, ["official_signals_reference", "by_outcome"]),
        },
    }


def _btc_summary(section: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": section.get("schema_version"),
        "definition": section.get("definition"),
        "official_signals": {
            "conflict_rows": _safe_get(section, ["official_signals", "conflict_rows"]),
            "by_side": _segment_brief(section, ["official_signals", "by_side"]),
            "by_reclaim_ok": _segment_brief(section, ["official_signals", "by_reclaim_ok"]),
            "by_outcome": _segment_brief(section, ["official_signals", "by_outcome"]),
        },
        "candidate_shadow": {
            "conflict_rows": _safe_get(section, ["candidate_shadow", "conflict_rows"]),
            "by_side": _segment_brief(section, ["candidate_shadow", "by_side"]),
            "by_reclaim_ok": _segment_brief(section, ["candidate_shadow", "by_reclaim_ok"]),
            "by_guard_reason": _segment_brief(section, ["candidate_shadow", "by_guard_reason"]),
            "by_outcome": _segment_brief(section, ["candidate_shadow", "by_outcome"]),
        },
    }


def _symbol_alpha_summary(section: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": section.get("schema_version"),
        "target_symbols": section.get("target_symbols", []),
        "candidate_shadow_denominator": section.get("candidate_shadow_denominator"),
        "matched_rows": section.get("matched_rows"),
        "target_symbol_rows": section.get("target_symbol_rows"),
        "ranking": {
            "alpha_potential_symbols": _limit(_safe_get(section, ["ranking", "alpha_potential_symbols"], []), max_items=15, depth=3),
            "noisy_or_negative_symbols": _limit(_safe_get(section, ["ranking", "noisy_or_negative_symbols"], []), max_items=15, depth=3),
            "ranking_note": _safe_get(section, ["ranking", "ranking_note"]),
        },
        "segments": {
            "by_symbol": _segment_brief(section, ["segments", "by_symbol"], max_segments=20),
            "by_side": _segment_brief(section, ["segments", "by_side"]),
            "by_market_regime": _segment_brief(section, ["segments", "by_market_regime"]),
            "by_btc_bias_conflict": _segment_brief(section, ["segments", "by_btc_bias_conflict"]),
            "by_outcome": _segment_brief(section, ["segments", "by_outcome"]),
        },
    }


def _render_md(digest: dict[str, Any]) -> str:
    lines = [
        "# F5_T09 AI Super Digest",
        "",
        "AI-ready compact digest generated from full F5_T09 JSON diagnostics.",
        "",
        "## Guardrails",
    ]
    for item in digest.get("guardrails", []):
        lines.append(f"- {item}")
    lines.extend(["", "## Files policy", digest.get("files_policy", {}).get("summary", "")])

    lifecycle = digest.get("sections", {}).get("lifecycle_reconciliation", {})
    lines.extend([
        "", "## 1. Lifecycle official vs runner",
        f"- Signals total: {lifecycle.get('signals_total')}",
        f"- Visual contradictions: {lifecycle.get('visual_contradiction_count')}",
        f"- Official result counts: `{json.dumps(lifecycle.get('official_result_counts', {}), ensure_ascii=False)}`",
    ])

    no_progress = digest.get("sections", {}).get("no_progress_root_cause_v3", {})
    lines.extend([
        "", "## 2. No-progress root cause",
        f"- Official no-progress count: {no_progress.get('official_no_progress_count')}",
        f"- Bucket counts: `{json.dumps(no_progress.get('bucket_counts', {}), ensure_ascii=False)}`",
    ])

    mfe = digest.get("sections", {}).get("mfe_capture_efficiency", {})
    lines.extend([
        "", "## 3. MFE capture efficiency",
        f"- Closed rows evaluated: {mfe.get('closed_rows_evaluated')}",
        f"- Data quality: `{json.dumps(mfe.get('data_quality', {}), ensure_ascii=False, default=str)[:1200]}`",
    ])

    guards = digest.get("sections", {}).get("guard_shadow_outcome_matrix", {})
    lines.extend([
        "", "## 4. Guard shadow value",
        f"- Candidate denominator: {guards.get('candidate_shadow_denominator')}",
        f"- Matched guard rows: {guards.get('matched_guard_rows')}",
        "- See JSON for avoided_losses_r, missed_winners_r, net_guard_value_r by guard.",
    ])

    symbol = digest.get("sections", {}).get("symbol_not_allowed_shadow_alpha", {})
    lines.extend([
        "", "## 5. Symbol-not-allowed alpha",
        f"- Target symbols: {', '.join(symbol.get('target_symbols', []))}",
        f"- Matched rows: {symbol.get('matched_rows')}",
        "- See JSON ranking.alpha_potential_symbols and ranking.noisy_or_negative_symbols.",
    ])

    lines.extend([
        "", "## AI instructions",
        "- Use this digest for hypothesis generation only.",
        "- Do not request strategy/runtime changes from one daily sample.",
        "- If deeper evidence is required, ask for the full server-side JSON for the specific section only.",
        "",
    ])
    return "\n".join(lines)


def build_f5_t09_super_digest(
    *,
    lifecycle_reconciliation: dict[str, Any],
    no_progress_v3: dict[str, Any],
    mfe_capture: dict[str, Any],
    guard_matrix: dict[str, Any],
    low_vol: dict[str, Any],
    copyability: dict[str, Any],
    atr_extension: dict[str, Any],
    btc_bias: dict[str, Any],
    symbol_alpha: dict[str, Any],
) -> dict[str, Any]:
    digest = {
        "schema_version": F5_T10_SCHEMA_VERSION,
        "section": "f5_t09_ai_super_digest",
        "read_only": True,
        "mode": "shadow_observational_only",
        "purpose": "Compact AI-ready summary of F5_T09 diagnostics while keeping full evidence JSONs on the server report folder.",
        "files_policy": {
            "summary": "Full F5_T09 JSON files 20-27 are generated on server for audit/debugging but excluded from the AI_REVIEW ZIP to avoid excessive chunking. The ZIP includes this digest instead.",
            "full_server_files_excluded_from_ai_zip": [
                "20_no_progress_root_cause_v3.json",
                "21_mfe_capture_efficiency_by_exit_reason.json",
                "22_guard_shadow_outcome_matrix.json",
                "23_low_vol_winners_vs_losers.json",
                "24_copyability_score_bucket_outcome.json",
                "25_atr_extension_shadow_outcomes.json",
                "26_btc_bias_conflict_reclaim_quality.json",
                "27_symbol_not_allowed_shadow_alpha.json",
            ],
            "ai_zip_digest_files": [F5_T10_DIGEST_JSON_FILENAME, F5_T10_DIGEST_MD_FILENAME],
        },
        "sections": {
            "lifecycle_reconciliation": _lifecycle_summary(lifecycle_reconciliation),
            "no_progress_root_cause_v3": _no_progress_summary(no_progress_v3),
            "mfe_capture_efficiency": _mfe_capture_summary(mfe_capture),
            "guard_shadow_outcome_matrix": _guard_summary(guard_matrix),
            "low_vol_winners_vs_losers": _low_vol_summary(low_vol),
            "copyability_score_bucket_outcome": _copyability_summary(copyability),
            "atr_extension_shadow_outcomes": _atr_summary(atr_extension),
            "btc_bias_conflict_reclaim_quality": _btc_summary(btc_bias),
            "symbol_not_allowed_shadow_alpha": _symbol_alpha_summary(symbol_alpha),
        },
        "guardrails": [
            "Do not recommend real trading or live operation changes from this digest.",
            "Do not propose automatic threshold, TP/SL, no-progress timeout, MFE-stall, guard, or allowlist changes.",
            "Candidate snapshots and shadow diagnostics are not official trades.",
            "Full JSON evidence remains on the server report folder; request only targeted full sections if needed.",
        ],
    }
    return {"json": digest, "markdown": _render_md(digest)}
