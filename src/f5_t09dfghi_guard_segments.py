"""F5_T09d/f/g/h/i guard and context segmentation diagnostics.

Read-only/dashboard-local analytics only. This module does not read or write the
BotVIP DB directly, does not send Telegram, and does not modify strategy,
thresholds, scanner runtime, lifecycle runtime, Telegram runtime, TP/SL, DB
schema, allowlist, or real trading.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any, Iterable

from .parsers import first_path, parse_json_safe

F5_T09D_GUARD_SHADOW_OUTCOME_MATRIX_FILENAME = "22_guard_shadow_outcome_matrix.json"
F5_T09F_LOW_VOL_WINNERS_LOSERS_FILENAME = "23_low_vol_winners_vs_losers.json"
F5_T09G_COPYABILITY_BUCKET_OUTCOME_FILENAME = "24_copyability_score_bucket_outcome.json"
F5_T09H_ATR_EXTENSION_OUTCOMES_FILENAME = "25_atr_extension_shadow_outcomes.json"
F5_T09I_BTC_BIAS_RECLAIM_QUALITY_FILENAME = "26_btc_bias_conflict_reclaim_quality.json"
F5_T09DFGHI_SCHEMA_VERSION = "f5_t09dfghi_guard_filter_segmentation_v1"

TARGET_GUARDS = [
    "ofa_live_regime_blocked",
    "ofa_live_rvol_too_low",
    "ofa_live_atr_extension_high",
    "ofa_live_symbol_not_allowed",
    "copyability_rr_degraded",
    "ofa_long_low_vol_shadow_only",
    "ofa_low_vol_shadow_only",
]


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


def _profit_factor(rows: list[dict[str, Any]], key: str = "r_value") -> dict[str, Any]:
    clean = [v for v in (_num(row.get(key)) for row in rows) if v is not None]
    wins = [v for v in clean if v > 0]
    losses = [v for v in clean if v < 0]
    gross_win = round(sum(wins), 6)
    gross_loss_abs = round(abs(sum(losses)), 6)
    if not clean:
        pf = None
        note = "no_r_values"
    elif not losses:
        pf = None
        note = "no_losses"
    elif not wins:
        pf = 0.0
        note = "no_wins"
    else:
        pf = round(gross_win / gross_loss_abs, 6) if gross_loss_abs > 0 else None
        note = "ok"
    return {
        "count": len(rows),
        "r_values_count": len(clean),
        "wins": len(wins),
        "losses": len(losses),
        "gross_win_r": gross_win,
        "gross_loss_abs_r": gross_loss_abs,
        "profit_factor": pf,
        "note": note,
    }


def _raw_index(rows: list[dict[str, Any]], id_keys: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        for key in id_keys:
            value = row.get(key)
            if value not in {None, ""}:
                out[str(value)] = row
                break
    return out


def _metadata_for_candidate(row: dict[str, Any], raw_candidates: dict[str, dict[str, Any]]) -> dict[str, Any]:
    cid = _text(row.get("candidate_id"), "")
    meta = parse_json_safe(raw_candidates.get(cid, {}).get("metadata_json"), default={})
    return meta if isinstance(meta, dict) else {}


def _metrics_for_signal(row: dict[str, Any], raw_signals: dict[str, dict[str, Any]]) -> dict[str, Any]:
    sid = _text(row.get("signal_id"), "")
    metrics = parse_json_safe(raw_signals.get(sid, {}).get("metrics_json"), default={})
    return metrics if isinstance(metrics, dict) else {}


def _path(data: dict[str, Any], paths: list[str]) -> Any:
    return first_path(data, paths, default=None)


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


def _btc_conflict(side: Any, btc_trend: Any) -> bool:
    side_u = _upper(side, "")
    btc_u = _upper(btc_trend, "")
    bearish = any(token in btc_u for token in ("BEAR", "DOWN", "SHORT", "SELL"))
    bullish = any(token in btc_u for token in ("BULL", "UP", "LONG", "BUY"))
    return (side_u == "LONG" and bearish) or (side_u == "SHORT" and bullish)


def _copyability_score(row: dict[str, Any], payload: dict[str, Any]) -> float | None:
    direct = _num(row.get("copyability_score"))
    if direct is not None:
        return direct
    return _num(_path(payload, [
        "copyability_score",
        "copyability.score",
        "copyability.final_score",
        "visible_policy.copyability_score",
        "copyability_gate.score",
        "copyability.final_copyability_score",
    ]))


def _copyability_bucket(score: float | None) -> str:
    if score is None:
        return "unknown"
    if score < 70:
        return "lt_70"
    if score < 75:
        return "70_74"
    if score < 80:
        return "75_79"
    if score < 89:
        return "80_88"
    if score <= 90:
        return "89_90"
    return "gt_90"


def _reclaim_score(payload: dict[str, Any]) -> float | None:
    return _num(_path(payload, [
        "reclaim_score",
        "ofa.reclaim_score",
        "ofa_funnel.reclaim_score",
        "reclaim.score",
        "reclaim_quality_score",
        "setup_quality.reclaim_score",
    ]))


def _reclaim_ok(row: dict[str, Any], payload: dict[str, Any]) -> bool | None:
    for value in [
        row.get("reclaim_ok"),
        _path(payload, ["reclaim_ok", "ofa.reclaim_ok", "ofa_funnel.reclaim_ok", "reclaim.ok"]),
    ]:
        if value is not None:
            return _bool(value)
    return None


def _mfe(row: dict[str, Any], payload: dict[str, Any]) -> float | None:
    direct = _num(row.get("mfe"))
    if direct is not None:
        return direct
    return _num(_path(payload, ["mfe", "mfe_r", "max_mfe_r", "max_favorable_excursion_r", "hypothetical_mfe_r", "tracking.mfe_r"]))


def _mae(row: dict[str, Any], payload: dict[str, Any]) -> float | None:
    direct = _num(row.get("mae"))
    if direct is not None:
        return direct
    return _num(_path(payload, ["mae", "mae_r", "max_mae_r", "max_adverse_excursion_r", "hypothetical_mae_r", "tracking.mae_r"]))


def _mfe_first_3m(row: dict[str, Any], payload: dict[str, Any]) -> float | None:
    return _num(_path(payload, [
        "mfe_first_3m",
        "mfe_first_3m_r",
        "first_3m_mfe_r",
        "first_minutes.mfe_3m_r",
        "tracking.mfe_first_3m_r",
    ]))


def _atr_extension(row: dict[str, Any], payload: dict[str, Any]) -> float | None:
    direct = _num(row.get("atr_extension"))
    if direct is not None:
        return direct
    return _num(_path(payload, ["atr_extension", "atr.extension", "market.atr_extension", "context.atr_extension"]))


def _market_regime(row: dict[str, Any], payload: dict[str, Any]) -> str:
    return _text(row.get("market_regime") or _path(payload, ["market_regime", "regime", "regime.regime", "market.regime"]))


def _btc_trend(row: dict[str, Any], payload: dict[str, Any]) -> str:
    return _text(row.get("btc_trend") or _path(payload, ["btc_trend", "market_regime.btc_trend", "btc.bias", "btc.trend"]))


def _low_vol(row: dict[str, Any], payload: dict[str, Any]) -> bool:
    regime = _upper(_market_regime(row, payload), "")
    rvol = _num(row.get("rvol"))
    if rvol is None:
        rvol = _num(_path(payload, ["rvol", "market.rvol", "context.rvol"]))
    return "LOW_VOL" in regime or "LOW VOL" in regime or (rvol is not None and rvol < 1.0)


def _guard_reason(row: dict[str, Any], payload: dict[str, Any]) -> str:
    reason = _text(row.get("blocked_reason") or row.get("reason_if_rejected") or _path(payload, ["blocked_reason", "reason", "reason_if_rejected", "guard_reason"]), "unknown")
    if reason.startswith("live_guard:"):
        reason = reason.split(":", 1)[1]
    return reason


def _target_guard(reason: str) -> str | None:
    for target in TARGET_GUARDS:
        if target == reason or target in reason:
            return target
    return None


def _official_r(row: dict[str, Any]) -> float | None:
    for key in ("net_r", "pnl_r", "gross_r"):
        value = _num(row.get(key))
        if value is not None:
            return value
    if _bool(row.get("real_stop_loss_hit")):
        return -1.0
    if _bool(row.get("primary_tp_hit")):
        return _num(row.get("gross_rr")) or 1.0
    return None


def _official_outcome(row: dict[str, Any]) -> str:
    if _bool(row.get("primary_tp_hit")):
        return "primary_tp_hit"
    if _bool(row.get("real_stop_loss_hit")):
        return "real_stop_loss_hit"
    if _bool(row.get("no_progress_exit")):
        return "no_progress_exit"
    if _bool(row.get("mfe_stall_exit")):
        return "mfe_stall_exit"
    if _bool(row.get("time_stop_exit")):
        return "time_stop_exit"
    if _bool(row.get("breakeven_stop_hit")):
        return "breakeven_stop_hit"
    if _bool(row.get("runner_breakeven_stop_hit")):
        return "runner_breakeven_stop_hit"
    if _bool(row.get("cancelled")):
        return "cancelled_or_expired"
    return _lower(row.get("exit_reason"), "other_or_open")


def _candidate_r(row: dict[str, Any], payload: dict[str, Any]) -> float | None:
    for key in ("hypothetical_r", "net_r", "pnl_r", "gross_r"):
        value = _num(row.get(key))
        if value is not None:
            return value
        value = _num(_path(payload, [key, "outcome." + key, "hypothetical." + key]))
        if value is not None:
            return value
    result = _lower(row.get("hypothetical_result") or _path(payload, ["hypothetical_result", "outcome.result", "hypothetical.result"]), "unknown")
    exit_reason = _lower(row.get("hypothetical_exit_reason") or _path(payload, ["hypothetical_exit_reason", "outcome.exit_reason", "hypothetical.exit_reason"]), "unknown")
    if result in {"won", "win", "primary_tp_hit"}:
        return _num(row.get("net_rr")) or _num(_path(payload, ["net_rr", "gross_rr", "rr"])) or 1.0
    if result in {"lost", "loss"} or exit_reason == "stop_loss":
        return -1.0
    if result in {"no_progress", "time_stop", "mfe_stall"} or exit_reason in {"no_progress", "time_stop", "mfe_stall"}:
        return _num(_path(payload, ["outcome.net_r", "hypothetical.net_r", "estimated_r"]))
    return None


def _candidate_outcome(row: dict[str, Any], payload: dict[str, Any]) -> str:
    result = _lower(row.get("hypothetical_result") or _path(payload, ["hypothetical_result", "outcome.result", "hypothetical.result"]), "unknown")
    exit_reason = _lower(row.get("hypothetical_exit_reason") or _path(payload, ["hypothetical_exit_reason", "outcome.exit_reason", "hypothetical.exit_reason"]), "")
    if result in {"won", "win"}:
        return "hypothetical_win"
    if result in {"lost", "loss"}:
        return "hypothetical_loss"
    if exit_reason:
        return exit_reason
    return result


def _candidate_compact(row: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    r_value = _candidate_r(row, payload)
    side = row.get("side") or _path(payload, ["side", "signal_type"])
    btc = _btc_trend(row, payload)
    return {
        "entity_type": "candidate_shadow",
        "candidate_id": row.get("candidate_id"),
        "symbol": row.get("symbol"),
        "side": side,
        "market_regime": _market_regime(row, payload),
        "btc_trend": btc,
        "btc_bias_conflict": _btc_conflict(side, btc),
        "zone": _zone(row),
        "guard_reason": _guard_reason(row, payload),
        "outcome": _candidate_outcome(row, payload),
        "r_value": r_value,
        "mfe_r": _mfe(row, payload),
        "mae_r": _mae(row, payload),
        "mfe_first_3m_r": _mfe_first_3m(row, payload),
        "copyability_score": _copyability_score(row, payload),
        "copyability_bucket": _copyability_bucket(_copyability_score(row, payload)),
        "atr_extension": _atr_extension(row, payload),
        "reclaim_score": _reclaim_score(payload),
        "reclaim_ok": _reclaim_ok(row, payload),
        "low_vol": _low_vol(row, payload),
        "data_quality_flags": [],
    }


def _signal_compact(row: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    r_value = _official_r(row)
    side = row.get("side")
    btc = _btc_trend(row, payload)
    return {
        "entity_type": "official_signal",
        "signal_id": row.get("signal_id"),
        "symbol": row.get("symbol"),
        "side": side,
        "market_regime": _market_regime(row, payload),
        "btc_trend": btc,
        "btc_bias_conflict": _btc_conflict(side, btc),
        "zone": _zone(row),
        "outcome": _official_outcome(row),
        "r_value": r_value,
        "mfe_r": _mfe(row, payload),
        "mae_r": _mae(row, payload),
        "mfe_first_3m_r": _mfe_first_3m(row, payload),
        "copyability_score": _copyability_score(row, payload),
        "copyability_bucket": _copyability_bucket(_copyability_score(row, payload)),
        "atr_extension": _atr_extension(row, payload),
        "reclaim_score": _reclaim_score(payload),
        "reclaim_ok": _reclaim_ok(row, payload),
        "low_vol": _low_vol(row, payload),
        "primary_tp_hit": _bool(row.get("primary_tp_hit")),
        "real_stop_loss_hit": _bool(row.get("real_stop_loss_hit")),
        "no_progress_exit": _bool(row.get("no_progress_exit")),
    }


def _sample(rows: list[dict[str, Any]], limit: int = 80) -> list[dict[str, Any]]:
    return rows[:limit]


def _shadow_value(rows: list[dict[str, Any]]) -> dict[str, Any]:
    values = [row.get("r_value") for row in rows]
    clean = [v for v in (_num(item) for item in values) if v is not None]
    losses_abs = round(abs(sum(v for v in clean if v < 0)), 6)
    winners = round(sum(v for v in clean if v > 0), 6)
    return {
        "rows": len(rows),
        "r_values_count": len(clean),
        "avoided_losses_r": losses_abs,
        "missed_winners_r": winners,
        "net_guard_value_r": round(losses_abs - winners, 6) if clean else None,
        "profit_factor_if_allowed": _profit_factor(rows),
        "outcome_counts": dict(Counter(_text(row.get("outcome")) for row in rows).most_common()),
        "data_quality_note": "Value metrics are computed only when hypothetical/derived R evidence exists or a conservative result fallback is available.",
    }


def _bucket_rows(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[_text(row.get(key))].append(row)
    out = {}
    for name, items in sorted(buckets.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        out[name] = {
            **_shadow_value(items),
            "avg_mfe_r": _avg(row.get("mfe_r") for row in items),
            "avg_mae_r": _avg(row.get("mae_r") for row in items),
            "avg_atr_extension": _avg(row.get("atr_extension") for row in items),
            "avg_copyability_score": _avg(row.get("copyability_score") for row in items),
            "sample": _sample(items, 30),
        }
    return out


def _build_entity_rows(facts: list[dict[str, Any]], signals: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    raw_signals = _raw_index(signals, ("id", "signal_id"))
    raw_candidates = _raw_index(candidates, ("id", "candidate_id"))
    official_rows = []
    candidate_rows = []
    for row in facts:
        if row.get("record_type") == "signal":
            official_rows.append(_signal_compact(row, _metrics_for_signal(row, raw_signals)))
        elif row.get("record_type") == "candidate":
            candidate_rows.append(_candidate_compact(row, _metadata_for_candidate(row, raw_candidates)))
    return official_rows, candidate_rows


def build_guard_shadow_outcome_matrix(*, facts: list[dict[str, Any]], signals: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    _official, candidate_rows = _build_entity_rows(facts, signals, candidates)
    target_rows = [row for row in candidate_rows if _target_guard(_text(row.get("guard_reason")))]
    for row in target_rows:
        row["target_guard"] = _target_guard(_text(row.get("guard_reason")))
    return {
        "schema_version": F5_T09DFGHI_SCHEMA_VERSION,
        "section": "guard_shadow_outcome_matrix",
        "read_only": True,
        "target_guards": TARGET_GUARDS,
        "candidate_shadow_denominator": len(candidate_rows),
        "matched_guard_rows": len(target_rows),
        "matrix_by_guard": _bucket_rows(target_rows, "target_guard"),
        "matrix_by_guard_and_symbol": {
            guard: _bucket_rows([row for row in target_rows if row.get("target_guard") == guard], "symbol")
            for guard in TARGET_GUARDS
        },
        "guardrails": [
            "Candidate snapshots are not official trades and must not be added to official signal counts.",
            "Guard value is observational: avoided_losses_r minus missed_winners_r when hypothetical R evidence is available.",
            "No guard is opened, closed, relaxed, or tightened by this report.",
        ],
    }


def build_low_vol_winners_vs_losers(*, facts: list[dict[str, Any]], signals: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    official_rows, candidate_rows = _build_entity_rows(facts, signals, candidates)
    official_low = [row for row in official_rows if row.get("low_vol")]
    candidate_low = [row for row in candidate_rows if row.get("low_vol")]
    return {
        "schema_version": F5_T09DFGHI_SCHEMA_VERSION,
        "section": "low_vol_winners_vs_losers",
        "read_only": True,
        "official_signals": {
            "denominator": len(official_rows),
            "low_vol_rows": len(official_low),
            "by_outcome": _bucket_rows(official_low, "outcome"),
            "by_reclaim_ok": _bucket_rows(official_low, "reclaim_ok"),
            "by_symbol": _bucket_rows(official_low, "symbol"),
        },
        "candidate_shadow": {
            "denominator": len(candidate_rows),
            "low_vol_rows": len(candidate_low),
            "by_outcome": _bucket_rows(candidate_low, "outcome"),
            "by_reclaim_ok": _bucket_rows(candidate_low, "reclaim_ok"),
            "by_guard_reason": _bucket_rows(candidate_low, "guard_reason"),
            "by_symbol": _bucket_rows(candidate_low, "symbol"),
        },
        "interpretation": "Separate LOW_VOL without expansion from LOW_VOL with reclaim/impulse evidence; do not treat LOW_VOL as always bad from this section alone.",
    }


def build_copyability_score_bucket_outcome(*, facts: list[dict[str, Any]], signals: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    official_rows, candidate_rows = _build_entity_rows(facts, signals, candidates)
    return {
        "schema_version": F5_T09DFGHI_SCHEMA_VERSION,
        "section": "copyability_score_bucket_outcome",
        "read_only": True,
        "buckets": ["lt_70", "70_74", "75_79", "80_88", "89_90", "gt_90", "unknown"],
        "official_signals": _bucket_rows(official_rows, "copyability_bucket"),
        "candidate_shadow": _bucket_rows(candidate_rows, "copyability_bucket"),
        "guardrail": "Copyability buckets are measured only; this report does not change visible policy or copyability gates.",
    }


def build_atr_extension_shadow_outcomes(*, facts: list[dict[str, Any]], signals: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    official_rows, candidate_rows = _build_entity_rows(facts, signals, candidates)
    shadow_with_atr = [row for row in candidate_rows if row.get("atr_extension") is not None]
    official_with_atr = [row for row in official_rows if row.get("atr_extension") is not None]
    return {
        "schema_version": F5_T09DFGHI_SCHEMA_VERSION,
        "section": "atr_extension_shadow_outcomes",
        "read_only": True,
        "candidate_shadow": {
            "rows_with_atr_extension": len(shadow_with_atr),
            "by_side": _bucket_rows(shadow_with_atr, "side"),
            "by_btc_bias_conflict": _bucket_rows(shadow_with_atr, "btc_bias_conflict"),
            "by_market_regime": _bucket_rows(shadow_with_atr, "market_regime"),
            "by_outcome": _bucket_rows(shadow_with_atr, "outcome"),
        },
        "official_signals_reference": {
            "rows_with_atr_extension": len(official_with_atr),
            "by_outcome": _bucket_rows(official_with_atr, "outcome"),
            "by_side": _bucket_rows(official_with_atr, "side"),
        },
        "mfe_first_3m_note": "mfe_first_3m_r is populated only when present in metadata_json/metrics_json; missing values are not invented.",
    }


def build_btc_bias_conflict_reclaim_quality(*, facts: list[dict[str, Any]], signals: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    official_rows, candidate_rows = _build_entity_rows(facts, signals, candidates)
    official_conflict = [row for row in official_rows if row.get("btc_bias_conflict")]
    candidate_conflict = [row for row in candidate_rows if row.get("btc_bias_conflict")]
    return {
        "schema_version": F5_T09DFGHI_SCHEMA_VERSION,
        "section": "btc_bias_conflict_reclaim_quality",
        "read_only": True,
        "definition": "LONG with BTC bearish/down/short/sell bias, or SHORT with BTC bullish/up/long/buy bias.",
        "official_signals": {
            "conflict_rows": len(official_conflict),
            "by_side": _bucket_rows(official_conflict, "side"),
            "by_reclaim_ok": _bucket_rows(official_conflict, "reclaim_ok"),
            "by_outcome": _bucket_rows(official_conflict, "outcome"),
        },
        "candidate_shadow": {
            "conflict_rows": len(candidate_conflict),
            "by_side": _bucket_rows(candidate_conflict, "side"),
            "by_reclaim_ok": _bucket_rows(candidate_conflict, "reclaim_ok"),
            "by_guard_reason": _bucket_rows(candidate_conflict, "guard_reason"),
            "by_outcome": _bucket_rows(candidate_conflict, "outcome"),
        },
        "guardrail": "BTC bias conflict quality is observational and must not auto-block or auto-allow signals.",
    }


def build_f5_t09dfghi_guard_filter_outputs(*, facts: list[dict[str, Any]], events: list[dict[str, Any]], signals: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "guard_shadow_outcome_matrix": build_guard_shadow_outcome_matrix(facts=facts, signals=signals, candidates=candidates),
        "low_vol_winners_vs_losers": build_low_vol_winners_vs_losers(facts=facts, signals=signals, candidates=candidates),
        "copyability_score_bucket_outcome": build_copyability_score_bucket_outcome(facts=facts, signals=signals, candidates=candidates),
        "atr_extension_shadow_outcomes": build_atr_extension_shadow_outcomes(facts=facts, signals=signals, candidates=candidates),
        "btc_bias_conflict_reclaim_quality": build_btc_bias_conflict_reclaim_quality(facts=facts, signals=signals, candidates=candidates),
    }
