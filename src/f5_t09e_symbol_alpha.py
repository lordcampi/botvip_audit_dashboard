"""F5_T09e symbol-not-allowed shadow alpha diagnostics.

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

F5_T09E_SYMBOL_SHADOW_ALPHA_FILENAME = "27_symbol_not_allowed_shadow_alpha.json"
F5_T09E_SCHEMA_VERSION = "f5_t09e_symbol_not_allowed_shadow_alpha_v1"
TARGET_SYMBOLS = ["SUI", "NEAR", "BCH", "LTC"]
SYMBOL_NOT_ALLOWED_GUARDS = {
    "ofa_live_symbol_not_allowed",
    "live_guard:ofa_live_symbol_not_allowed",
    "symbol_not_allowed",
    "not_allowed_symbol",
    "symbol_not_in_allowlist",
}


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


def _profit_factor(rows: list[dict[str, Any]]) -> dict[str, Any]:
    clean = [v for v in (_num(row.get("r_value")) for row in rows) if v is not None]
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


def _base_symbol(symbol: Any) -> str:
    text = _upper(symbol, "")
    if "/" in text:
        text = text.split("/", 1)[0]
    if ":" in text:
        text = text.split(":", 1)[0]
    return text or "UNKNOWN"


def _raw_candidate_index(candidates: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in candidates:
        cid = row.get("id") or row.get("candidate_id")
        if cid not in {None, ""}:
            out[str(cid)] = row
    return out


def _metadata(row: dict[str, Any], raw_candidates: dict[str, dict[str, Any]]) -> dict[str, Any]:
    cid = _text(row.get("candidate_id"), "")
    payload = parse_json_safe(raw_candidates.get(cid, {}).get("metadata_json"), default={})
    return payload if isinstance(payload, dict) else {}


def _path(payload: dict[str, Any], paths: list[str]) -> Any:
    return first_path(payload, paths, default=None)


def _guard_reason(row: dict[str, Any], payload: dict[str, Any]) -> str:
    return _text(row.get("blocked_reason") or row.get("reason_if_rejected") or _path(payload, ["blocked_reason", "reason", "reason_if_rejected", "guard_reason"]), "unknown")


def _is_symbol_not_allowed(row: dict[str, Any], payload: dict[str, Any]) -> bool:
    reason = _guard_reason(row, payload)
    reason_l = reason.lower()
    if reason in SYMBOL_NOT_ALLOWED_GUARDS or reason_l in SYMBOL_NOT_ALLOWED_GUARDS:
        return True
    return "symbol_not_allowed" in reason_l or "not_allowed" in reason_l or "allowlist" in reason_l


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


def _btc_trend(row: dict[str, Any], payload: dict[str, Any]) -> str:
    return _text(row.get("btc_trend") or _path(payload, ["btc_trend", "market_regime.btc_trend", "btc.bias", "btc.trend"]))


def _btc_conflict(side: Any, btc_trend: Any) -> bool:
    side_u = _upper(side, "")
    btc_u = _upper(btc_trend, "")
    bearish = any(token in btc_u for token in ("BEAR", "DOWN", "SHORT", "SELL"))
    bullish = any(token in btc_u for token in ("BULL", "UP", "LONG", "BUY"))
    return (side_u == "LONG" and bearish) or (side_u == "SHORT" and bullish)


def _market_regime(row: dict[str, Any], payload: dict[str, Any]) -> str:
    return _text(row.get("market_regime") or _path(payload, ["market_regime", "regime", "regime.regime", "market.regime"]))


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
        return "tp1_or_hypothetical_win"
    if result in {"lost", "loss"}:
        return "sl_or_hypothetical_loss"
    if exit_reason in {"no_progress", "mfe_stall", "time_stop"}:
        return exit_reason
    if exit_reason:
        return exit_reason
    return result


def _confidence(sample_size: int, r_values_count: int) -> str:
    if sample_size >= 50 and r_values_count >= 20:
        return "robust_sample"
    if sample_size >= 20 and r_values_count >= 10:
        return "medium_sample"
    if sample_size >= 5 and r_values_count >= 3:
        return "small_sample"
    if sample_size > 0:
        return "very_small_sample"
    return "no_sample"


def _row_compact(row: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    side = row.get("side") or _path(payload, ["side", "signal_type"])
    btc = _btc_trend(row, payload)
    outcome = _candidate_outcome(row, payload)
    r_value = _candidate_r(row, payload)
    mfe = _mfe(row, payload)
    mae = _mae(row, payload)
    return {
        "candidate_id": row.get("candidate_id"),
        "symbol": row.get("symbol"),
        "base_symbol": _base_symbol(row.get("symbol")),
        "side": side,
        "market_regime": _market_regime(row, payload),
        "zone": _zone(row),
        "btc_trend": btc,
        "btc_bias_conflict": _btc_conflict(side, btc),
        "guard_reason": _guard_reason(row, payload),
        "outcome": outcome,
        "tp1": outcome == "tp1_or_hypothetical_win",
        "sl": outcome == "sl_or_hypothetical_loss",
        "no_progress": outcome == "no_progress",
        "mfe_r": mfe,
        "mae_r": mae,
        "r_value": r_value,
        "score": _num(row.get("score") or _path(payload, ["score"])),
        "rvol": _num(row.get("rvol") or _path(payload, ["rvol"])),
        "atr_extension": _num(row.get("atr_extension") or _path(payload, ["atr_extension"])),
        "data_quality_flags": [flag for flag, missing in [("mfe_missing", mfe is None), ("mae_missing", mae is None), ("r_value_missing", r_value is None)] if missing],
    }


def _segment_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    clean_r = [v for v in (_num(row.get("r_value")) for row in rows) if v is not None]
    wins = [row for row in rows if row.get("tp1") or (_num(row.get("r_value")) is not None and (_num(row.get("r_value")) or 0) > 0)]
    losses = [row for row in rows if row.get("sl") or (_num(row.get("r_value")) is not None and (_num(row.get("r_value")) or 0) < 0)]
    net_sum = round(sum(clean_r), 6) if clean_r else None
    sample_size = len(rows)
    r_count = len(clean_r)
    avg_r = _avg(clean_r)
    alpha_score = None
    if avg_r is not None:
        alpha_score = round(avg_r * min(1.0, r_count / 20.0), 6)
    return {
        "sample_size": sample_size,
        "r_values_count": r_count,
        "confidence": _confidence(sample_size, r_count),
        "tp1_or_win_count": len(wins),
        "sl_or_loss_count": len(losses),
        "no_progress_count": sum(1 for row in rows if row.get("no_progress")),
        "avg_r": avg_r,
        "net_sum_r": net_sum,
        "avg_mfe_r": _avg(row.get("mfe_r") for row in rows),
        "avg_mae_r": _avg(row.get("mae_r") for row in rows),
        "profit_factor": _profit_factor(rows),
        "alpha_score": alpha_score,
        "outcome_counts": dict(Counter(_text(row.get("outcome")) for row in rows).most_common()),
        "data_quality_counts": dict(Counter(flag for row in rows for flag in row.get("data_quality_flags", [])).most_common()),
        "examples": rows[:60],
    }


def _bucket(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[_text(row.get(key))].append(row)
    return {name: _segment_summary(items) for name, items in sorted(buckets.items(), key=lambda kv: (-len(kv[1]), kv[0]))}


def _symbol_rank(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = []
    for symbol, items in _bucket(rows, "base_symbol").items():
        item = dict(items)
        item["symbol"] = symbol
        ranked.append(item)
    ranked.sort(key=lambda item: (item.get("alpha_score") is not None, item.get("alpha_score") or -999, item.get("sample_size") or 0), reverse=True)
    return ranked


def build_symbol_not_allowed_shadow_alpha(*, facts: list[dict[str, Any]], events: list[dict[str, Any]] | None = None, signals: list[dict[str, Any]] | None = None, candidates: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    raw_candidates = _raw_candidate_index(candidates or [])
    candidate_facts = [row for row in facts if row.get("record_type") == "candidate"]
    rows: list[dict[str, Any]] = []
    for row in candidate_facts:
        payload = _metadata(row, raw_candidates)
        compact = _row_compact(row, payload)
        if _is_symbol_not_allowed(row, payload) or compact["base_symbol"] in TARGET_SYMBOLS:
            rows.append(compact)

    target_rows = [row for row in rows if row.get("base_symbol") in TARGET_SYMBOLS]
    other_rows = [row for row in rows if row.get("base_symbol") not in TARGET_SYMBOLS]
    ranking = _symbol_rank(rows)
    alpha_potential = [row for row in ranking if row.get("alpha_score") is not None and row.get("alpha_score") > 0]
    noisy = [row for row in ranking if row.get("alpha_score") is not None and row.get("alpha_score") <= 0]

    return {
        "schema_version": F5_T09E_SCHEMA_VERSION,
        "section": "symbol_not_allowed_shadow_alpha",
        "read_only": True,
        "mode": "shadow_observational_only",
        "purpose": "Measure alpha in blocked/not-allowed symbols without opening allowlist.",
        "target_symbols": TARGET_SYMBOLS,
        "candidate_shadow_denominator": len(candidate_facts),
        "matched_rows": len(rows),
        "target_symbol_rows": len(target_rows),
        "other_symbol_rows": len(other_rows),
        "ranking": {
            "alpha_potential_symbols": alpha_potential,
            "noisy_or_negative_symbols": noisy,
            "all_symbols_ranked": ranking,
            "ranking_note": "alpha_score = avg_r multiplied by sample-size confidence cap. It is descriptive, not an allowlist decision.",
        },
        "segments": {
            "by_symbol": _bucket(rows, "base_symbol"),
            "by_side": _bucket(rows, "side"),
            "by_market_regime": _bucket(rows, "market_regime"),
            "by_zone": _bucket(rows, "zone"),
            "by_btc_bias_conflict": _bucket(rows, "btc_bias_conflict"),
            "by_guard_reason": _bucket(rows, "guard_reason"),
            "by_outcome": _bucket(rows, "outcome"),
        },
        "entity_scope": {
            "candidate_snapshots_countable_as_trades": False,
            "official_signals_counted_here": 0,
            "rule": "This section evaluates candidate shadow snapshots only; do not mix with official signal/trade denominators.",
        },
        "guardrails": [
            "Do not change allowlist from this report.",
            "Do not modify strategy, thresholds, TP/SL, scanner, lifecycle, Telegram runtime, DB schema, or trading behavior.",
            "Small samples are explicitly marked with confidence and must not be treated as robust evidence.",
        ],
    }
