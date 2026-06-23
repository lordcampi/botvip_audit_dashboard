from __future__ import annotations

import csv
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from .parsers import as_bool, as_float, first_path, parse_json_safe

EVENT_ALIASES = {
    "created": {"CREATED", "SIGNAL_CREATED"},
    "activated": {"ACTIVATED", "SIGNAL_ACTIVATED"},
    "notified": {"NOTIFIED"},
    "cancelled": {"CANCELLED_EXPIRED", "SIGNAL_CANCELLED"},
    # F4 official lifecycle: PRIMARY_TP_HIT is the official TP event.
    # PARTIAL_TP_HIT and TAKE_PROFIT_HIT are legacy/secondary signals and must not inflate official primary TP.
    "primary_tp": {"PRIMARY_TP_HIT"},
    "legacy_tp": {"PARTIAL_TP_HIT", "TAKE_PROFIT_HIT"},
    "real_stop_loss": {"STOP_LOSS_HIT"},
    "breakeven_armed": {"BREAKEVEN_ARMED"},
    "sl_moved_to_breakeven": {"SL_MOVED_TO_BREAKEVEN"},
    "breakeven_stop": {"BREAKEVEN_STOP_HIT"},
    "runner_breakeven": {"RUNNER_BREAKEVEN_STOP_HIT"},
    "runner_tp": {"RUNNER_TP_HIT"},
    "time_stop": {"TIME_STOP_EXIT"},
    "no_progress": {"NO_PROGRESS_EXIT", "MFE_STALL_EXIT"},
    "data_gap": {"DATA_GAP_DETECTED"},
}

FACT_COLUMNS = [
    "record_type", "signal_id", "candidate_id", "cycle_id", "created_at", "source_table",
    "symbol", "side", "setup_type", "engine_name", "operating_mode", "market_regime", "btc_trend", "weekend", "killzone",
    "score", "min_score", "score_margin", "adx", "rvol", "atr_extension",
    "passed_adx_18", "passed_adx_20", "passed_adx_22", "passed_rvol_1_0", "passed_rvol_1_1", "passed_rvol_1_2",
    "liquidity_zone_ok", "sweep_ok", "reclaim_ok", "execution_ok", "risk_ok", "signal_complete", "telegram_notified", "lifecycle_started",
    "entry_price", "tp_price", "primary_tp_price", "sl_price", "primary_tp_distance", "sl_distance", "gross_rr", "net_rr", "estimated_cost",
    "sent_to_telegram", "blocked", "blocked_reason", "near_miss", "would_send_signal", "reason_if_rejected", "hypothetical_result", "hypothetical_exit_reason",
    "status", "official_result", "official_result_locked", "exit_reason", "primary_tp_hit", "real_stop_loss_hit", "breakeven_stop_hit",
    "runner_tp_hit", "runner_breakeven_stop_hit", "time_stop_exit", "cancelled", "no_progress_exit", "mfe_stall_exit",
    "pnl_r", "gross_r", "net_r", "runner_extra_r", "mfe", "mae",
    "opened_at", "closed_at", "expires_at", "time_to_entry_minutes", "time_to_primary_tp_minutes", "time_to_close_minutes",
    "data_gap_events", "event_count", "event_types",
]


def parse_dt(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    text = str(value).strip().replace("T", " ")
    if text.endswith("Z"):
        text = text[:-1]
    for fmt in ["%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
        try:
            return datetime.strptime(text[:26], fmt)
        except Exception:
            pass
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


def minutes_between(start: Any, end: Any) -> Optional[float]:
    a = parse_dt(start)
    b = parse_dt(end)
    if not a or not b:
        return None
    return round((b - a).total_seconds() / 60.0, 4)


def nested_value(payload: Any, paths: Iterable[str], default: Any = None) -> Any:
    parsed = parse_json_safe(payload, default={})
    return first_path(parsed, paths, default=default)


def normalize_regime(value: Any) -> Any:
    parsed = parse_json_safe(value, default=None)
    if isinstance(parsed, dict):
        return parsed.get("regime") or parsed.get("label") or parsed.get("type") or parsed.get("name") or value
    return value


def calc_distance(entry: Any, target: Any) -> Optional[float]:
    e = as_float(entry)
    t = as_float(target)
    if e is None or t is None:
        return None
    return abs(t - e)


def build_event_index(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "event_count": 0,
        "event_types": set(),
        "first_event_at": None,
        "primary_tp_at": None,
        "activated_at": None,
        "notified": False,
        "primary_tp_hit": False,
        "legacy_tp_hit": False,
        "stop_loss_event_seen": False,
        "breakeven_armed": False,
        "sl_moved_to_breakeven": False,
        "breakeven_stop_hit": False,
        "runner_tp_hit": False,
        "runner_breakeven_stop_hit": False,
        "time_stop_exit": False,
        "cancelled": False,
        "no_progress_exit": False,
        "mfe_stall_exit": False,
        "data_gap_events": 0,
    })
    for event in events:
        sid = str(event.get("signal_id")) if event.get("signal_id") is not None else None
        if not sid:
            continue
        etype = str(event.get("event_type") or "")
        item = index[sid]
        item["event_count"] += 1
        item["event_types"].add(etype)
        ev_time = event.get("event_time")
        if item["first_event_at"] is None:
            item["first_event_at"] = ev_time
        if etype in EVENT_ALIASES["activated"] and item["activated_at"] is None:
            item["activated_at"] = ev_time
        if etype in EVENT_ALIASES["notified"]:
            item["notified"] = True
        if etype in EVENT_ALIASES["primary_tp"]:
            item["primary_tp_hit"] = True
            if item["primary_tp_at"] is None:
                item["primary_tp_at"] = ev_time
        if etype in EVENT_ALIASES["legacy_tp"]:
            item["legacy_tp_hit"] = True
        if etype in EVENT_ALIASES["real_stop_loss"]:
            item["stop_loss_event_seen"] = True
        if etype in EVENT_ALIASES["breakeven_armed"]:
            item["breakeven_armed"] = True
        if etype in EVENT_ALIASES["sl_moved_to_breakeven"]:
            item["sl_moved_to_breakeven"] = True
        if etype in EVENT_ALIASES["breakeven_stop"]:
            item["breakeven_stop_hit"] = True
        if etype in EVENT_ALIASES["runner_tp"]:
            item["runner_tp_hit"] = True
        if etype in EVENT_ALIASES["runner_breakeven"]:
            item["runner_breakeven_stop_hit"] = True
        if etype in EVENT_ALIASES["time_stop"]:
            item["time_stop_exit"] = True
        if etype in EVENT_ALIASES["cancelled"]:
            item["cancelled"] = True
        if etype == "NO_PROGRESS_EXIT":
            item["no_progress_exit"] = True
        if etype == "MFE_STALL_EXIT":
            item["mfe_stall_exit"] = True
        if etype in EVENT_ALIASES["data_gap"]:
            item["data_gap_events"] += 1
    for item in index.values():
        item["event_types"] = ",".join(sorted(item["event_types"]))
    return dict(index)


def empty_fact() -> dict[str, Any]:
    return {col: None for col in FACT_COLUMNS}


def signal_to_fact(signal: dict[str, Any], event_index: dict[str, dict[str, Any]]) -> dict[str, Any]:
    fact = empty_fact()
    sid = str(signal.get("id"))
    metrics = parse_json_safe(signal.get("metrics_json"), default={})
    events = event_index.get(sid, {})
    status_upper = str(signal.get("status") or "").upper()
    exit_reason_lower = str(signal.get("exit_reason") or "").lower()

    fact.update({
        "record_type": "signal",
        "signal_id": sid,
        "created_at": signal.get("created_at"),
        "source_table": "signal_records",
        "symbol": signal.get("symbol"),
        "side": signal.get("signal_type"),
        "setup_type": signal.get("setup") or nested_value(metrics, ["setup_type"]),
        "engine_name": signal.get("engine_name"),
        "operating_mode": signal.get("operating_mode"),
        "market_regime": signal.get("market_regime") or normalize_regime(nested_value(metrics, ["regime", "market_regime"])),
        "btc_trend": signal.get("btc_trend") or nested_value(metrics, ["btc_trend", "market_regime.btc_trend"]),
        "weekend": as_bool(signal.get("weekend")),
        "killzone": as_bool(signal.get("in_killzone")),
        "score": as_float(signal.get("score")) if signal.get("score") is not None else as_float(nested_value(metrics, ["score"])),
        "min_score": as_float(signal.get("min_score")) if signal.get("min_score") is not None else as_float(nested_value(metrics, ["min_score"])),
        "rvol": as_float(nested_value(metrics, ["rvol"])),
        "entry_price": as_float(signal.get("entry_price")),
        "tp_price": as_float(signal.get("tp_price")),
        "primary_tp_price": as_float(nested_value(metrics, ["primary_tp_price", "official_result.primary_tp_price", "initial_geometry.primary_tp_price"])),
        "sl_price": as_float(signal.get("sl_price")),
        "gross_rr": as_float(signal.get("gross_rr")),
        "net_rr": as_float(signal.get("net_rr")),
        "estimated_cost": as_float(signal.get("estimated_cost")),
        "status": signal.get("status"),
        "official_result": nested_value(metrics, ["official_result.official_result", "official_result"]),
        "official_result_locked": as_bool(nested_value(metrics, ["official_result_locked", "official_result.official_result_locked"])),
        "exit_reason": signal.get("exit_reason"),
        "pnl_r": as_float(signal.get("pnl_r")),
        "gross_r": as_float(signal.get("gross_r")),
        "net_r": as_float(signal.get("net_r")),
        "opened_at": signal.get("opened_at"),
        "closed_at": signal.get("closed_at"),
        "expires_at": signal.get("expires_at"),
    })

    ofa = nested_value(metrics, ["ofa_funnel"], default={}) or {}
    if isinstance(ofa, dict):
        for key in ["liquidity_zone_ok", "sweep_ok", "reclaim_ok", "execution_ok", "risk_ok", "signal_complete", "telegram_notified", "lifecycle_started"]:
            fact[key] = as_bool(ofa.get(key))

    official_result = fact.get("official_result")
    primary_tp_hit = bool(events.get("primary_tp_hit")) or status_upper == "WON" or official_result == "WIN"
    breakeven_stop_hit = bool(events.get("breakeven_stop_hit")) or exit_reason_lower == "breakeven_stop"
    runner_breakeven_stop_hit = bool(events.get("runner_breakeven_stop_hit")) or exit_reason_lower == "runner_breakeven_stop"
    non_real_sl_exit = exit_reason_lower in {"breakeven_stop", "runner_breakeven_stop", "time_stop", "no_progress", "mfe_stall", "expired_pending", "take_profit"}
    real_stop_loss_hit = (exit_reason_lower == "stop_loss" or status_upper == "LOST" or bool(events.get("stop_loss_event_seen")))
    if primary_tp_hit or breakeven_stop_hit or runner_breakeven_stop_hit or non_real_sl_exit:
        real_stop_loss_hit = exit_reason_lower == "stop_loss" or status_upper == "LOST"

    fact["score_margin"] = None if fact["score"] is None or fact["min_score"] is None else fact["score"] - fact["min_score"]
    fact["primary_tp_distance"] = calc_distance(fact["entry_price"], fact["primary_tp_price"] or fact["tp_price"])
    fact["sl_distance"] = calc_distance(fact["entry_price"], fact["sl_price"])
    fact["sent_to_telegram"] = bool(events.get("notified"))
    fact["primary_tp_hit"] = primary_tp_hit
    fact["real_stop_loss_hit"] = real_stop_loss_hit
    fact["breakeven_stop_hit"] = breakeven_stop_hit
    fact["runner_tp_hit"] = bool(events.get("runner_tp_hit"))
    fact["runner_breakeven_stop_hit"] = runner_breakeven_stop_hit
    fact["time_stop_exit"] = bool(events.get("time_stop_exit")) or exit_reason_lower == "time_stop"
    fact["cancelled"] = bool(events.get("cancelled")) or status_upper == "EXPIRED"
    fact["no_progress_exit"] = bool(events.get("no_progress_exit")) or exit_reason_lower == "no_progress"
    fact["mfe_stall_exit"] = bool(events.get("mfe_stall_exit")) or exit_reason_lower == "mfe_stall"
    fact["data_gap_events"] = events.get("data_gap_events", 0)
    fact["event_count"] = events.get("event_count", 0)
    fact["event_types"] = events.get("event_types")
    fact["time_to_entry_minutes"] = minutes_between(fact["created_at"], fact["opened_at"])
    fact["time_to_primary_tp_minutes"] = minutes_between(fact["created_at"], events.get("primary_tp_at"))
    fact["time_to_close_minutes"] = minutes_between(fact["created_at"], fact["closed_at"])
    return fact


def candidate_to_fact(candidate: dict[str, Any]) -> dict[str, Any]:
    fact = empty_fact()
    meta = parse_json_safe(candidate.get("metadata_json"), default={})
    reason = candidate.get("reason") or nested_value(meta, ["reason_if_rejected"])
    score = as_float(candidate.get("score")) if candidate.get("score") is not None else as_float(nested_value(meta, ["score"]))
    min_score = as_float(nested_value(meta, ["min_score", "adaptive_thresholds.min_score"], default=None))
    ofa = nested_value(meta, ["ofa_funnel"], default={}) or {}

    fact.update({
        "record_type": "candidate",
        "candidate_id": candidate.get("id"),
        "cycle_id": candidate.get("cycle_id"),
        "created_at": candidate.get("created_at"),
        "source_table": "scanner_candidate_shadow_snapshots",
        "symbol": candidate.get("symbol"),
        "side": nested_value(meta, ["side"]),
        "setup_type": nested_value(meta, ["setup_type"]),
        "operating_mode": candidate.get("mode"),
        "market_regime": normalize_regime(nested_value(meta, ["regime", "market_regime"])),
        "score": score,
        "min_score": min_score,
        "adx": as_float(candidate.get("adx")),
        "rvol": as_float(candidate.get("rvol")) if candidate.get("rvol") is not None else as_float(nested_value(meta, ["rvol"])),
        "atr_extension": as_float(candidate.get("atr_extension")),
        "passed_adx_18": as_bool(candidate.get("passed_adx_18")),
        "passed_adx_20": as_bool(candidate.get("passed_adx_20")),
        "passed_adx_22": as_bool(candidate.get("passed_adx_22")),
        "passed_rvol_1_0": as_bool(candidate.get("passed_rvol_1_0")),
        "passed_rvol_1_1": as_bool(candidate.get("passed_rvol_1_1")),
        "passed_rvol_1_2": as_bool(candidate.get("passed_rvol_1_2")),
        "entry_price": as_float(nested_value(meta, ["entry"])),
        "tp_price": as_float(nested_value(meta, ["tp"])),
        "sl_price": as_float(nested_value(meta, ["sl"])),
        "gross_rr": as_float(nested_value(meta, ["gross_rr"])),
        "net_rr": as_float(nested_value(meta, ["net_rr"])),
        "estimated_cost": as_float(nested_value(meta, ["estimated_cost"])),
        "blocked": reason not in {None, "", "ofa_shadow_ok"},
        "blocked_reason": reason,
        "near_miss": as_bool(nested_value(meta, ["near_miss"]), default=False) or reason == "near_miss_chop",
        "would_send_signal": as_bool(nested_value(meta, ["would_send_signal"]), default=False),
        "reason_if_rejected": nested_value(meta, ["reason_if_rejected"]),
        "hypothetical_result": nested_value(meta, ["hypothetical_result"]),
        "hypothetical_exit_reason": nested_value(meta, ["hypothetical_exit_reason"]),
        "mfe": as_float(nested_value(meta, ["mfe"])),
        "mae": as_float(nested_value(meta, ["mae"])),
    })

    if isinstance(ofa, dict):
        for key in ["liquidity_zone_ok", "sweep_ok", "reclaim_ok", "execution_ok", "risk_ok", "signal_complete", "telegram_notified", "lifecycle_started"]:
            fact[key] = as_bool(ofa.get(key))

    fact["score_margin"] = None if fact["score"] is None or fact["min_score"] is None else fact["score"] - fact["min_score"]
    fact["primary_tp_distance"] = calc_distance(fact["entry_price"], fact["tp_price"])
    fact["sl_distance"] = calc_distance(fact["entry_price"], fact["sl_price"])
    return fact


def build_daily_facts(events: list[dict[str, Any]], signals: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    event_index = build_event_index(events)
    facts = [signal_to_fact(row, event_index) for row in signals]
    facts.extend(candidate_to_fact(row) for row in candidates)
    return facts


def write_facts_csv(facts: list[dict[str, Any]], path: str | Path) -> None:
    selected = Path(path)
    selected.parent.mkdir(parents=True, exist_ok=True)
    with selected.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FACT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for fact in facts:
            writer.writerow({col: fact.get(col) for col in FACT_COLUMNS})
