"""
F5_T12 — Batch 5: Filter Contribution Matrix Generator.

Generates 03_filter_contribution_matrix.json from scanner_candidate_shadow_snapshots
and signal_records. Measures which filters avoid losses vs miss winners.

Usage:
    python src/f5_t12_generate_filter_contribution.py [--db data/trading_bot.db] [--output data/03_filter_contribution_matrix.json]
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


def load_candidates(db_path: str) -> List[dict]:
    """Load all scanner_candidate_shadow_snapshots from the database as dicts."""
    conn = connect_readonly(db_path)
    cursor = conn.execute("SELECT * FROM scanner_candidate_shadow_snapshots ORDER BY created_at")
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
# Filter Contribution builder
# ---------------------------------------------------------------------------

def build_filter_contribution(
    candidates: List[dict],
    signal_rows: List[dict],
    min_count: int = 5,
) -> List[dict]:
    """Build filter contribution matrix.

    Uses scanner_candidate_shadow_snapshots.reason as the filter/reason dimension.
    Evaluable candidates are those that became signal_records (matched by symbol + time proximity).
    """
    enriched_signals = [enrich_row(r) for r in signal_rows]
    results: List[dict] = []

    # Group candidates by reason
    reason_groups: Dict[str, List[dict]] = defaultdict(list)
    for c in candidates:
        reason = str(c.get("reason", "unknown")).strip()
        reason_groups[reason].append(c)

    # For each reason, compute blocked vs evaluable
    for reason, reason_candidates in sorted(reason_groups.items(), key=lambda x: -len(x[1])):
        blocked_count = len(reason_candidates)

        # Determine evaluable count: candidates that have a matching signal_record
        evaluable = []
        for c in reason_candidates:
            sym = str(c.get("symbol", ""))
            # Check if there's a signal_record with same symbol
            matching = [s for s in enriched_signals if str(s.get("symbol", "")) == sym]
            if matching:
                evaluable.append(c)

        evaluable_count = len(evaluable)
        evaluable_rate = round(evaluable_count / max(1, blocked_count), 4)

        # For evaluable candidates, compute hypothetical performance
        hypothetical_wins = 0
        hypothetical_losses = 0
        hypothetical_r_values: List[float] = []
        time_stop_count = 0

        for c in evaluable:
            sym = str(c.get("symbol", ""))
            matching_signals = [s for s in enriched_signals if str(s.get("symbol", "")) == sym]
            for s in matching_signals:
                r_val = safe_float(s.get("net_r") or s.get("pnl_r"))
                if r_val is not None and math.isfinite(r_val):
                    hypothetical_r_values.append(r_val)
                    if r_val > 0:
                        hypothetical_wins += 1
                    else:
                        hypothetical_losses += 1
                if str(s.get("exit_reason", "")).lower().find("time") >= 0:
                    time_stop_count += 1

        # Compute filter value metrics
        gross_win_r = sum(r for r in hypothetical_r_values if r > 0)
        gross_loss_abs_r = abs(sum(r for r in hypothetical_r_values if r < 0))
        net_filter_value_r = gross_win_r - gross_loss_abs_r
        pf_if_allowed = gross_win_r / max(0.0001, gross_loss_abs_r) if gross_loss_abs_r > 0 else (999.0 if gross_win_r > 0 else None)

        # Decision logic
        if evaluable_count < min_count:
            decision = "INSUFFICIENT_SAMPLE"
            confidence = "LOW"
        elif evaluable_rate < 0.3:
            decision = "NEEDS_GEOMETRY"
            confidence = "LOW"
        elif net_filter_value_r > 0 and (pf_if_allowed is not None and pf_if_allowed < 0.8):
            decision = "KEEP"
            confidence = "HIGH" if evaluable_count >= 30 else "MEDIUM"
        elif net_filter_value_r < 0 and (pf_if_allowed is not None and pf_if_allowed > 1.2):
            decision = "RELAX"
            confidence = "HIGH" if evaluable_count >= 30 else "MEDIUM"
        elif net_filter_value_r < -5 and evaluable_count >= min_count:
            decision = "REMOVE_CANDIDATE"
            confidence = "MEDIUM"
        else:
            decision = "CONTEXTUAL"
            confidence = "LOW"

        results.append({
            "filter_or_reason": reason,
            "blocked_count": blocked_count,
            "evaluable_count": evaluable_count,
            "evaluable_rate": evaluable_rate,
            "hypothetical_wins": hypothetical_wins,
            "hypothetical_losses": hypothetical_losses,
            "time_stop_count": time_stop_count,
            "avoided_loss_r": round(gross_loss_abs_r, 4),
            "missed_win_r": round(gross_win_r, 4),
            "net_filter_value_r": round(net_filter_value_r, 4),
            "profit_factor_if_allowed": round(pf_if_allowed, 4) if pf_if_allowed is not None else None,
            "decision": decision,
            "confidence": confidence,
        })

    # Sort by net_filter_value_r ascending (worst filters first)
    results.sort(key=lambda x: (x["net_filter_value_r"] or 9999))
    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generate Filter Contribution Matrix JSON")
    parser.add_argument("--db", default="data/trading_bot.db", help="Path to SQLite DB")
    parser.add_argument("--output", default="data/03_filter_contribution_matrix.json", help="Output JSON path")
    parser.add_argument("--min-count", type=int, default=5, help="Minimum evaluable count for decision")
    args = parser.parse_args()

    print(f"[F5_T12 Batch 5] Loading data from {args.db}...")
    signal_rows = load_signal_records(args.db)
    candidates = load_candidates(args.db)
    print(f"[F5_T12 Batch 5] Loaded {len(signal_rows)} signal records, {len(candidates)} candidates")

    # Build filter contribution matrix
    print("[F5_T12 Batch 5] Computing filter contribution...")
    matrix = build_filter_contribution(candidates, signal_rows, min_count=args.min_count)

    # Build output
    output = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_signal_records": len(signal_rows),
            "total_candidates": len(candidates),
            "unique_reasons": len(set(str(c.get("reason", "unknown")) for c in candidates)),
            "min_count_for_decision": args.min_count,
            "description": "Filter Contribution Matrix — F5_T12 Batch 5",
        },
        "rows": matrix,
    }

    # Write compact JSON
    filepath = write_compact_json(output, args.output)
    print(f"[F5_T12 Batch 5] Written {len(matrix)} rows to {filepath}")
    print("[F5_T12 Batch 5] Done.")


if __name__ == "__main__":
    main()
