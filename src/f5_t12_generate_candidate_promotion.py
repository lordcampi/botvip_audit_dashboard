#!/usr/bin/env python3
"""
F5_T12 Batch 7 — Candidate Promotion Table
============================================
Converts evaluable candidates into a tool for knowing which OFA pattern
deserves promotion. Uses scanner_candidate_shadow_snapshots table.

Output: data/06_candidate_promotion_table.json
"""

import argparse
import json
import math
import sqlite3
import sys
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# Add parent to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.f5_t12_calibration_core import (
    safe_float,
    parse_json_safe,
    write_compact_json,
    compute_profit_factor_stats,
    classify_decision,
)


# ---------------------------------------------------------------------------
# Candidate class mapping
# ---------------------------------------------------------------------------

def classify_candidate_class(row: dict, metadata: dict) -> str:
    """Classify a candidate into a candidate class based on reason and metadata.
    
    Classes (in priority order):
      - sweep_only: reason contains 'sweep' but no reclaim
      - sweep_plus_reclaim: reason contains 'sweep' and 'reclaim'
      - reclaim_blocked: reason contains 'reclaim_blocked'
      - absorption_confirmed: metadata or reason indicates absorption
      - delta_confirmed: metadata or reason indicates delta
      - stacked_imbalance_confirmed: metadata or reason indicates stacked imbalance
      - vwap_aligned: metadata or reason indicates VWAP alignment
      - volume_profile_level: metadata or reason indicates volume profile
      - unknown_or_no_geometry: fallback
    """
    reason = (row.get("reason") or "").lower()
    setup_type = (metadata.get("setup_type") or "").lower()
    hypothetical_result = (metadata.get("hypothetical_result") or "").lower()
    
    # Check for geometry-based classes first
    if hypothetical_result == "skipped_no_geometry":
        return "unknown_or_no_geometry"
    
    # Check reason patterns
    if "sweep" in reason and "reclaim" in reason:
        return "sweep_plus_reclaim"
    if "sweep" in reason:
        return "sweep_only"
    if "reclaim_blocked" in reason:
        return "reclaim_blocked"
    if "absorption" in reason:
        return "absorption_confirmed"
    if "delta" in reason:
        return "delta_confirmed"
    if "stacked_imbalance" in reason or "imbalance" in reason:
        return "stacked_imbalance_confirmed"
    if "vwap" in reason:
        return "vwap_aligned"
    if "volume_profile" in reason or "profile" in reason:
        return "volume_profile_level"
    
    # Check setup type
    if "sweep" in setup_type and "reclaim" in setup_type:
        return "sweep_plus_reclaim"
    if "sweep" in setup_type:
        return "sweep_only"
    
    return "unknown_or_no_geometry"


def get_hypothetical_r(metadata: dict) -> Optional[float]:
    """Extract hypothetical R from metadata."""
    net_rr = metadata.get("net_rr")
    if net_rr is not None:
        return safe_float(net_rr)
    # Try alternative field names
    for key in ["net_r", "pnl_r", "gross_r"]:
        val = metadata.get(key)
        if val is not None:
            return safe_float(val)
    return None


def get_hypothetical_mfe(metadata: dict) -> Optional[float]:
    """Extract hypothetical MFE from metadata."""
    mfe = metadata.get("mfe")
    if mfe is not None:
        return safe_float(mfe)
    return None


def get_hypothetical_mae(metadata: dict) -> Optional[float]:
    """Extract hypothetical MAE from metadata."""
    mae = metadata.get("mae")
    if mae is not None:
        return safe_float(mae)
    return None


# ---------------------------------------------------------------------------
# Load candidates
# ---------------------------------------------------------------------------

def load_candidates(db_path: str) -> List[dict]:
    """Load all scanner_candidate_shadow_snapshots rows."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("""
        SELECT id, created_at, cycle_id, mode, symbol, reason, adx, rvol,
               atr_extension, score, metadata_json
        FROM scanner_candidate_shadow_snapshots
        ORDER BY created_at DESC
    """)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


# ---------------------------------------------------------------------------
# Build candidate promotion segments
# ---------------------------------------------------------------------------

def build_candidate_segments(
    candidates: List[dict],
    min_count: int = 5,
) -> List[dict]:
    """Build candidate promotion segments grouped by candidate class."""
    
    # Parse metadata and classify
    enriched: List[dict] = []
    for c in candidates:
        meta = parse_json_safe(c.get("metadata_json"))
        c_class = classify_candidate_class(c, meta)
        hypo_result = (meta.get("hypothetical_result") or "").lower()
        has_geometry = hypo_result != "skipped_no_geometry"
        
        enriched.append({
            "candidate_class": c_class,
            "has_geometry": has_geometry,
            "hypothetical_result": hypo_result,
            "hypothetical_r": get_hypothetical_r(meta),
            "hypothetical_mfe": get_hypothetical_mfe(meta),
            "hypothetical_mae": get_hypothetical_mae(meta),
            "setup_type": meta.get("setup_type"),
            "reason": c.get("reason"),
            "symbol": c.get("symbol"),
            "score": safe_float(c.get("score")),
            "adx": safe_float(c.get("adx")),
            "rvol": safe_float(c.get("rvol")),
        })
    
    # Group by candidate class
    classes: Dict[str, List[dict]] = {}
    for e in enriched:
        cls = e["candidate_class"]
        if cls not in classes:
            classes[cls] = []
        classes[cls].append(e)
    
    results: List[dict] = []
    
    for c_class, items in sorted(classes.items()):
        total_count = len(items)
        evaluable = [i for i in items if i["has_geometry"]]
        evaluable_count = len(evaluable)
        
        # R values from evaluable candidates
        r_values = [i["hypothetical_r"] for i in evaluable if i["hypothetical_r"] is not None and math.isfinite(i["hypothetical_r"])]
        mfe_values = [i["hypothetical_mfe"] for i in evaluable if i["hypothetical_mfe"] is not None and math.isfinite(i["hypothetical_mfe"])]
        mae_values = [i["hypothetical_mae"] for i in evaluable if i["hypothetical_mae"] is not None and math.isfinite(i["hypothetical_mae"])]
        
        # Hypothetical wins/losses
        hypothetical_wins = sum(1 for v in r_values if v > 0)
        hypothetical_losses = sum(1 for v in r_values if v < 0)
        
        # PF stats - use net_rr as the r column name
        pf_stats = compute_profit_factor_stats([{"net_r": v} for v in r_values])
        
        # Avg MFE/MAE
        avg_mfe_r = round(sum(mfe_values) / len(mfe_values), 4) if mfe_values else None
        avg_mae_r = round(sum(mae_values) / len(mae_values), 4) if mae_values else None
        
        # Net R
        net_r = pf_stats.get("net_r")
        
        # Promotion decision
        pf = pf_stats.get("profit_factor")
        if evaluable_count < min_count:
            promotion_decision = "INSUFFICIENT_SAMPLE"
            required_confirmations = max(min_count - evaluable_count, 0)
        elif evaluable_count == 0:
            promotion_decision = "NO_EVALUABLE_DATA"
            required_confirmations = 0
        elif pf is None and pf_stats.get("gross_loss_abs_r", 0) == 0 and pf_stats.get("gross_win_r", 0) > 0:
            # All wins, no losses — infinite PF
            promotion_decision = "PROMOTE"
            required_confirmations = 0
        elif pf is not None and pf >= 1.25 and net_r is not None and net_r > 0:
            promotion_decision = "PROMOTE"
            required_confirmations = 0
        elif pf is not None and pf >= 0.8 and net_r is not None and net_r > 0:
            promotion_decision = "WATCH"
            required_confirmations = 0
        elif pf is not None and pf < 0.8 and net_r is not None and net_r < 0:
            promotion_decision = "RESTRICT"
            required_confirmations = 0
        else:
            promotion_decision = "REVIEW"
            required_confirmations = 0
        
        # Top symbols
        symbol_counts: Dict[str, int] = {}
        for i in items:
            sym = i.get("symbol") or "unknown"
            symbol_counts[sym] = symbol_counts.get(sym, 0) + 1
        top_symbols = sorted(symbol_counts.items(), key=lambda x: -x[1])[:5]
        
        results.append({
            "candidate_class": c_class,
            "count": total_count,
            "evaluable_count": evaluable_count,
            "hypothetical_wins": hypothetical_wins,
            "hypothetical_losses": hypothetical_losses,
            "hypothetical_pf": pf_stats.get("profit_factor"),
            "net_r": net_r,
            "avg_mfe_r": avg_mfe_r,
            "avg_mae_r": avg_mae_r,
            "top_symbols": [s[0] for s in top_symbols],
            "promotion_decision": promotion_decision,
            "required_confirmations": required_confirmations,
        })
    
    # Sort by evaluable_count descending
    results.sort(key=lambda x: -x["evaluable_count"])
    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generate Candidate Promotion Table JSON")
    parser.add_argument("--db", default="data/trading_bot.db", help="Path to SQLite DB")
    parser.add_argument("--output", default="data/06_candidate_promotion_table.json", help="Output JSON path")
    parser.add_argument("--min-count", type=int, default=5, help="Minimum evaluable count per segment")
    args = parser.parse_args()
    
    print(f"[F5_T12 Batch 7] Loading candidates from {args.db}...")
    candidates = load_candidates(args.db)
    print(f"[F5_T12 Batch 7] Loaded {len(candidates)} candidates")
    
    # Count evaluable
    evaluable_count = 0
    for c in candidates:
        meta = parse_json_safe(c.get("metadata_json"))
        hr = (meta.get("hypothetical_result") or "").lower()
        if hr != "skipped_no_geometry":
            evaluable_count += 1
    print(f"[F5_T12 Batch 7] Evaluable candidates: {evaluable_count}")
    
    # Build segments
    print("[F5_T12 Batch 7] Computing candidate promotion segments...")
    segments = build_candidate_segments(candidates, min_count=args.min_count)
    
    # Build output
    output = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_candidates": len(candidates),
            "evaluable_candidates": evaluable_count,
            "min_count_per_segment": args.min_count,
            "description": "Candidate Promotion Table — F5_T12 Batch 7",
        },
        "rows": segments,
    }
    
    # Write compact JSON
    filepath = write_compact_json(output, args.output)
    print(f"[F5_T12 Batch 7] Written {len(segments)} segments to {filepath}")
    print("[F5_T12 Batch 7] Done.")


if __name__ == "__main__":
    main()
