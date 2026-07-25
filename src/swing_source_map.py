from __future__ import annotations

"""
swing_source_map.py
-------------------
Defines the authoritative source mapping for every metric consumed by the
Swing Strategy Review Center.

Each metric declares:
  - source:        "postgresql" | "runtime_logs" | "both" | "derived"
  - table:         PostgreSQL table name (None for non-PG sources)
  - column:        Column within the table (None if not a single column)
  - json_path:     Dotted path into a JSON/JSONB column (e.g. "swing_v1.adapter_parity")
  - authority:     "PRIMARY_OFFICIAL" | "SHADOW" | "SECONDARY_DIAGNOSTIC" | 
                   "TEMPORARY_LOG_SOURCE" | "LEGACY_ONLY" | "DERIVED"
  - confidence:    "HIGH" | "MEDIUM" | "LOW" | "EXPERIMENTAL" | "PARTIAL" | "UNVERIFIED"
  - data_available: bool (whether the data exists in the current env)
  - nullable:      bool (whether the field may legitimately be absent)
  - derivable:     bool (whether the value can be approximated from other fields)
  - retention_warning: bool (whether the source has limited retention)
  - description:   human-readable explanation
"""

from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True)
class MetricSource:
    key: str
    source: str                # "postgresql" | "runtime_logs" | "both" | "derived"
    table: Optional[str]       # PostgreSQL table name
    column: Optional[str]      # Column within the table
    json_path: Optional[str]   # Dotted path into JSON/JSONB column
    authority: str
    confidence: str
    data_available: bool = True
    nullable: bool = False
    derivable: bool = False
    retention_warning: bool = False
    description: str = ""


# ---------------------------------------------------------------------------
# SWING Master Source Map
# ---------------------------------------------------------------------------
SWING_SOURCE_MAP: List[MetricSource] = [
    # ---- Official signal lifecycle (PostgreSQL) ----
    MetricSource(
        key="official_signals",
        source="postgresql",
        table="signal_records",
        column=None,
        json_path=None,
        authority="PRIMARY_OFFICIAL",
        confidence="HIGH",
        description="Core signal table: id, symbol, signal_type, side, status, engine_name, setup_type",
    ),
    MetricSource(
        key="official_lifecycle",
        source="postgresql",
        table="signal_events",
        column=None,
        json_path=None,
        authority="PRIMARY_OFFICIAL",
        confidence="HIGH",
        description="Every lifecycle event: created, activated, tp_hit, sl_hit, cancelled, expired. "
                    "Timestamp column: event_time. Metadata column: metadata_json.",
    ),
    MetricSource(
        key="signal_pnl_r",
        source="postgresql",
        table="signal_records",
        column="net_r",
        json_path=None,
        authority="PRIMARY_OFFICIAL",
        confidence="HIGH",
        description="Official R outcome (net_r, gross_r, pnl_r per signal)",
    ),
    MetricSource(
        key="signal_status",
        source="postgresql",
        table="signal_records",
        column="status",
        json_path=None,
        authority="PRIMARY_OFFICIAL",
        confidence="HIGH",
        description="OPEN, PENDING, CLOSED, etc.",
    ),
    MetricSource(
        key="signal_fingerprint",
        source="postgresql",
        table="signal_records",
        column="metrics_json",
        json_path="swing_v1.config_fingerprint",
        authority="PRIMARY_OFFICIAL",
        confidence="HIGH",
        description="Runtime fingerprint from signal metrics_json",
    ),

    # ---- Adapter Parity (embedded in signal_records, NOT a separate table) ----
    MetricSource(
        key="adapter_parity",
        source="postgresql",
        table="signal_records",
        column="metrics_json",
        json_path="swing_v1.adapter_parity",
        authority="PRIMARY_OFFICIAL",
        confidence="HIGH",
        nullable=True,
        description="Production vs Demo execution comparison embedded in signal_records.metrics_json. "
                    "NOT a separate table.",
    ),

    # ---- same_market_bar (embedded in signal_records) ----
    MetricSource(
        key="same_market_bar",
        source="postgresql",
        table="signal_records",
        column="metrics_json",
        json_path="swing_v1.same_market_bar",
        authority="PRIMARY_OFFICIAL",
        confidence="HIGH",
        nullable=True,
        derivable=True,
        description="Whether the signal activated on the same bar as the entry signal. "
                    "Canonical field may not exist in historical signals (deployed after commit 3442942). "
                    "Derivable from timestamps when field is absent.",
    ),

    # ---- Retroactive bar fill (derived, not persisted) ----
    MetricSource(
        key="retroactive_bar_fill",
        source="derived",
        table=None,
        column=None,
        json_path=None,
        authority="DERIVED",
        confidence="MEDIUM",
        derivable=True,
        description="Detected retroactive bar fills in Demo execution. "
                    "Derived from created_at/pending_persisted_at vs activation bar timestamp.",
    ),

    # ---- Demo compatibility (derived from adapter_parity) ----
    MetricSource(
        key="demo_compatibility",
        source="derived",
        table=None,
        column=None,
        json_path=None,
        authority="DERIVED",
        confidence="MEDIUM",
        description="Demo compatibility classification: REQUESTED, SUBMITTED, FILLED, CANCELLED, "
                    "ACTIVATION_MISMATCH, UNAVAILABLE, UNKNOWN. "
                    "reason=submitted does NOT equal fill.",
    ),

    # ---- Execution detached (separate from same_market_bar) ----
    MetricSource(
        key="execution_detached",
        source="postgresql",
        table="signal_records",
        column="metrics_json",
        json_path="swing_v1.execution_detached",
        authority="PRIMARY_OFFICIAL",
        confidence="HIGH",
        nullable=True,
        description="Whether Demo execution was detached from the signal bar. "
                    "Separate field; does not substitute same_market_bar.",
    ),

    # ---- Experimental lifecycles (PostgreSQL) ----
    MetricSource(
        key="experiments",
        source="postgresql",
        table="swing_experimental_lifecycles",
        column=None,
        json_path=None,
        authority="SHADOW",
        confidence="MEDIUM",
        description="Shadow guard lifecycle tracking for experimental filter sets",
    ),

    # ---- Scanner diagnostics (PostgreSQL, unverified) ----
    MetricSource(
        key="scanner_diagnostics",
        source="postgresql",
        table="scanner_shadow_diagnostics",
        column="mode",
        json_path=None,
        authority="SECONDARY_DIAGNOSTIC",
        confidence="PARTIAL",
        data_available=False,
        description="Scanner shadow diagnostics mode column. "
                    "Availability UNVERIFIED. SWING isolation NOT guaranteed. "
                    "Do NOT feed official results from this table.",
    ),

    # ---- Pre-signal funnel (runtime logs only) ----
    MetricSource(
        key="pre_signal_funnel",
        source="runtime_logs",
        table=None,
        column=None,
        json_path=None,
        authority="TEMPORARY_LOG_SOURCE",
        confidence="LOW",
        retention_warning=True,
        description="Pre-signal funnel: no_breakout reasons, regime_blocked, setup_active, "
                    "pre_signal_transitions, invalidations. Log retention limited.",
    ),

    # ---- Runtime summary (fingerprint, version) ----
    MetricSource(
        key="runtime_summary",
        source="postgresql",
        table="signal_records",
        column="metrics_json",
        json_path="swing_v1",
        authority="PRIMARY_OFFICIAL",
        confidence="HIGH",
        description="Observed runtime fingerprint and version from signal metadata",
    ),

    # ---- SQLite Legacy (explicitly blocked) ----
    MetricSource(
        key="sqlite_legacy",
        source="runtime_logs",
        table=None,
        column=None,
        json_path=None,
        authority="LEGACY_ONLY",
        confidence="LOW",
        data_available=False,
        description="SQLite legacy source. NO FALLBACK PERMITTED. "
                    "All metrics must come from PostgreSQL or derived sources.",
    ),
]


def get_source_map() -> list[MetricSource]:
    """Return the immutable SWING source map."""
    return list(SWING_SOURCE_MAP)


def get_pg_tables() -> list[str]:
    """Return all distinct PostgreSQL tables referenced in the source map."""
    tables = sorted({
        m.table
        for m in SWING_SOURCE_MAP
        if m.source in ("postgresql", "both") and m.table is not None
    })
    return tables


def get_runtime_keys() -> list[str]:
    """Return keys that rely on runtime logs (ephemeral data)."""
    return [m.key for m in SWING_SOURCE_MAP if m.source in ("runtime_logs", "both")]


def get_authority(key: str) -> Optional[str]:
    """Return the authority level for a given metric key."""
    for m in SWING_SOURCE_MAP:
        if m.key == key:
            return m.authority
    return None


def get_confidence(key: str) -> Optional[str]:
    """Return the confidence level for a given metric key."""
    for m in SWING_SOURCE_MAP:
        if m.key == key:
            return m.confidence
    return None


def _build_metadata_dict(metric: MetricSource) -> dict:
    """Return a dict with required metadata fields per the plan spec."""
    result = {
        "key": metric.key,
        "source": metric.source,
        "authority": metric.authority,
        "confidence": metric.confidence,
        "data_available": metric.data_available,
    }
    if metric.table:
        result["table"] = metric.table
    if metric.column:
        result["column"] = metric.column
    if metric.json_path:
        result["json_path"] = metric.json_path
    if metric.nullable:
        result["nullable"] = True
    if metric.derivable:
        result["derivable"] = True
    if metric.retention_warning:
        result["retention_warning"] = True
    return result


def get_all_metadata() -> dict[str, dict]:
    """Return source/authority/confidence for all metrics."""
    return {m.key: _build_metadata_dict(m) for m in SWING_SOURCE_MAP}