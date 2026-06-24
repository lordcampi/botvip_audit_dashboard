"""F5_T04 Batch 2 diagnostics for BotVIP Daily AI Reporter.

Read-only/dashboard-local analytics only. This module does not read or write the
BotVIP DB directly, does not send Telegram, and does not modify strategy,
thresholds, scanner runtime, lifecycle runtime, Telegram runtime, or schema.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any, Iterable

BOGOTA_TZ = ZoneInfo("America/Bogota")
SCHEMA_VERSION = "f5_t04bcd_batch2_diagnostics_v1"
NO_PROGRESS_ROOT_CAUSE_FILENAME = "13_no_progress_root_cause_diagnostics.json"
ZONE_DIAGNOSTICS_V2_FILENAME = "14_zone_diagnostics_v2.json"
ZONE_MAPPING_QUALITY_FILENAME = "15_zone_mapping_quality.json"
ENTITY_SCOPE_RECONCILIATION_FILENAME = "16_entity_scope_reconciliation.json"


def _norm(value: Any, default: str = "unknown") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _lower(value: Any, default: str = "unknown") -> str:
    return _norm(value, default).lower()


def _upper(value: Any, default: str = "UNKNOWN") -> str:
    return _norm(value, default).upper()


def _is_true(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _boolish(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    raw = str(value).strip().lower()
    if raw in {"1", "true", "yes", "y", "on", "ok"}:
        return True
    if raw in {"0", "false", "no", "n", "off"}:
        return False
    return None


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
    clean = [v for v in (_num(x) for x in values) if v is not None]
    return None if not clean else round(sum(clean) / len(clean), 6)


def _parse_dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    text = str(value).strip().replace("T", " ")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text[:26], fmt)
            return dt.replace(tzinfo=BOGOTA_TZ)
        except Exception:
            pass
    try:
        dt = datetime.fromisoformat(text)
    except Exception:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=BOGOTA_TZ)
    return dt.astimezone(BOGOTA_TZ)


def _timestamp_for_row(row: dict[str, Any]) -> tuple[Any, str | None]:
    for key in ("opened_at", "created_at", "closed_at", "expires_at"):
        value = row.get(key)
        if value not in {None, ""}:
            return value, key
    return None, None


def _lifetime_seconds(row: dict[str, Any]) -> int | None:
    start = _parse_dt(row.get("opened_at") or row.get("created_at"))
    end = _parse_dt(row.get("closed_at"))
    if not start or not end:
        return None
    return max(0, int((end - start).total_seconds()))


def _profit_factor(rows: list[dict[str, Any]], r_key: str = "net_r") -> dict[str, Any]:
    values = [_num(row.get(r_key)) for row in rows]
    clean = [v for v in values if v is not None]
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


def resolve_operational_zone(row: dict[str, Any]) -> dict[str, Any]:
    timestamp_value, timestamp_field = _timestamp_for_row(row)
    dt = _parse_dt(timestamp_value)
    weekend_flag = _boolish(row.get("weekend"))
    killzone_flag = _boolish(row.get("killzone"))
    mode = _lower(row.get("operating_mode"), "")

    if dt is not None and dt.weekday() >= 5:
        return {
            "zone": "weekend",
            "method": "timestamp_weekend_priority",
            "source_timestamp_field_used": timestamp_field,
            "source_timestamp_value": timestamp_value,
            "timezone_used": "America/Bogota",
            "parse_error": None,
        }
    if weekend_flag is True:
        return {
            "zone": "weekend",
            "method": "explicit_weekend_flag",
            "source_timestamp_field_used": timestamp_field,
            "source_timestamp_value": timestamp_value,
            "timezone_used": "America/Bogota",
            "parse_error": None if dt is not None or timestamp_value is None else "timestamp_parse_failed",
        }
    if killzone_flag is True:
        return {
            "zone": "killzone",
            "method": "explicit_killzone_flag",
            "source_timestamp_field_used": timestamp_field,
            "source_timestamp_value": timestamp_value,
            "timezone_used": "America/Bogota",
            "parse_error": None if dt is not None or timestamp_value is None else "timestamp_parse_failed",
        }
    if killzone_flag is False:
        return {
            "zone": "outside_killzone",
            "method": "explicit_killzone_false",
            "source_timestamp_field_used": timestamp_field,
            "source_timestamp_value": timestamp_value,
            "timezone_used": "America/Bogota",
            "parse_error": None if dt is not None or timestamp_value is None else "timestamp_parse_failed",
        }
    if "killzone" in mode or "institucional activa" in mode:
        return {
            "zone": "killzone",
            "method": "operating_mode_label",
            "source_timestamp_field_used": timestamp_field,
            "source_timestamp_value": timestamp_value,
            "timezone_used": "America/Bogota",
            "parse_error": None if dt is not None or timestamp_value is None else "timestamp_parse_failed",
        }
    if mode and mode not in {"unknown", "none", "null"}:
        return {
            "zone": "outside_killzone",
            "method": "operating_mode_label",
            "source_timestamp_field_used": timestamp_field,
            "source_timestamp_value": timestamp_value,
            "timezone_used": "America/Bogota",
            "parse_error": None if dt is not None or timestamp_value is None else "timestamp_parse_failed",
        }
    return {
        "zone": "unknown_zone",
        "method": "unresolved_missing_flags_and_mode",
        "source_timestamp_field_used": timestamp_field,
        "source_timestamp_value": timestamp_value,
        "timezone_used": "America/Bogota",
        "parse_error": "missing_timestamp" if timestamp_value is None else "timestamp_parse_failed",
    }


def _zone_counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(row.get("resolved_zone", "unknown_zone") for row in rows)
    total = len(rows)
    return {
        "total": total,
        "counts": dict(counts.most_common()),
        "percentages": {key: round(value / max(1, total), 6) for key, value in counts.most_common()},
    }


def _resolved_rows(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for idx, row in enumerate(facts):
        resolved = resolve_operational_zone(row)
        item = dict(row)
        item["row_index"] = idx
        item["resolved_zone"] = resolved["zone"]
        item["zone_resolution_method"] = resolved["method"]
        item["source_timestamp_field_used"] = resolved["source_timestamp_field_used"]
        item["timezone_used"] = resolved["timezone_used"]
        item["zone_parse_error"] = resolved["parse_error"]
        out.append(item)
    return out


def _event_index(events: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        sid = _norm(event.get("signal_id"), "")
        if sid:
            buckets[sid].append(event)
    return dict(buckets)


def _event_price_flags(signal: dict[str, Any], events: list[dict[str, Any]]) -> tuple[bool, bool]:
    opened = _parse_dt(signal.get("opened_at") or signal.get("created_at"))
    closed = _parse_dt(signal.get("closed_at"))
    had_after_entry = False
    had_before_exit = False
    for event in events:
        if _num(event.get("price")) is None:
            continue
        etime = _parse_dt(event.get("event_time") or event.get("created_at"))
        if not etime:
            continue
        if opened and etime >= opened:
            had_after_entry = True
        if closed and etime <= closed:
            had_before_exit = True
    return had_after_entry, had_before_exit


def _classify_no_progress(row: dict[str, Any], had_after_entry: bool, had_before_exit: bool) -> tuple[str, str, list[str]]:
    notes: list[str] = []
    mfe = _num(row.get("mfe"))
    mae = _num(row.get("mae"))
    gaps = _num(row.get("data_gap_events")) or 0
    lifetime = _lifetime_seconds(row)

    if gaps > 0:
        notes.append("data_gap_events_present")
    if not had_after_entry:
        notes.append("no_price_event_after_entry")
    if row.get("closed_at") and not had_before_exit:
        notes.append("no_price_event_before_exit")
    if mfe is None and mae is None:
        notes.append("mfe_mae_missing")
    if lifetime is not None and lifetime < 180:
        notes.append("lifetime_under_180_seconds")

    if gaps > 0:
        return "data_gap_suspect", "low", notes
    if not had_after_entry:
        return "missing_price_path", "low", notes
    if lifetime is not None and lifetime < 180:
        return "insufficient_lifetime", "medium", notes
    if mfe is None and mae is None:
        return "unknown_due_to_missing_observability", "low", notes
    if mfe is not None and mfe >= 0.25:
        return "mfe_positive_but_failed_to_convert", "medium", notes
    if mae is not None and mae <= -0.25 and (mfe is None or mfe < 0.20):
        return "mae_immediate_adverse", "medium", notes
    return "real_no_progress", "medium", notes


def build_no_progress_root_cause_diagnostics(
    *,
    facts: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    indexed_events = _event_index(events)
    signals = [row for row in _resolved_rows(facts) if row.get("record_type") == "signal"]
    rows = [row for row in signals if _is_true(row.get("no_progress_exit")) or _lower(row.get("exit_reason")) == "no_progress"]
    diagnostics: list[dict[str, Any]] = []
    missing_reasons: list[dict[str, Any]] = []

    for row in rows:
        sid = _norm(row.get("signal_id"), "unknown")
        evs = indexed_events.get(sid, [])
        had_after_entry, had_before_exit = _event_price_flags(row, evs)
        classifier, confidence, notes = _classify_no_progress(row, had_after_entry, had_before_exit)
        mfe = _num(row.get("mfe"))
        mae = _num(row.get("mae"))
        item = {
            "signal_id": sid,
            "symbol": row.get("symbol"),
            "side": row.get("side"),
            "setup": row.get("setup_type"),
            "opened_at": row.get("opened_at"),
            "closed_at": row.get("closed_at"),
            "lifetime_seconds": _lifetime_seconds(row),
            "zone": row.get("resolved_zone"),
            "zone_resolution_method": row.get("zone_resolution_method"),
            "close_reason": row.get("exit_reason"),
            "mfe_r_known": mfe is not None,
            "mae_r_known": mae is not None,
            "mfe_r": mfe,
            "mae_r": mae,
            "data_gap_events": int(_num(row.get("data_gap_events")) or 0),
            "had_price_after_entry": had_after_entry,
            "had_price_before_exit": had_before_exit,
            "max_favorable_move_estimated": mfe,
            "max_adverse_move_estimated": mae,
            "classifier": classifier,
            "confidence": confidence,
            "evidence_notes": notes,
        }
        diagnostics.append(item)
        if sid == "unknown":
            missing_reasons.append({"row_index": row.get("row_index"), "reason": "missing_signal_id"})

    return {
        "schema_version": SCHEMA_VERSION,
        "section": "F5_T04b_no_progress_root_cause_diagnostics",
        "read_only": True,
        "official_no_progress_count": len(rows),
        "diagnostic_row_count": len(diagnostics),
        "missing_or_unmatched": missing_reasons,
        "classifier_counts": dict(Counter(row["classifier"] for row in diagnostics).most_common()),
        "confidence_counts": dict(Counter(row["confidence"] for row in diagnostics).most_common()),
        "rows": diagnostics,
        "guardrails": [
            "Official outcomes are not recalculated.",
            "Classifiers describe available evidence only.",
            "Low confidence is expected when price path or MFE/MAE is missing.",
        ],
    }


def build_zone_diagnostics_v2(*, facts: list[dict[str, Any]]) -> dict[str, Any]:
    rows = _resolved_rows(facts)
    signals = [row for row in rows if row.get("record_type") == "signal"]
    candidates = [row for row in rows if row.get("record_type") == "candidate"]
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in signals:
        buckets[row.get("resolved_zone", "unknown_zone")].append(row)
    return {
        "schema_version": SCHEMA_VERSION,
        "section": "F5_T04c_zone_diagnostics_v2",
        "read_only": True,
        "timezone_used": "America/Bogota",
        "signals": _zone_counts(signals),
        "candidates": _zone_counts(candidates),
        "all_rows": _zone_counts(rows),
        "profit_factor_by_zone": {key: _profit_factor(items) for key, items in sorted(buckets.items())},
        "no_progress_by_zone": {
            key: {
                "signals": len(items),
                "no_progress_exit": sum(1 for row in items if _is_true(row.get("no_progress_exit"))),
                "rate": round(sum(1 for row in items if _is_true(row.get("no_progress_exit"))) / max(1, len(items)), 6),
            }
            for key, items in sorted(buckets.items())
        },
        "examples_unresolved": [
            {
                "record_type": row.get("record_type"),
                "signal_id": row.get("signal_id"),
                "candidate_id": row.get("candidate_id"),
                "symbol": row.get("symbol"),
                "created_at": row.get("created_at"),
                "opened_at": row.get("opened_at"),
                "operating_mode": row.get("operating_mode"),
                "source_timestamp_field_used": row.get("source_timestamp_field_used"),
                "parse_error": row.get("zone_parse_error"),
            }
            for row in rows if row.get("resolved_zone") == "unknown_zone"
        ][:50],
        "guardrail": "Zone mapping is reporting-only and does not change runtime strategy decisions.",
    }


def build_zone_mapping_quality(*, facts: list[dict[str, Any]]) -> dict[str, Any]:
    rows = _resolved_rows(facts)
    method_counts = Counter(row.get("zone_resolution_method") for row in rows)
    timestamp_field_counts = Counter(_norm(row.get("source_timestamp_field_used"), "missing") for row in rows)
    unknown = [row for row in rows if row.get("resolved_zone") == "unknown_zone"]
    return {
        "schema_version": SCHEMA_VERSION,
        "section": "F5_T04c_zone_mapping_quality",
        "read_only": True,
        "timezone_used": "America/Bogota",
        "total_rows": len(rows),
        "resolved_rows": len(rows) - len(unknown),
        "unknown_zone_rows": len(unknown),
        "unknown_zone_rate": round(len(unknown) / max(1, len(rows)), 6),
        "method_counts": dict(method_counts.most_common()),
        "source_timestamp_field_counts": dict(timestamp_field_counts.most_common()),
        "parse_error_counts": dict(Counter(_norm(row.get("zone_parse_error"), "none") for row in rows).most_common()),
        "unknown_zone_is_dominant": len(unknown) > (len(rows) / 2) if rows else False,
        "examples_unresolved": [
            {
                "record_type": row.get("record_type"),
                "row_index": row.get("row_index"),
                "signal_id": row.get("signal_id"),
                "candidate_id": row.get("candidate_id"),
                "created_at": row.get("created_at"),
                "opened_at": row.get("opened_at"),
                "operating_mode": row.get("operating_mode"),
                "parse_error": row.get("zone_parse_error"),
            }
            for row in unknown[:50]
        ],
    }


def _derived_row_key(row: dict[str, Any], row_index: int) -> str:
    raw = "|".join([
        _norm(row.get("source_table"), "unknown_source"),
        _norm(row.get("symbol"), "unknown_symbol"),
        _norm(row.get("setup_type"), "unknown_setup"),
        _norm(row.get("created_at") or row.get("opened_at") or row.get("closed_at"), "unknown_timestamp"),
        str(row_index),
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def build_entity_scope_reconciliation(
    *,
    facts: list[dict[str, Any]],
    events: list[dict[str, Any]],
    signals: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    signal_facts = [row for row in facts if row.get("record_type") == "signal"]
    candidate_facts = [row for row in facts if row.get("record_type") == "candidate"]
    sent_signal_ids = sorted({_norm(row.get("signal_id")) for row in signal_facts if _is_true(row.get("sent_to_telegram"))})
    unknown_rows = []
    for idx, row in enumerate(facts):
        if row.get("record_type") != "signal" or _norm(row.get("signal_id"), "unknown") == "unknown":
            unknown_rows.append({
                "record_type": row.get("record_type"),
                "official_signal_id": row.get("signal_id") if row.get("record_type") == "signal" else None,
                "derived_row_key": _derived_row_key(row, idx),
                "source_table": row.get("source_table"),
                "symbol": row.get("symbol"),
                "setup": row.get("setup_type"),
                "timestamp": row.get("created_at") or row.get("opened_at") or row.get("closed_at"),
                "row_index": idx,
                "note": "derived_row_key is for analytics traceability only and is not an official signal_id",
            })
    return {
        "schema_version": SCHEMA_VERSION,
        "section": "F5_T04d_entity_scope_reconciliation",
        "read_only": True,
        "entities": {
            "official_signals": {
                "source": "signal_records / daily facts record_type=signal",
                "row_count_from_loader": len(signals),
                "row_count_from_facts": len(signal_facts),
                "countable_as_trades": True,
                "official_signal_id_field": "signal_id",
                "warning": "Use this entity for official signal/trade counts. Do not add derived rows as extra trades.",
            },
            "telegram_sent_signals": {
                "source": "signal_events NOTIFIED folded into daily facts sent_to_telegram",
                "row_count": len(sent_signal_ids),
                "countable_as_trades": False,
                "relationship": "subset_of_official_signals",
                "official_signal_ids_sample": sent_signal_ids[:50],
            },
            "candidate_snapshots": {
                "source": "scanner_candidate_shadow_snapshots / daily facts record_type=candidate",
                "row_count_from_loader": len(candidates),
                "row_count_from_facts": len(candidate_facts),
                "countable_as_trades": False,
                "identifier_field": "candidate_id",
                "warning": "Candidate snapshots are not official trades or official signals.",
            },
            "dashboard_derived_rows": {
                "source": "build_daily_facts output",
                "row_count": len(facts),
                "countable_as_trades": False,
                "warning": "Facts combine official signals and candidate snapshots for analytics. Do not sum all facts as trades.",
            },
            "data_quality_rows": {
                "source": "diagnostic rows derived from official signal facts",
                "row_count": len(signal_facts),
                "countable_as_trades": False,
                "relationship": "one_or_more_diagnostics_per_official_signal_possible",
            },
            "event_rows": {
                "source": "signal_events",
                "row_count": len(events),
                "countable_as_trades": False,
                "relationship": "many_events_to_one_official_signal",
            },
        },
        "record_type_counts_in_facts": dict(Counter(_norm(row.get("record_type")) for row in facts).most_common()),
        "source_table_counts_in_facts": dict(Counter(_norm(row.get("source_table")) for row in facts).most_common()),
        "unknown_or_derived_identity_rows": {
            "count": len(unknown_rows),
            "sample": unknown_rows[:100],
            "rule": "Do not invent official signal_id. Use derived_row_key only for dashboard-derived traceability.",
        },
        "do_not_double_count": [
            "Do not count candidate_snapshots as trades.",
            "Do not count dashboard_derived_rows as trades.",
            "Do not add CSV row counts to official signal counts.",
            "Use official_signals as the only official signal/trade denominator unless a section explicitly states otherwise.",
        ],
    }


def build_f5_t04bcd_diagnostics(
    *,
    facts: list[dict[str, Any]],
    events: list[dict[str, Any]],
    signals: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "no_progress_root_cause_diagnostics": build_no_progress_root_cause_diagnostics(facts=facts, events=events),
        "zone_diagnostics_v2": build_zone_diagnostics_v2(facts=facts),
        "zone_mapping_quality": build_zone_mapping_quality(facts=facts),
        "entity_scope_reconciliation": build_entity_scope_reconciliation(
            facts=facts,
            events=events,
            signals=signals,
            candidates=candidates,
        ),
    }
