"""F5_T03d dashboard-side AI Reporter derived sections.

Read-only/dashboard-local derived diagnostics for the BotVIP Daily AI Reporter.
It does not write DB, send Telegram, change strategy, thresholds, scanner,
lifecycle, or BotVIP runtime.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

SCHEMA_VERSION = "f5_t03b_ai_reporter_integration_v1_dashboard_derived"
FILENAME = "f5_t03b_integration_sections.json"


def _get(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(key, default)
    return getattr(row, key, default)


def _safe_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


def _norm(value: Any, default: str = "unknown") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _lower(value: Any, default: str = "unknown") -> str:
    return _norm(value, default).lower()


def _upper(value: Any, default: str = "UNKNOWN") -> str:
    return _norm(value, default).upper()


def _boolish(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    raw = str(value).strip().lower()
    if raw in {"1", "true", "yes", "on", "ok"}:
        return True
    if raw in {"0", "false", "no", "off", "blocked"}:
        return False
    return None


def _signal_id(row: Mapping[str, Any]) -> str:
    return _norm(
        _get(row, "signal_id")
        or _get(row, "id")
        or _get(row, "record_id")
        or _get(row, "signal_record_id"),
        "unknown",
    )


def _symbol(row: Mapping[str, Any]) -> str:
    return _norm(_get(row, "symbol") or _get(row, "pair") or _get(row, "asset"), "unknown")


def _setup(row: Mapping[str, Any]) -> str:
    return _upper(_get(row, "setup_type") or _get(row, "setup") or _get(row, "trigger"), "UNKNOWN")


def _side(row: Mapping[str, Any]) -> str:
    return _upper(_get(row, "side") or _get(row, "signal_type") or _get(row, "signal"), "UNKNOWN")


def _exit_reason(row: Mapping[str, Any]) -> str:
    return _lower(_get(row, "exit_reason") or _get(row, "outcome") or _get(row, "result"), "unknown")


def _zone(row: Mapping[str, Any]) -> str:
    weekend = _boolish(_get(row, "weekend") or _get(row, "is_weekend_utc"))
    killzone = _boolish(_get(row, "in_killzone") or _get(row, "is_killzone"))
    if weekend is True:
        return "weekend"
    if killzone is True:
        return "killzone"
    if killzone is False:
        return "outside_killzone"
    return _lower(_get(row, "zone") or _get(row, "zone_label"), "unknown_zone")


def _net_r(row: Mapping[str, Any]) -> float | None:
    for key in ("net_r", "pnl_r", "gross_r", "hypothetical_r"):
        value = _safe_float(_get(row, key))
        if value is not None:
            return value
    return None


def _mfe(row: Mapping[str, Any]) -> float | None:
    for key in ("mfe_r", "mfe", "max_favorable_excursion_r"):
        value = _safe_float(_get(row, key))
        if value is not None:
            return value
    return None


def _mae(row: Mapping[str, Any]) -> float | None:
    for key in ("mae_r", "mae", "max_adverse_excursion_r"):
        value = _safe_float(_get(row, key))
        if value is not None:
            return value
    return None


def _is_notified(row: Mapping[str, Any]) -> bool:
    for key in ("sent_to_telegram", "telegram_notified", "notified", "has_notified_event"):
        value = _boolish(_get(row, key))
        if value is not None:
            return value
    return False


def _profit_factor(values: Iterable[Any]) -> dict[str, Any]:
    parsed = [v for v in (_safe_float(item) for item in values) if v is not None]
    wins = [v for v in parsed if v > 0]
    losses = [v for v in parsed if v < 0]
    gross_win = sum(wins)
    gross_loss_abs = abs(sum(losses))
    if not parsed:
        pf = None
        note = "no_r_values"
    elif not losses:
        pf = None
        note = "no_losses"
    elif not wins:
        pf = 0.0
        note = "no_wins"
    else:
        pf = gross_win / gross_loss_abs if gross_loss_abs > 0 else None
        note = "ok"
    return {
        "count": len(parsed),
        "wins": len(wins),
        "losses": len(losses),
        "gross_win_r": round(gross_win, 6),
        "gross_loss_abs_r": round(gross_loss_abs, 6),
        "profit_factor": None if pf is None else round(pf, 6),
        "note": note,
    }


def _classify_no_progress(row: Mapping[str, Any]) -> str | None:
    if _exit_reason(row) != "no_progress" and _lower(_get(row, "outcome")) != "no_progress_exit":
        return None
    mfe = _mfe(row)
    mae = _mae(row)
    data_gap = _safe_float(_get(row, "data_gap_events")) or 0
    if data_gap > 0:
        return "data_quality_suspect"
    if mfe is None:
        return "unknown_mfe"
    if mfe < 0.10 and (mae is None or mae > -0.25):
        return "true_no_movement"
    if mfe < 0.20 and mae is not None and mae <= -0.25:
        return "adverse_drift"
    if mfe >= 0.25:
        return "missed_micro_tp"
    return "low_follow_through"


def _quality_score(row: Mapping[str, Any]) -> int:
    score = 100
    if _mfe(row) is None:
        score -= 15
    if _mae(row) is None:
        score -= 15
    data_gap = _safe_float(_get(row, "data_gap_events")) or 0
    if data_gap > 0:
        score -= 10
    if _setup(row).startswith("OFA_") and not _is_notified(row):
        score -= 5
    return max(0, int(score))


def _compact_signal_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "signal_id": _signal_id(row),
        "symbol": _symbol(row),
        "side": _side(row),
        "setup": _setup(row),
        "exit_reason": _exit_reason(row),
        "zone": _zone(row),
        "net_r": _net_r(row),
        "mfe_r": _mfe(row),
        "mae_r": _mae(row),
        "sent_to_telegram": _is_notified(row),
        "data_gap_events": _safe_float(_get(row, "data_gap_events")) or 0,
        "no_progress_class": _classify_no_progress(row),
        "data_quality_score": _quality_score(row),
    }


def _candidate_near_miss(row: Mapping[str, Any]) -> bool:
    if _boolish(_get(row, "near_miss")) is True:
        return True
    if _boolish(_get(row, "blocked_by_live_guard")) is True:
        return True
    if _boolish(_get(row, "blocked_by_copyability")) is True:
        return True
    outcome = _lower(_get(row, "hypothetical_result") or _get(row, "outcome"), "pending")
    return outcome in {"won", "lost", "time_stop", "skipped_no_geometry", "invalid_geometry"}


def _candidate_outcome(row: Mapping[str, Any]) -> str:
    return _lower(_get(row, "hypothetical_result") or _get(row, "outcome"), "pending")


def _avg(values: Iterable[Any]) -> float | None:
    parsed = [v for v in (_safe_float(item) for item in values) if v is not None]
    return None if not parsed else round(sum(parsed) / len(parsed), 6)


def build_f5_t03b_integration_sections(
    *,
    facts: list[dict[str, Any]],
    events: list[dict[str, Any]],
    signals: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    lifecycle: dict[str, Any] | None = None,
    diagnostics: dict[str, Any] | None = None,
    t02_diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build dashboard-derived F5_T03b-style AI sections."""
    lifecycle = dict(lifecycle or {})
    diagnostics = dict(diagnostics or {})
    t02_diagnostics = dict(t02_diagnostics or {})

    signal_like = facts if facts else signals
    signal_rows = [_compact_signal_row(row) for row in signal_like]

    event_notified_ids = {
        _norm(_get(ev, "signal_id"), "")
        for ev in events
        if _upper(_get(ev, "event_type")) == "NOTIFIED"
    }

    notified_effective = [
        row for row in signal_rows
        if row.get("sent_to_telegram") or str(row.get("signal_id")) in event_notified_ids
    ]
    mfe_rows = [row for row in signal_rows if row.get("mfe_r") is not None or row.get("mae_r") is not None]
    no_progress_rows = [row for row in signal_rows if row.get("no_progress_class")]

    by_zone: dict[str, list[float]] = defaultdict(list)
    by_setup: dict[str, list[float]] = defaultdict(list)
    by_exit_reason: dict[str, list[float]] = defaultdict(list)

    for row in signal_rows:
        if row.get("net_r") is None:
            continue
        by_zone[str(row.get("zone"))].append(row.get("net_r"))
        by_setup[str(row.get("setup"))].append(row.get("net_r"))
        by_exit_reason[str(row.get("exit_reason"))].append(row.get("net_r"))

    near_rows = [row for row in candidates if _candidate_near_miss(row)]
    near_outcomes = Counter(_candidate_outcome(row) for row in near_rows)

    ofa_rows = [row for row in signal_rows if str(row.get("setup") or "").startswith("OFA_")]
    ofa_issues = []
    for row in ofa_rows:
        if not row.get("sent_to_telegram") and str(row.get("signal_id")) not in event_notified_ids:
            ofa_issues.append({"signal_id": row.get("signal_id"), "issue": "ofa_signal_without_notified_evidence"})
        if row.get("mfe_r") is None and row.get("mae_r") is None:
            ofa_issues.append({"signal_id": row.get("signal_id"), "issue": "missing_mfe_mae"})

    return {
        "schema_version": SCHEMA_VERSION,
        "source": "botvip_audit_dashboard_daily_facts_derived",
        "read_only": True,
        "double_counting_warning": "CSV rows and this JSON are alternative derived views; do not count both as independent trades.",
        "telegram_notified_consistency_check": {
            "total_signals": len(signal_rows),
            "notified_effective": len(notified_effective),
            "event_notified_ids": len(event_notified_ids),
            "sent_to_telegram_rows": sum(1 for row in signal_rows if row.get("sent_to_telegram")),
            "mismatches": max(0, len(event_notified_ids) - sum(1 for row in signal_rows if row.get("sent_to_telegram"))),
            "examples": [row for row in signal_rows if str(row.get("signal_id")) in event_notified_ids and not row.get("sent_to_telegram")][:20],
        },
        "mfe_mae_recovery_summary": {
            "known_mfe_mae": len(mfe_rows),
            "missing_mfe_mae": max(0, len(signal_rows) - len(mfe_rows)),
            "avg_mfe_r": _avg(row.get("mfe_r") for row in mfe_rows),
            "avg_mae_r": _avg(row.get("mae_r") for row in mfe_rows),
            "source_counts": {"daily_facts_or_signals": len(mfe_rows)},
        },
        "no_progress_diagnostics_v2": {
            "total_no_progress": len(no_progress_rows),
            "classes": dict(Counter(str(row.get("no_progress_class")) for row in no_progress_rows)),
            "examples": no_progress_rows[:80],
            "t02_reference_available": bool(t02_diagnostics),
        },
        "profit_factor_diagnostics": {
            "all": _profit_factor(row.get("net_r") for row in signal_rows),
            "by_zone": {key: _profit_factor(values) for key, values in sorted(by_zone.items())},
            "by_setup": {key: _profit_factor(values) for key, values in sorted(by_setup.items())},
            "by_exit_reason": {key: _profit_factor(values) for key, values in sorted(by_exit_reason.items())},
        },
        "zone_diagnostics": {
            "counts": dict(Counter(str(row.get("zone")) for row in signal_rows)),
            "profit_factor_by_zone": {key: _profit_factor(values) for key, values in sorted(by_zone.items())},
        },
        "ofa_funnel_integrity": {
            "ofa_signal_rows": len(ofa_rows),
            "issues": len(ofa_issues),
            "examples": ofa_issues[:50],
        },
        "near_miss_usability_summary": {
            "total_near_miss_or_blocked_shadow": len(near_rows),
            "outcomes": dict(near_outcomes),
            "examples": near_rows[:80],
        },
        "data_quality_score_by_signal": {
            "rows": [
                {
                    "signal_id": row.get("signal_id"),
                    "symbol": row.get("symbol"),
                    "setup": row.get("setup"),
                    "score": row.get("data_quality_score"),
                    "mfe_known": row.get("mfe_r") is not None,
                    "mae_known": row.get("mae_r") is not None,
                    "data_gap_events": row.get("data_gap_events"),
                }
                for row in signal_rows
            ]
        },
        "source_diagnostics_references": {
            "lifecycle_keys": sorted(lifecycle.keys()),
            "deep_diagnostics_keys": sorted(diagnostics.keys()),
            "t02_diagnostics_keys": sorted(t02_diagnostics.keys()),
        },
    }
