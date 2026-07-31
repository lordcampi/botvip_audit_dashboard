from __future__ import annotations

"""
swing_review_pack_builder.py — R3A in-memory analytical pack builder.

Consumes the R2 view model dict (already filtered by fingerprint/scope).
Builds 10 files + manifest in memory, creates a ZIP via BytesIO.
No PostgreSQL connections, no filesystem writes, no Telegram.

Key guarantees:
- Deterministic ZIP (fixed timestamps, stable ordering)
- Chunking for files exceeding 95k chars/bytes
- Recursive secret scanning (fail-closed)
- Explicit temporal contract (data_loaded_at vs generated_at_utc)
- Contract validation before any content is produced
"""

import hashlib
import json
import zipfile
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Any, Optional

from .swing_loaders import _safe_json_load, _nested_get

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAX_CHARS_PER_FILE = 95_000
MAX_BYTES_PER_FILE = 200_000  # generous byte limit for Unicode content
SCHEMA_VERSION = "r3a_swing_review_draft_v1"
STRATEGY = "SWING_TREND_RECLAIM_V1"
MINIMUM_OBSERVATIONAL_SIGNALS = 1
MINIMUM_CONTROL_REVIEW_SIGNALS = 30

# Fixed ZIP metadata for determinism
ZIP_FIXED_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
ZIP_FIXED_CREATE_SYSTEM = 0  # MS-DOS
ZIP_FIXED_EXTERNAL_ATTR = 0o644 << 16
ZIP_FIXED_COMPRESSION = zipfile.ZIP_DEFLATED

OBSERVE = "OBSERVE"
DATA_INSUFFICIENT = "DATA_INSUFFICIENT"
DATA_QUALITY_PARTIAL = "DATA_QUALITY_PARTIAL"
DO_NOT_CHANGE_CONTROL = "DO_NOT_CHANGE_CONTROL"

PROHIBITED = {
    "AUTO_CHANGE_PARAMETERS",
    "PROMOTE_AUTOMATICALLY",
    "ENABLE_REAL_TRADING",
}

VALID_SCOPES = {"latest_only", "all_mixed"}

SENSITIVE_KEYS = {
    "password", "passwd", "token", "secret", "api_key", "authorization",
    "dsn", "database_url", "pg_password", "telegram_bot_token",
    "connection_string", "access_token", "refresh_token",
}
SENSITIVE_STRING_PREFIXES = (
    "postgresql://", "postgres://", "sqlite://", "mysql://",
    "mongodb://", "redis://", "postgresql+",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _iso(val: Any) -> str:
    if val is None:
        return "N/A"
    if isinstance(val, datetime):
        return val.isoformat()
    return str(val)


def _json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True, default=str)


def _compute_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _review_id(gen_at: datetime) -> str:
    if isinstance(gen_at, datetime):
        return f"SWING-{gen_at.strftime('%Y%m%d-%H%M')}"
    return f"SWING-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}"


def _make_zip_info(filename: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(filename, ZIP_FIXED_TIMESTAMP)
    info.create_system = ZIP_FIXED_CREATE_SYSTEM
    info.external_attr = ZIP_FIXED_EXTERNAL_ATTR
    info.compress_type = ZIP_FIXED_COMPRESSION
    return info


# ---------------------------------------------------------------------------
# Sensitive-content scanner (fail-closed)
# ---------------------------------------------------------------------------
def _scan_sensitive_content(obj: Any, path: str = "$") -> None:
    """Recursively scan for sensitive keys or connection strings.

    Raises ValueError (sanitized, no values) on detection.
    """
    if obj is None or isinstance(obj, (int, float, bool)):
        return

    if isinstance(obj, str):
        lower = obj.lower()
        for prefix in SENSITIVE_STRING_PREFIXES:
            if lower.startswith(prefix):
                raise ValueError(
                    f"Sensitive content detected at {path}: connection string pattern"
                )
        # Scan string for embedded key=value patterns that look like credentials
        for sensitive in SENSITIVE_KEYS:
            if sensitive in lower and "=" in lower:
                # Could be an env-style assignment — fail closed
                raise ValueError(
                    f"Potential sensitive content at {path}: matches pattern '{sensitive}'"
                )
        return

    if isinstance(obj, dict):
        for key, value in obj.items():
            key_lower = str(key).lower()
            if key_lower in SENSITIVE_KEYS:
                raise ValueError(
                    f"Sensitive key detected at {path}.{key}"
                )
            _scan_sensitive_content(value, f"{path}.{key}")

    elif isinstance(obj, (list, tuple)):
        for idx, item in enumerate(obj):
            _scan_sensitive_content(item, f"{path}[{idx}]")

    elif isinstance(obj, set):
        for item in obj:
            _scan_sensitive_content(item, f"{path}<set>")

    # Other types (bytes, etc.) — skip silently


# ---------------------------------------------------------------------------
# Contract validation
# ---------------------------------------------------------------------------
def _validate_contract(
    dashboard_data: dict,
    selected_fingerprint: str,
    fingerprint_scope: str,
    window_start_utc: Any,
    window_end_utc: Any,
    generated_at_utc: Any,
) -> None:
    """Validate inputs before building content. Raises ValueError on violation."""
    if not isinstance(dashboard_data, dict):
        raise ValueError("dashboard_data must be a dict")

    if fingerprint_scope not in VALID_SCOPES:
        raise ValueError(
            f"Invalid fingerprint_scope: {fingerprint_scope}. Must be one of: {sorted(VALID_SCOPES)}"
        )

    if fingerprint_scope == "latest_only":
        if not selected_fingerprint or not isinstance(selected_fingerprint, str):
            raise ValueError("selected_fingerprint must be non-empty for latest_only scope")

    # Window validation
    w_start = _parse_ts(window_start_utc)
    w_end = _parse_ts(window_end_utc)
    if w_start is not None and w_end is not None and w_start >= w_end:
        raise ValueError("window_start_utc must be before window_end_utc")

    if generated_at_utc is None:
        raise ValueError("generated_at_utc is required")
    _parse_ts(generated_at_utc)  # raises if not parseable

    # KPI structure check
    kpis = dashboard_data.get("signal_kpis", {})
    if kpis.get("available") is not None:
        # KPIs present — validate counts
        for key in ("total", "lifecycle_closed", "closed_evaluable", "result_derived_count"):
            val = kpis.get(key, 0)
            if val is not None and val < 0:
                raise ValueError(f"Negative count in signal_kpis.{key}: {val}")

    quality = dashboard_data.get("data_quality", {})
    if quality:
        level = quality.get("level", "UNKNOWN")
        if level not in ("GOOD", "PARTIAL", "INSUFFICIENT", "INVALID", "UNKNOWN"):
            raise ValueError(f"Unknown data_quality level: {level}")


def _parse_ts(val: Any):
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        try:
            return datetime.fromisoformat(val)
        except Exception:
            raise ValueError(f"Cannot parse timestamp: {val}")
    raise ValueError(f"Invalid timestamp type: {type(val)}")


# ---------------------------------------------------------------------------
# Common metadata
# ---------------------------------------------------------------------------
def _common_metadata(
    dashboard_data: dict,
    selected_fingerprint: str,
    fingerprint_scope: str,
    generated_at_utc: str,
    review_id: str,
    window_start_utc: str,
    window_end_utc: str,
    window_start_colombia: str,
    window_end_colombia: str,
    authority: str = "PRIMARY_OFFICIAL",
    confidence: str = "HIGH",
    data_available: bool = True,
) -> dict:
    return {
        "_metadata": {
            "source": "postgresql",
            "authority": authority,
            "confidence": confidence,
            "window": {
                "start_utc": window_start_utc,
                "end_utc": window_end_utc,
                "start_colombia": window_start_colombia,
                "end_colombia": window_end_colombia,
            },
            "fingerprint": selected_fingerprint,
            "fingerprint_scope": fingerprint_scope,
            "data_available": data_available,
            "generated_at_utc": generated_at_utc,
            "review_id": review_id,
            "read_only": True,
            "real_trading": False,
            "automatic_changes": False,
        },
    }


# ---------------------------------------------------------------------------
# File builders
# ---------------------------------------------------------------------------
def _build_manifest(
    dashboard_data: dict,
    selected_fingerprint: str,
    fingerprint_scope: str,
    review_id: str,
    generated_at_utc: str,
    window_start_utc: str,
    window_end_utc: str,
    window_start_colombia: str,
    window_end_colombia: str,
    files: dict[str, str],
    warnings: list[str],
    readiness: dict,
    chunking_info: dict,
) -> dict:
    quality = dashboard_data.get("data_quality", {})
    kpis = dashboard_data.get("signal_kpis", {})
    fp_seg = dashboard_data.get("fingerprint_segmentation", {})
    loaded_at = _iso(dashboard_data.get("loaded_at"))

    file_list = [{
        "name": "00_manifest.json",
        "size_chars": None,
        "size_bytes": None,
        "sha256": None,
        "self_referential_size_omitted": True,
        "chunked": False,
        "content_type": "application/json",
    }]

    for name, content in files.items():
        encoded = content.encode("utf-8")
        file_list.append({
            "name": name,
            "size_chars": len(content),
            "size_bytes": len(encoded),
            "sha256": _compute_sha256(content),
            "self_referential_size_omitted": False,
            "chunked": chunking_info.get(name, False),
            "content_type": "text/markdown" if name.endswith(".md") else "application/json",
        })

    return {
        "schema_version": SCHEMA_VERSION,
        "review_id": review_id,
        "generated_at_utc": generated_at_utc,
        "data_loaded_at_utc": loaded_at,
        "window": {
            "start_utc": window_start_utc,
            "end_utc": window_end_utc,
            "start_colombia": window_start_colombia,
            "end_colombia": window_end_colombia,
        },
        "strategy": STRATEGY,
        "selected_fingerprint": selected_fingerprint,
        "fingerprint_scope": fingerprint_scope,
        "scope_validation": {
            "provided_by": "R2_VIEW_MODEL",
            "strategy": STRATEGY,
            "fingerprint_selection_applied": True,
            "builder_revalidated": False,
            "non_swing_excluded_count": dashboard_data.get("excluded_non_swing", 0),
            "fingerprint_excluded_count": dashboard_data.get("excluded_by_fingerprint", 0),
        },
        "counts": {
            "signal_count": dashboard_data.get("total_signals", 0),
            "closed_count": kpis.get("lifecycle_closed", 0),
            "experimental_count": dashboard_data.get("experiments", {}).get("rows", 0),
        },
        "quality": {
            "level": quality.get("level", "UNKNOWN"),
            "reasons": quality.get("reasons", []),
        },
        "readiness": readiness.get("decision", DATA_INSUFFICIENT),
        "warnings": warnings,
        "available_fingerprints": fp_seg.get("fingerprints", {}),
        "files": file_list,
        "max_chars_per_file": MAX_CHARS_PER_FILE,
        "max_bytes_per_file": MAX_BYTES_PER_FILE,
        "chunking_applied": chunking_info.get("_applied", False),
        "chunking_status": "IN_MEMORY_DETERMINISTIC" if chunking_info.get("_applied") else "NO_CHUNKING_NEEDED",
        "chunked_original_files": chunking_info.get("_count", 0),
        "read_only": True,
        "real_trading": False,
        "automatic_changes": False,
        "complete_for_copilot": False,
        "prompt_status": "R3B_PENDING",
    }


def _build_executive_summary(
    dashboard_data: dict,
    selected_fingerprint: str,
    fingerprint_scope: str,
    readiness: dict,
    review_id: str,
    generated_at_utc: str,
) -> str:
    kpis = dashboard_data.get("signal_kpis", {})
    quality = dashboard_data.get("data_quality", {})

    lines = [
        "# SWING Strategy Review — Executive Summary",
        "",
        f"**Review ID:** {review_id}",
        f"**Generated:** {generated_at_utc}",
        f"**Strategy:** {STRATEGY}",
        f"**Fingerprint:** {selected_fingerprint}",
        f"**Scope:** {fingerprint_scope}",
        "",
        "## Sample",
        f"- Total signals: {kpis.get('total', 0)}",
        f"- Closed: {kpis.get('lifecycle_closed', 0)}",
        f"- Closed evaluable: {kpis.get('closed_evaluable', 0)}",
        f"- Pending: {kpis.get('lifecycle_pending', 0)}",
        f"- Activated: {kpis.get('lifecycle_activated', 0)}",
        f"- Cancelled: {kpis.get('lifecycle_cancelled', 0)}",
        f"- Expired: {kpis.get('lifecycle_expired', 0)}",
        f"- Other: {kpis.get('lifecycle_other', 0)}",
        "",
        "## Results",
        f"- WIN: {kpis.get('result_win', 0)}",
        f"- LOSS: {kpis.get('result_loss', 0)}",
        f"- BE: {kpis.get('result_be', 0)}",
        f"- Unknown: {kpis.get('result_unknown', 0)}",
        f"- Canonical: {kpis.get('result_canonical_count', 0)}",
        f"- Derived: {kpis.get('result_derived_count', 0)}",
        "",
        "## Performance",
        f"- PF: {kpis.get('profit_factor', 'N/A')}",
        f"- Net R: {kpis.get('total_r', 'N/A')}",
        f"- Avg R: {kpis.get('avg_r', 'N/A')}",
        f"- Latest signal: {kpis.get('latest_signal_id', 'N/A')}",
        "",
        "## Quality",
        f"- Level: {quality.get('level', 'UNKNOWN')}",
    ]
    for reason in quality.get("reasons", []):
        lines.append(f"  - {reason}")
    lines.extend([
        "",
        "## Readiness",
        f"- Decision: **{readiness.get('decision', DATA_INSUFFICIENT)}**",
        f"- Control change allowed: {readiness.get('control_change_allowed', False)}",
        "",
        "## Warnings",
    ])
    for w in readiness.get("reasons", []):
        lines.append(f"- {w}")
    lines.extend([
        "",
        "## Disclaimer",
        "This review is observational only. No automatic strategy changes.",
        "Trading is OFF. CONTROL is protected.",
    ])
    return "\n".join(lines) + "\n"


def _build_runtime_control(
    dashboard_data, fp, scope, gen_at, rev_id, w_start_utc, w_end_utc, w_start_co, w_end_co
):
    fp_seg = dashboard_data.get("fingerprint_segmentation", {})
    return {
        **_common_metadata(dashboard_data, fp, scope, gen_at, rev_id, w_start_utc, w_end_utc, w_start_co, w_end_co),
        "data": {
            "strategy": STRATEGY,
            "selected_fingerprint": fp,
            "fingerprint_scope": scope,
            "available_fingerprints": fp_seg.get("fingerprints", {}),
            "available_fingerprint_count": fp_seg.get("num_distinct", 0),
            "signals_excluded_by_fingerprint": dashboard_data.get("excluded_by_fingerprint", 0),
            "non_swing_rows_excluded": dashboard_data.get("excluded_non_swing", 0),
            "postgresql_read_only": True,
            "control_protected": True,
            "real_trading": False,
            "automatic_changes": False,
            "source_commit": dashboard_data.get("fingerprint", None),
        },
    }


def _build_data_quality(
    dashboard_data, fp, scope, gen_at, rev_id, w_start_utc, w_end_utc, w_start_co, w_end_co
):
    quality = dashboard_data.get("data_quality", {})
    fp_seg = dashboard_data.get("fingerprint_segmentation", {})
    return {
        **_common_metadata(dashboard_data, fp, scope, gen_at, rev_id, w_start_utc, w_end_utc, w_start_co, w_end_co,
                          authority="PRIMARY_OFFICIAL_WITH_DIAGNOSTICS",
                          confidence=quality.get("level", "UNKNOWN")),
        "data": {
            "level": quality.get("level", "UNKNOWN"),
            "reasons": quality.get("reasons", []),
            "active_fingerprints": fp_seg.get("num_distinct", 0),
            "mixed_config": fp_seg.get("num_distinct", 0) > 1,
            "scanner_status": "STALE / LOW CONFIDENCE / NON-OFFICIAL",
            "scanner_available": dashboard_data.get("scanner", {}).get("available", False),
            "denominators": {"total_signals": dashboard_data.get("total_signals", 0)},
        },
    }


def _build_official_performance(
    dashboard_data, fp, scope, gen_at, rev_id, w_start_utc, w_end_utc, w_start_co, w_end_co
):
    kpis = dashboard_data.get("signal_kpis", {})
    return {
        **_common_metadata(dashboard_data, fp, scope, gen_at, rev_id, w_start_utc, w_end_utc, w_start_co, w_end_co,
                          authority="PRIMARY_OFFICIAL"),
        "data": {
            "total": kpis.get("total", 0),
            "closed": kpis.get("lifecycle_closed", 0),
            "pending": kpis.get("lifecycle_pending", 0),
            "activated": kpis.get("lifecycle_activated", 0),
            "cancelled": kpis.get("lifecycle_cancelled", 0),
            "expired": kpis.get("lifecycle_expired", 0),
            "other": kpis.get("lifecycle_other", 0),
            "result": {"win": kpis.get("result_win", 0), "loss": kpis.get("result_loss", 0),
                       "be": kpis.get("result_be", 0), "unknown": kpis.get("result_unknown", 0)},
            "result_sources": {"canonical_count": kpis.get("result_canonical_count", 0),
                               "physical_column_count": 0,
                               "derived_count": kpis.get("result_derived_count", 0),
                               "insufficient_count": kpis.get("result_unknown", 0)},
            "performance": {"closed_evaluable": kpis.get("closed_evaluable", 0),
                            "profit_factor": kpis.get("profit_factor"),
                            "net_r": kpis.get("total_r"), "avg_r": kpis.get("avg_r")},
            "sample_warning": kpis.get("pf_warning"),
            "latest_signal_id": kpis.get("latest_signal_id"),
        },
    }


def _build_lifecycle_results(
    dashboard_data, fp, scope, gen_at, rev_id, w_start_utc, w_end_utc, w_start_co, w_end_co
):
    kpis = dashboard_data.get("signal_kpis", {})
    return {
        **_common_metadata(dashboard_data, fp, scope, gen_at, rev_id, w_start_utc, w_end_utc, w_start_co, w_end_co,
                          authority="PRIMARY_OFFICIAL"),
        "data": {
            "lifecycle": {"pending": kpis.get("lifecycle_pending", 0),
                          "activated": kpis.get("lifecycle_activated", 0),
                          "closed": kpis.get("lifecycle_closed", 0),
                          "cancelled": kpis.get("lifecycle_cancelled", 0),
                          "expired": kpis.get("lifecycle_expired", 0),
                          "other": kpis.get("lifecycle_other", 0)},
            "result_dimension": {"win": kpis.get("result_win", 0), "loss": kpis.get("result_loss", 0),
                                 "be": kpis.get("result_be", 0), "unknown": kpis.get("result_unknown", 0)},
            "reconciliation": {
                "closed_total": kpis.get("lifecycle_closed", 0),
                "win_plus_loss_plus_be_plus_unknown": (kpis.get("result_win", 0) + kpis.get("result_loss", 0)
                                                       + kpis.get("result_be", 0) + kpis.get("result_unknown", 0)),
                "lifecycle_dimensions_separate_from_results": True,
                "no_double_counting_note": "Lifecycle status and official result are separate dimensions.",
            },
        },
    }


def _build_activation_realism(
    dashboard_data, fp, scope, gen_at, rev_id, w_start_utc, w_end_utc, w_start_co, w_end_co
):
    exec_data = dashboard_data.get("executability", {})
    smb = exec_data.get("same_market_bar", {})
    ed = exec_data.get("execution_detached", {})
    rbf = exec_data.get("retroactive_bar_fill", {})
    return {
        **_common_metadata(dashboard_data, fp, scope, gen_at, rev_id, w_start_utc, w_end_utc, w_start_co, w_end_co,
                          authority="PRIMARY_OFFICIAL_WITH_DERIVED_FIELDS", confidence="MEDIUM"),
        "data": {
            "same_market_bar": {"true": smb.get("true", 0), "false": smb.get("false", 0),
                                "unavailable": smb.get("none", 0), "canonical": smb.get("canonical", 0),
                                "derived": smb.get("derived", 0),
                                "note": "canonical field may be absent; derived from timestamps provides fallback"},
            "execution_detached": {"true": ed.get("true", 0), "false": ed.get("false", 0),
                                   "unavailable": ed.get("none", 0),
                                   "note": "SEPARATE from same_market_bar — do not substitute"},
            "retroactive_bar_fill": {"true": rbf.get("true", 0), "false": rbf.get("false", 0),
                                     "unavailable": rbf.get("none", 0)},
            "denominators": {"total_signals": dashboard_data.get("total_signals", 0)},
            "concepts_separated": True,
            "historical_fallback_ambiguous": True,
        },
    }


def _build_demo_compatibility(
    dashboard_data, fp, scope, gen_at, rev_id, w_start_utc, w_end_utc, w_start_co, w_end_co
):
    exec_data = dashboard_data.get("executability", {})
    demo = exec_data.get("demo_compatibility", {})
    return {
        **_common_metadata(dashboard_data, fp, scope, gen_at, rev_id, w_start_utc, w_end_utc, w_start_co, w_end_co,
                          authority="SECONDARY_EXECUTION_OBSERVABILITY", confidence="MEDIUM"),
        "data": {
            "classification": dict(sorted(demo.items())),
            "submitted_is_not_filled": True,
            "execution_detached_reported_separately": True,
            "denominator": dashboard_data.get("total_signals", 0),
        },
    }


def _build_shadow_comparison(
    dashboard_data, fp, scope, gen_at, rev_id, w_start_utc, w_end_utc, w_start_co, w_end_co
):
    shadow = dashboard_data.get("shadow", {})
    table = shadow.get("table")
    pairs_summary = None
    if table is not None and not table.empty:
        pairs_summary = table.to_dict(orient="records")

    probe = dashboard_data.get("experiments", {})
    probe_table = probe.get("table")
    probe_summary = None
    if probe_table is not None and not probe_table.empty:
        probe_summary = probe_table.to_dict(orient="records")

    return {
        **_common_metadata(dashboard_data, fp, scope, gen_at, rev_id, w_start_utc, w_end_utc, w_start_co, w_end_co,
                          authority="PRIMARY_EXPERIMENTAL", confidence="MEDIUM"),
        "data": {
            "not_official": True,
            # Shadow core pairs — Telegram + Binance Demo
            "shadow_core": {
                "description": "SWING_TREND_RECLAIM SHORT core pairs — sent to Telegram and executed on Binance Demo",
                "experimental_row_count": shadow.get("rows", 0),
                "pairs": shadow.get("pairs", 0),
                "available": shadow.get("available", False),
                "pairs_summary": pairs_summary,
                "comparable_results_available": bool(
                    shadow.get("available", False) and shadow.get("rows", 0) > 0
                ),
            },
            # Universe probe — internal only, no Telegram, no Binance Demo
            "universe_probe": {
                "description": "swing_short_universe_probe_v1 — internal SHORT-only probe across extra pairs. Never Telegram. Never Binance Demo.",
                "experimental_row_count": probe.get("rows", 0),
                "available": probe.get("available", False),
                "probe_summary": probe_summary,
                "comparable_results_available": bool(
                    probe.get("available", False) and probe.get("rows", 0) > 0
                ),
            },
            "warnings": [
                "NOT OFFICIAL — Shadow core pairs are sent to Telegram + Binance Demo only.",
                "NOT OFFICIAL — Universe probe is internal-only: no Telegram, no Binance Demo.",
            ],
        },
    }


def _build_calibration_readiness(
    dashboard_data, fp, scope, gen_at, rev_id, w_start_utc, w_end_utc, w_start_co, w_end_co
):
    return {
        **_common_metadata(dashboard_data, fp, scope, gen_at, rev_id, w_start_utc, w_end_utc, w_start_co, w_end_co,
                          authority="OBSERVATIONAL_REVIEW", confidence="MEDIUM"),
        "data": _assess_readiness(dashboard_data),
    }


# ---------------------------------------------------------------------------
# Readiness evaluator
# ---------------------------------------------------------------------------
def _assess_readiness(dashboard_data: dict) -> dict:
    kpis = dashboard_data.get("signal_kpis", {})
    quality = dashboard_data.get("data_quality", {})
    fp_seg = dashboard_data.get("fingerprint_segmentation", {})

    closed_evaluable = kpis.get("closed_evaluable", 0)
    derived_count = kpis.get("result_derived_count", 0)
    quality_level = quality.get("level", "UNKNOWN")
    num_fps = fp_seg.get("num_distinct", 1)

    rules = []
    reasons = []

    # Rule 1
    min_obs = MINIMUM_OBSERVATIONAL_SIGNALS
    r1 = closed_evaluable >= min_obs
    rules.append({"rule": "minimum_closed_evaluable_for_analysis", "passed": r1, "observed": closed_evaluable,
                  "required": min_obs, "severity": "HIGH" if not r1 else "LOW"})
    if not r1:
        reasons.append("no_closed_evaluable_signals")

    # Rule 2
    r2 = quality_level not in ("INVALID",)
    rules.append({"rule": "data_quality_not_invalid", "passed": r2, "observed": quality_level,
                  "required": "not INVALID", "severity": "CRITICAL" if not r2 else "LOW"})
    if not r2:
        reasons.append("data_quality_invalid")

    # Rule 3
    min_ctrl = MINIMUM_CONTROL_REVIEW_SIGNALS
    r3 = closed_evaluable >= min_ctrl
    rules.append({"rule": "minimum_closed_evaluable_for_control_review", "passed": r3, "observed": closed_evaluable,
                  "required": min_ctrl, "severity": "HIGH" if not r3 else "LOW"})
    if not r3:
        reasons.append(f"sample_below_{min_ctrl}_closed")

    # Rule 4
    r4 = num_fps <= 1
    rules.append({"rule": "single_fingerprint_or_explicitly_mixed", "passed": r4, "observed": num_fps,
                  "required": "≤ 1", "severity": "MEDIUM" if not r4 else "LOW"})
    if not r4:
        reasons.append("mixed_config_fingerprints")

    # Rule 5
    r5 = derived_count <= closed_evaluable * 0.5 if closed_evaluable > 0 else True
    rules.append({"rule": "derived_results_not_dominant", "passed": r5, "observed": derived_count,
                  "max_allowed": int(closed_evaluable * 0.5) if closed_evaluable > 0 else 0,
                  "severity": "HIGH" if not r5 else "LOW"})
    if not r5:
        reasons.append("majority_results_derived_from_gross_r")

    if not r1 or not r2:
        decision = DATA_INSUFFICIENT
    elif quality_level == "PARTIAL":
        decision = DATA_QUALITY_PARTIAL
    elif not r3 or not r4 or not r5:
        decision = DO_NOT_CHANGE_CONTROL
    else:
        decision = OBSERVE

    return {
        "decision": decision,
        "control_change_allowed": False,
        "rules_evaluated": rules,
        "reasons": reasons,
        "prohibited_actions": sorted(PROHIBITED),
    }


# ---------------------------------------------------------------------------
# Chunking engine
# ---------------------------------------------------------------------------
def _chunk_json(name: str, content: str) -> dict[str, str]:
    """Split oversized JSON content into parts + index."""
    data = json.loads(content)
    items = data.get("data", {})

    # Try to split a list inside data
    list_key = None
    if isinstance(items, dict):
        for k, v in items.items():
            if isinstance(v, list) and len(v) > 1:
                list_key = k
                break

    if list_key is None:
        # No splittable list — fail closed
        raise ValueError(
            f"File {name} exceeds limits but has no splittable list in data"
        )

    items_list = items[list_key]
    parts = {}
    current = []
    current_chars = 0
    header = {k: v for k, v in items.items() if k != list_key}
    part_num = 0

    for item in items_list:
        test = list(current) + [item]
        test_data = dict(header)
        test_data[list_key] = test
        test_json = _json_dumps({"data": test_data})
        if len(test_json) > MAX_CHARS_PER_FILE and current:
            part_num += 1
            part_data = dict(header)
            part_data[list_key] = list(current)
            part_name = f"{name.replace('.json', '')}_part{part_num:03d}.json"
            parts[part_name] = _json_dumps({
                "schema_version": SCHEMA_VERSION,
                "original_name": name,
                "part_number": part_num,
                "part_count": 0,  # patched later
                "item_start": (part_num - 1) * len(parts),  # approximate
                "item_end": len(current),
                "_metadata": None,  # patched later
                "data": part_data,
            })
            current = [item]
            current_chars = len(test_json)
        else:
            current.append(item)
            current_chars = len(test_json)

    # Final part
    if current:
        part_num += 1
        part_data = dict(header)
        part_data[list_key] = list(current)
        part_name = f"{name.replace('.json', '')}_part{part_num:03d}.json"
        parts[part_name] = _json_dumps({
            "schema_version": SCHEMA_VERSION,
            "original_name": name,
            "part_number": part_num,
            "part_count": part_num,
            "item_start": (part_num - 1) * max(1, len(items_list) // part_num),
            "item_end": len(current),
            "_metadata": None,
            "data": part_data,
        })

    # Patch part_count in all parts
    for key in list(parts.keys()):
        parsed = json.loads(parts[key])
        parsed["part_count"] = part_num
        parts[key] = _json_dumps(parsed)

    # Build index
    index_name = f"{name.replace('.json', '')}_index.json"
    index_content = _json_dumps({
        "schema_version": f"{SCHEMA_VERSION}_chunked",
        "original_name": name,
        "content_type": "application/json",
        "part_count": part_num,
        "parts": sorted(parts.keys()),
        "reconstruction_order": sorted(parts.keys()),
        "total_items": len(items_list),
        "strategy": "json_list_partition",
    })
    parts[index_name] = index_content

    return parts


def _chunk_markdown(name: str, content: str) -> dict[str, str]:
    """Split oversized Markdown into parts by sections/preserving UTF-8."""
    lines = content.splitlines(keepends=True)
    parts = {}
    current = ""
    part_num = 0

    for line in lines:
        line_bytes = len(line.encode("utf-8"))
        # If single line itself exceeds both limits, split character-by-character
        if line_bytes > MAX_BYTES_PER_FILE and len(line) > MAX_CHARS_PER_FILE:
            # Split safely at Unicode boundaries
            for i in range(0, len(line), MAX_CHARS_PER_FILE // 2):
                chunk = line[i:i + MAX_CHARS_PER_FILE // 2]
                if current:
                    part_num += 1
                    parts[f"{name.replace('.md', '')}_part{part_num:03d}.md"] = current
                    current = ""
                current = chunk
            continue

        test = current + line
        if (len(test) > MAX_CHARS_PER_FILE or len(test.encode("utf-8")) > MAX_BYTES_PER_FILE) and current:
            part_num += 1
            parts[f"{name.replace('.md', '')}_part{part_num:03d}.md"] = current
            current = line
        else:
            current += line

    if current:
        part_num += 1
        parts[f"{name.replace('.md', '')}_part{part_num:03d}.md"] = current

    return parts


def _chunk_file(name: str, content: str) -> dict[str, str]:
    """Route to appropriate chunker based on extension. Returns mapping of new filenames."""
    if name.endswith(".json"):
        return _chunk_json(name, content)
    if name.endswith(".md"):
        return _chunk_markdown(name, content)
    raise ValueError(f"Cannot chunk file type: {name}")


# ---------------------------------------------------------------------------
# Main pack builder
# ---------------------------------------------------------------------------
def build_review_contents(
    dashboard_data: dict,
    selected_fingerprint: str,
    fingerprint_scope: str,
    window_start_utc: Any,
    window_end_utc: Any,
    window_start_colombia: Any,
    window_end_colombia: Any,
    generated_at_utc: Any,
) -> dict[str, Any]:
    """Build the in-memory review pack draft (10 files + manifest, chunked if needed).

    Does NOT connect to PostgreSQL, write files, or send Telegram.
    Does NOT mutate dashboard_data.
    """
    # Canonicalize timestamps
    gen_at_str = _iso(generated_at_utc)
    w_start_utc_str = _iso(window_start_utc)
    w_end_utc_str = _iso(window_end_utc)
    w_start_co_str = _iso(window_start_colombia)
    w_end_co_str = _iso(window_end_colombia)

    # Validate contract
    _validate_contract(dashboard_data, selected_fingerprint, fingerprint_scope,
                       window_start_utc, window_end_utc, generated_at_utc)

    # Security scan on input
    _scan_sensitive_content(dashboard_data)

    review_id = _review_id(generated_at_utc if isinstance(generated_at_utc, datetime) else
                           datetime.fromisoformat(gen_at_str))

    # Build all analytical file contents (as strings)
    raw_files: dict[str, str] = {}

    raw_files["01_executive_summary.md"] = _build_executive_summary(
        dashboard_data, selected_fingerprint, fingerprint_scope,
        _assess_readiness(dashboard_data), review_id, gen_at_str
    )
    raw_files["02_runtime_and_control.json"] = _json_dumps(
        _build_runtime_control(dashboard_data, selected_fingerprint, fingerprint_scope,
                               gen_at_str, review_id, w_start_utc_str, w_end_utc_str, w_start_co_str, w_end_co_str)
    )
    raw_files["03_data_quality.json"] = _json_dumps(
        _build_data_quality(dashboard_data, selected_fingerprint, fingerprint_scope,
                            gen_at_str, review_id, w_start_utc_str, w_end_utc_str, w_start_co_str, w_end_co_str)
    )
    raw_files["04_official_performance.json"] = _json_dumps(
        _build_official_performance(dashboard_data, selected_fingerprint, fingerprint_scope,
                                    gen_at_str, review_id, w_start_utc_str, w_end_utc_str, w_start_co_str, w_end_co_str)
    )
    raw_files["05_lifecycle_and_results.json"] = _json_dumps(
        _build_lifecycle_results(dashboard_data, selected_fingerprint, fingerprint_scope,
                                 gen_at_str, review_id, w_start_utc_str, w_end_utc_str, w_start_co_str, w_end_co_str)
    )
    raw_files["06_activation_realism.json"] = _json_dumps(
        _build_activation_realism(dashboard_data, selected_fingerprint, fingerprint_scope,
                                  gen_at_str, review_id, w_start_utc_str, w_end_utc_str, w_start_co_str, w_end_co_str)
    )
    raw_files["07_demo_compatibility.json"] = _json_dumps(
        _build_demo_compatibility(dashboard_data, selected_fingerprint, fingerprint_scope,
                                  gen_at_str, review_id, w_start_utc_str, w_end_utc_str, w_start_co_str, w_end_co_str)
    )
    raw_files["08_shadow_comparison.json"] = _json_dumps(
        _build_shadow_comparison(dashboard_data, selected_fingerprint, fingerprint_scope,
                                 gen_at_str, review_id, w_start_utc_str, w_end_utc_str, w_start_co_str, w_end_co_str)
    )
    raw_files["09_calibration_readiness.json"] = _json_dumps(
        _build_calibration_readiness(dashboard_data, selected_fingerprint, fingerprint_scope,
                                     gen_at_str, review_id, w_start_utc_str, w_end_utc_str, w_start_co_str, w_end_co_str)
    )

    # Security scan on each built payload
    for name, content in raw_files.items():
        if name.endswith(".json"):
            _scan_sensitive_content(json.loads(content))

    # Apply chunking for any file exceeding limits
    chunking_info = {"_applied": False, "_count": 0}
    final_files: dict[str, str] = {}

    for name, content in raw_files.items():
        encoded = content.encode("utf-8")
        if len(content) > MAX_CHARS_PER_FILE or len(encoded) > MAX_BYTES_PER_FILE:
            chunked = _chunk_file(name, content)
            final_files.update(chunked)
            chunking_info["_applied"] = True
            chunking_info["_count"] += 1
            chunking_info[name] = True
        else:
            final_files[name] = content
            chunking_info[name] = False

    # Build readiness
    readiness = _assess_readiness(dashboard_data)
    warnings = list(readiness.get("reasons", []))

    # Build manifest last
    manifest = _build_manifest(
        dashboard_data, selected_fingerprint, fingerprint_scope, review_id,
        gen_at_str, w_start_utc_str, w_end_utc_str, w_start_co_str, w_end_co_str,
        final_files, warnings, readiness, chunking_info,
    )
    manifest_json = _json_dumps(manifest)

    # Prepend manifest
    ordered_files: dict[str, str] = {"00_manifest.json": manifest_json}
    ordered_files.update(final_files)

    kpis = dashboard_data.get("signal_kpis", {})

    return {
        "review_id": review_id,
        "generated_at_utc": gen_at_str,
        "readiness": readiness,
        "files": ordered_files,
        "warnings": warnings,
        "selected_fingerprint": selected_fingerprint,
        "fingerprint_scope": fingerprint_scope,
        "signal_count": dashboard_data.get("total_signals", 0),
        "closed_count": kpis.get("lifecycle_closed", 0),
        "experimental_count": dashboard_data.get("experiments", {}).get("rows", 0),
    }


def build_swing_review_zip(
    dashboard_data: dict,
    selected_fingerprint: str,
    fingerprint_scope: str,
    window_start_utc: Any,
    window_end_utc: Any,
    window_start_colombia: Any,
    window_end_colombia: Any,
    generated_at_utc: Any,
) -> bytes:
    """Build the complete in-memory ZIP of the analytical review pack.

    Returns the ZIP file as deterministic bytes. Never writes to disk.
    """
    draft = build_review_contents(
        dashboard_data, selected_fingerprint, fingerprint_scope,
        window_start_utc, window_end_utc, window_start_colombia, window_end_colombia, generated_at_utc,
    )

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=ZIP_FIXED_COMPRESSION) as zf:
        for filename in sorted(draft["files"].keys()):
            content = draft["files"][filename]
            content_bytes = content.encode("utf-8")
            if len(content) > MAX_CHARS_PER_FILE or len(content_bytes) > MAX_BYTES_PER_FILE:
                raise ValueError(
                    f"File {filename} exceeds limits after chunking: "
                    f"chars={len(content)}, bytes={len(content_bytes)}"
                )
            zinfo = _make_zip_info(filename)
            zf.writestr(zinfo, content_bytes)

    return buffer.getvalue()