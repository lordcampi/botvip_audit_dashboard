"""
F5_T12 — Batch 3: No-Progress Compact Optimizer.

Generates 02_no_progress_compact.json from signal_records.
Diagnoses root causes of signals that don't advance (no-progress exits).

Usage:
    python src/f5_t12_generate_no_progress_compact.py [--db data/trading_bot.db] [--output data/02_no_progress_compact.json]
"""

import argparse
import json
import math
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.f5_t12_calibration_core import (
    safe_float,
    parse_json_safe,
    normalize_session,
    compute_weekend_flag,
    bucket_exit_family,
    is_managed_exit,
    is_directional_exit,
    extract_r_values,
    compute_profit_factor_stats,
    classify_decision,
    segment_rows,
    compute_no_progress_stats,
    _classify_no_progress_action,
    compact_json_serialize,
    write_compact_json,
)
from src.db_readonly import connect_readonly


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def load_signal_records(db_path: str) -> List[dict]:
    """Load all signal_records from the database as dicts."""
    conn = connect_readonly(db_path)
    cursor = conn.execute("SELECT * FROM signal_records ORDER BY created_at")
    columns = [desc[0] for desc in cursor.description]
    rows = cursor.fetchall()
    conn.close()
    return [dict(zip(columns, row)) for row in rows]


def enrich_row(row: dict) -> dict:
    """Add computed fields to a row."""
    enriched = dict(row)
    enriched["exit_family"] = bucket_exit_family(row)
    enriched["session_bucket"] = normalize_session(row.get("created_at"))
    enriched["is_weekend"] = compute_weekend_flag(row)
    return enriched


# ---------------------------------------------------------------------------
# No-Progress segment definitions
# ---------------------------------------------------------------------------

NO_PROGRESS_SEGMENTS = [
    "ALL",
    "symbol",
    "signal_type",
    "market_regime",
    "weekend",
    "session_bucket",
    "symbol+market_regime",
    "symbol+btc_conflict",
    "symbol+low_vol",
    "managed_direct_context",
]


def _compute_btc_conflict(row: dict) -> bool:
    """Check if signal direction conflicts with BTC trend."""
    signal_type = str(row.get("signal_type", "")).strip().upper()
    btc_trend = str(row.get("btc_trend", "")).strip().lower()
    if signal_type == "LONG" and btc_trend == "bearish":
        return True
    if signal_type == "SHORT" and btc_trend == "bullish":
        return True
    return False


def _compute_low_vol(row: dict) -> bool:
    """Check if signal has low volume context."""
    quote_vol = safe_float(row.get("quote_volume"))
    if quote_vol is not None and quote_vol < 1000000:  # < $1M
        return True
    return False


def _compute_spread_sensitive(row: dict) -> bool:
    """Check if spread is high enough to affect entry."""
    spread = safe_float(row.get("spread_pct"))
    if spread is not None and spread > 0.05:  # > 0.05%
        return True
    return False


def _compute_entered_too_late(row: dict) -> bool:
    """Check if signal was entered too late based on metrics."""
    meta = parse_json_safe(row.get("metrics_json"))
    entry_delay = safe_float(meta.get("entry_delay_seconds") or meta.get("entry_delay"))
    if entry_delay is not None and entry_delay > 30:  # > 30 seconds delay
        return True
    return False


def _get_top_symbols(rows: List[dict], limit: int = 5) -> List[str]:
    """Get top symbols by count from a list of rows."""
    counts: Dict[str, int] = defaultdict(int)
    for r in rows:
        sym = str(r.get("symbol", "UNKNOWN"))
        counts[sym] += 1
    sorted_syms = sorted(counts.items(), key=lambda x: -x[1])
    return [sym for sym, _ in sorted_syms[:limit]]


# ---------------------------------------------------------------------------
# No-Progress segment builder
# ---------------------------------------------------------------------------

def build_no_progress_segments(
    all_rows: List[dict],
    np_rows: List[dict],
    sent_count: int,
    min_count: int = 5,
) -> List[dict]:
    """Build no-progress compact table across segments.

    Returns a list of dicts, one per segment, with root cause analysis.
    """
    enriched_all = [enrich_row(r) for r in all_rows]
    enriched_np = [enrich_row(r) for r in np_rows]
    results: List[dict] = []

    # --- ALL ---
    if enriched_np:
        stats = compute_no_progress_stats(enriched_np)
        results.append({
            "segment_type": "ALL",
            "segment": "ALL",
            "count": stats["count"],
            "rate_over_sent": round(stats["count"] / max(1, sent_count), 4),
            "avg_net_r": stats["avg_net_r"],
            "gross_loss_abs_r": stats["gross_loss_abs_r"],
            "avg_mfe_r": stats["avg_mfe_r"],
            "avg_mae_r": stats["avg_mae_r"],
            "mfe_zero_count": stats["mfe_zero_count"],
            "mfe_lt_0_15r_count": stats["mfe_lt_0_15r_count"],
            "adverse_first_minutes_count": stats["adverse_first_minutes_count"],
            "low_vol_count": stats["low_vol_count"],
            "btc_conflict_count": stats["btc_conflict_count"],
            "spread_sensitive_count": stats["spread_sensitive_count"],
            "entered_too_late_count": stats["entered_too_late_count"],
            "top_symbols": _get_top_symbols(enriched_np),
            "action": stats["action"],
        })

    # --- By symbol ---
    symbol_segments = segment_rows(enriched_np, "symbol")
    for sym, sym_rows in sorted(symbol_segments.items(), key=lambda x: -len(x[1])):
        if len(sym_rows) < min_count:
            continue
        stats = compute_no_progress_stats(sym_rows)
        results.append({
            "segment_type": "symbol",
            "segment": str(sym),
            "count": stats["count"],
            "rate_over_sent": round(stats["count"] / max(1, sent_count), 4),
            "avg_net_r": stats["avg_net_r"],
            "gross_loss_abs_r": stats["gross_loss_abs_r"],
            "avg_mfe_r": stats["avg_mfe_r"],
            "avg_mae_r": stats["avg_mae_r"],
            "mfe_zero_count": stats["mfe_zero_count"],
            "mfe_lt_0_15r_count": stats["mfe_lt_0_15r_count"],
            "adverse_first_minutes_count": stats["adverse_first_minutes_count"],
            "low_vol_count": stats["low_vol_count"],
            "btc_conflict_count": stats["btc_conflict_count"],
            "spread_sensitive_count": stats["spread_sensitive_count"],
            "entered_too_late_count": stats["entered_too_late_count"],
            "top_symbols": [str(sym)],
            "action": stats["action"],
        })

    # --- By signal_type ---
    st_segments = segment_rows(enriched_np, "signal_type")
    for st, st_rows in st_segments.items():
        if len(st_rows) < min_count:
            continue
        stats = compute_no_progress_stats(st_rows)
        results.append({
            "segment_type": "signal_type",
            "segment": str(st),
            "count": stats["count"],
            "rate_over_sent": round(stats["count"] / max(1, sent_count), 4),
            "avg_net_r": stats["avg_net_r"],
            "gross_loss_abs_r": stats["gross_loss_abs_r"],
            "avg_mfe_r": stats["avg_mfe_r"],
            "avg_mae_r": stats["avg_mae_r"],
            "mfe_zero_count": stats["mfe_zero_count"],
            "mfe_lt_0_15r_count": stats["mfe_lt_0_15r_count"],
            "adverse_first_minutes_count": stats["adverse_first_minutes_count"],
            "low_vol_count": stats["low_vol_count"],
            "btc_conflict_count": stats["btc_conflict_count"],
            "spread_sensitive_count": stats["spread_sensitive_count"],
            "entered_too_late_count": stats["entered_too_late_count"],
            "top_symbols": _get_top_symbols(st_rows),
            "action": stats["action"],
        })

    # --- By market_regime ---
    mr_segments = segment_rows(enriched_np, "market_regime")
    for mr, mr_rows in mr_segments.items():
        if len(mr_rows) < min_count:
            continue
        stats = compute_no_progress_stats(mr_rows)
        results.append({
            "segment_type": "market_regime",
            "segment": str(mr),
            "count": stats["count"],
            "rate_over_sent": round(stats["count"] / max(1, sent_count), 4),
            "avg_net_r": stats["avg_net_r"],
            "gross_loss_abs_r": stats["gross_loss_abs_r"],
            "avg_mfe_r": stats["avg_mfe_r"],
            "avg_mae_r": stats["avg_mae_r"],
            "mfe_zero_count": stats["mfe_zero_count"],
            "mfe_lt_0_15r_count": stats["mfe_lt_0_15r_count"],
            "adverse_first_minutes_count": stats["adverse_first_minutes_count"],
            "low_vol_count": stats["low_vol_count"],
            "btc_conflict_count": stats["btc_conflict_count"],
            "spread_sensitive_count": stats["spread_sensitive_count"],
            "entered_too_late_count": stats["entered_too_late_count"],
            "top_symbols": _get_top_symbols(mr_rows),
            "action": stats["action"],
        })

    # --- By weekend ---
    wk_segments = segment_rows(enriched_np, "weekend")
    for wk, wk_rows in wk_segments.items():
        if len(wk_rows) < min_count:
            continue
        stats = compute_no_progress_stats(wk_rows)
        results.append({
            "segment_type": "weekend",
            "segment": str(wk),
            "count": stats["count"],
            "rate_over_sent": round(stats["count"] / max(1, sent_count), 4),
            "avg_net_r": stats["avg_net_r"],
            "gross_loss_abs_r": stats["gross_loss_abs_r"],
            "avg_mfe_r": stats["avg_mfe_r"],
            "avg_mae_r": stats["avg_mae_r"],
            "mfe_zero_count": stats["mfe_zero_count"],
            "mfe_lt_0_15r_count": stats["mfe_lt_0_15r_count"],
            "adverse_first_minutes_count": stats["adverse_first_minutes_count"],
            "low_vol_count": stats["low_vol_count"],
            "btc_conflict_count": stats["btc_conflict_count"],
            "spread_sensitive_count": stats["spread_sensitive_count"],
            "entered_too_late_count": stats["entered_too_late_count"],
            "top_symbols": _get_top_symbols(wk_rows),
            "action": stats["action"],
        })

    # --- By session_bucket ---
    sb_segments = segment_rows(enriched_np, "session_bucket")
    for sb, sb_rows in sb_segments.items():
        if len(sb_rows) < min_count:
            continue
        stats = compute_no_progress_stats(sb_rows)
        results.append({
            "segment_type": "session_bucket",
            "segment": str(sb),
            "count": stats["count"],
            "rate_over_sent": round(stats["count"] / max(1, sent_count), 4),
            "avg_net_r": stats["avg_net_r"],
            "gross_loss_abs_r": stats["gross_loss_abs_r"],
            "avg_mfe_r": stats["avg_mfe_r"],
            "avg_mae_r": stats["avg_mae_r"],
            "mfe_zero_count": stats["mfe_zero_count"],
            "mfe_lt_0_15r_count": stats["mfe_lt_0_15r_count"],
            "adverse_first_minutes_count": stats["adverse_first_minutes_count"],
            "low_vol_count": stats["low_vol_count"],
            "btc_conflict_count": stats["btc_conflict_count"],
            "spread_sensitive_count": stats["spread_sensitive_count"],
            "entered_too_late_count": stats["entered_too_late_count"],
            "top_symbols": _get_top_symbols(sb_rows),
            "action": stats["action"],
        })

    # --- By symbol+market_regime ---
    sym_mr_groups: Dict[str, List[dict]] = defaultdict(list)
    for r in enriched_np:
        key = f"{r.get('symbol', 'UNKNOWN')}+{r.get('market_regime', 'unknown')}"
        sym_mr_groups[key].append(r)
    for key, group_rows in sorted(sym_mr_groups.items(), key=lambda x: -len(x[1])):
        if len(group_rows) < min_count:
            continue
        stats = compute_no_progress_stats(group_rows)
        results.append({
            "segment_type": "symbol+market_regime",
            "segment": key,
            "count": stats["count"],
            "rate_over_sent": round(stats["count"] / max(1, sent_count), 4),
            "avg_net_r": stats["avg_net_r"],
            "gross_loss_abs_r": stats["gross_loss_abs_r"],
            "avg_mfe_r": stats["avg_mfe_r"],
            "avg_mae_r": stats["avg_mae_r"],
            "mfe_zero_count": stats["mfe_zero_count"],
            "mfe_lt_0_15r_count": stats["mfe_lt_0_15r_count"],
            "adverse_first_minutes_count": stats["adverse_first_minutes_count"],
            "low_vol_count": stats["low_vol_count"],
            "btc_conflict_count": stats["btc_conflict_count"],
            "spread_sensitive_count": stats["spread_sensitive_count"],
            "entered_too_late_count": stats["entered_too_late_count"],
            "top_symbols": [key.split("+")[0]],
            "action": stats["action"],
        })

    # --- By symbol+btc_conflict ---
    sym_btc_groups: Dict[str, List[dict]] = defaultdict(list)
    for r in enriched_np:
        conflict = _compute_btc_conflict(r)
        key = f"{r.get('symbol', 'UNKNOWN')}+btc_conflict={conflict}"
        sym_btc_groups[key].append(r)
    for key, group_rows in sorted(sym_btc_groups.items(), key=lambda x: -len(x[1])):
        if len(group_rows) < min_count:
            continue
        stats = compute_no_progress_stats(group_rows)
        results.append({
            "segment_type": "symbol+btc_conflict",
            "segment": key,
            "count": stats["count"],
            "rate_over_sent": round(stats["count"] / max(1, sent_count), 4),
            "avg_net_r": stats["avg_net_r"],
            "gross_loss_abs_r": stats["gross_loss_abs_r"],
            "avg_mfe_r": stats["avg_mfe_r"],
            "avg_mae_r": stats["avg_mae_r"],
            "mfe_zero_count": stats["mfe_zero_count"],
            "mfe_lt_0_15r_count": stats["mfe_lt_0_15r_count"],
            "adverse_first_minutes_count": stats["adverse_first_minutes_count"],
            "low_vol_count": stats["low_vol_count"],
            "btc_conflict_count": stats["btc_conflict_count"],
            "spread_sensitive_count": stats["spread_sensitive_count"],
            "entered_too_late_count": stats["entered_too_late_count"],
            "top_symbols": [key.split("+")[0]],
            "action": stats["action"],
        })

    # --- By symbol+low_vol ---
    sym_lv_groups: Dict[str, List[dict]] = defaultdict(list)
    for r in enriched_np:
        low_vol = _compute_low_vol(r)
        key = f"{r.get('symbol', 'UNKNOWN')}+low_vol={low_vol}"
        sym_lv_groups[key].append(r)
    for key, group_rows in sorted(sym_lv_groups.items(), key=lambda x: -len(x[1])):
        if len(group_rows) < min_count:
            continue
        stats = compute_no_progress_stats(group_rows)
        results.append({
            "segment_type": "symbol+low_vol",
            "segment": key,
            "count": stats["count"],
            "rate_over_sent": round(stats["count"] / max(1, sent_count), 4),
            "avg_net_r": stats["avg_net_r"],
            "gross_loss_abs_r": stats["gross_loss_abs_r"],
            "avg_mfe_r": stats["avg_mfe_r"],
            "avg_mae_r": stats["avg_mae_r"],
            "mfe_zero_count": stats["mfe_zero_count"],
            "mfe_lt_0_15r_count": stats["mfe_lt_0_15r_count"],
            "adverse_first_minutes_count": stats["adverse_first_minutes_count"],
            "low_vol_count": stats["low_vol_count"],
            "btc_conflict_count": stats["btc_conflict_count"],
            "spread_sensitive_count": stats["spread_sensitive_count"],
            "entered_too_late_count": stats["entered_too_late_count"],
            "top_symbols": [key.split("+")[0]],
            "action": stats["action"],
        })

    # --- By managed/direct context ---
    managed_np = [r for r in enriched_np if is_managed_exit(r.get("exit_family", ""))]
    if managed_np:
        stats = compute_no_progress_stats(managed_np)
        results.append({
            "segment_type": "managed_direct_context",
            "segment": "managed_exits",
            "count": stats["count"],
            "rate_over_sent": round(stats["count"] / max(1, sent_count), 4),
            "avg_net_r": stats["avg_net_r"],
            "gross_loss_abs_r": stats["gross_loss_abs_r"],
            "avg_mfe_r": stats["avg_mfe_r"],
            "avg_mae_r": stats["avg_mae_r"],
            "mfe_zero_count": stats["mfe_zero_count"],
            "mfe_lt_0_15r_count": stats["mfe_lt_0_15r_count"],
            "adverse_first_minutes_count": stats["adverse_first_minutes_count"],
            "low_vol_count": stats["low_vol_count"],
            "btc_conflict_count": stats["btc_conflict_count"],
            "spread_sensitive_count": stats["spread_sensitive_count"],
            "entered_too_late_count": stats["entered_too_late_count"],
            "top_symbols": _get_top_symbols(managed_np),
            "action": stats["action"],
        })

    # Sort by count descending
    results.sort(key=lambda x: -x["count"])
    return results


def get_top_examples(np_rows: List[dict], limit: int = 10) -> List[dict]:
    """Get top no-progress examples for debugging."""
    enriched = [enrich_row(r) for r in np_rows]
    # Sort by worst net_r first
    sorted_rows = sorted(enriched, key=lambda x: safe_float(x.get("net_r")) or 0)
    examples = []
    for r in sorted_rows[:limit]:
        meta = parse_json_safe(r.get("metrics_json"))
        examples.append({
            "id": r.get("id"),
            "symbol": r.get("symbol"),
            "signal_type": r.get("signal_type"),
            "net_r": safe_float(r.get("net_r")),
            "mfe": safe_float(meta.get("mfe")),
            "mae": safe_float(meta.get("mae")),
            "btc_trend": r.get("btc_trend"),
            "spread_pct": safe_float(r.get("spread_pct")),
            "market_regime": r.get("market_regime"),
            "session_bucket": normalize_session(r.get("created_at")),
            "weekend": compute_weekend_flag(r),
        })
    return examples


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generate No-Progress Compact JSON")
    parser.add_argument("--db", default="data/trading_bot.db", help="Path to SQLite DB")
    parser.add_argument("--output", default="data/02_no_progress_compact.json", help="Output JSON path")
    parser.add_argument("--min-count", type=int, default=5, help="Minimum count per segment")
    parser.add_argument("--top-examples", type=int, default=10, help="Number of top examples")
    args = parser.parse_args()

    print(f"[F5_T12 Batch 3] Loading signal_records from {args.db}...")
    rows = load_signal_records(args.db)
    print(f"[F5_T12 Batch 3] Loaded {len(rows)} signal records")

    # Identify no-progress rows and sent count
    # sent_to_telegram column doesn't exist in schema; use rows with exit_reason as proxy
    np_rows = [r for r in rows if bucket_exit_family(r) == "no_progress"]
    sent_count = sum(1 for r in rows if r.get("exit_reason") is not None and r.get("exit_reason") != "")
    print(f"[F5_T12 Batch 3] No-progress rows: {len(np_rows)}, rows_with_exit_reason: {sent_count}")

    # Build segments
    print("[F5_T12 Batch 3] Computing no-progress segments...")
    segments = build_no_progress_segments(rows, np_rows, sent_count, min_count=args.min_count)

    # Top examples
    examples = get_top_examples(np_rows, limit=args.top_examples)

    # Build output
    output = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_signal_records": len(rows),
            "no_progress_count": len(np_rows),
            "sent_to_telegram_count": sent_count,
            "no_progress_rate": round(len(np_rows) / max(1, sent_count), 4),
            "min_count_per_segment": args.min_count,
            "description": "No-Progress Compact Optimizer — F5_T12 Batch 3",
        },
        "segments": segments,
        "top_examples": examples,
    }

    # Write compact JSON
    filepath = write_compact_json(output, args.output)
    print(f"[F5_T12 Batch 3] Written {len(segments)} segments to {filepath}")
    print(f"[F5_T12 Batch 3] Top {len(examples)} examples included")
    print("[F5_T12 Batch 3] Done.")


if __name__ == "__main__":
    main()
