from __future__ import annotations

from src.f5_t04bcd_diagnostics import (
    build_entity_scope_reconciliation,
    build_f5_t04bcd_diagnostics,
    build_no_progress_root_cause_diagnostics,
    build_zone_mapping_quality,
    resolve_operational_zone,
)


def test_weekend_priority_from_timestamp() -> None:
    row = {"created_at": "2026-06-27 10:00:00", "killzone": True, "operating_mode": "Killzone institucional activa"}
    resolved = resolve_operational_zone(row)
    assert resolved["zone"] == "weekend"
    assert resolved["method"] == "timestamp_weekend_priority"


def test_zone_mapping_uses_operating_mode_when_flags_missing() -> None:
    row = {"created_at": "2026-06-24 10:00:00", "operating_mode": "Killzone institucional activa"}
    resolved = resolve_operational_zone(row)
    assert resolved["zone"] == "killzone"
    assert resolved["method"] == "operating_mode_label"


def test_no_progress_missing_observability_is_low_confidence() -> None:
    facts = [{
        "record_type": "signal",
        "signal_id": "101",
        "symbol": "BTC/USDT:USDT",
        "side": "LONG",
        "setup_type": "OFA_SWEEP_RECLAIM",
        "created_at": "2026-06-24 09:00:00",
        "opened_at": "2026-06-24 09:01:00",
        "closed_at": "2026-06-24 09:20:00",
        "exit_reason": "no_progress",
        "no_progress_exit": True,
        "sent_to_telegram": True,
        "operating_mode": "Killzone institucional activa",
        "data_gap_events": 0,
    }]
    result = build_no_progress_root_cause_diagnostics(facts=facts, events=[])
    row = result["rows"][0]
    assert row["classifier"] in {"missing_price_path", "unknown_due_to_missing_observability"}
    assert row["confidence"] == "low"


def test_entity_scope_candidate_gets_derived_key_not_official_signal_id() -> None:
    facts = [
        {"record_type": "signal", "signal_id": "11", "source_table": "signal_records", "symbol": "ETH", "created_at": "2026-06-24 09:00:00", "sent_to_telegram": True},
        {"record_type": "candidate", "candidate_id": 22, "source_table": "scanner_candidate_shadow_snapshots", "symbol": "ETH", "setup_type": "OFA", "created_at": "2026-06-24 09:05:00"},
    ]
    result = build_entity_scope_reconciliation(facts=facts, events=[], signals=[{"id": 11}], candidates=[{"id": 22}])
    assert result["entities"]["official_signals"]["countable_as_trades"] is True
    assert result["entities"]["candidate_snapshots"]["countable_as_trades"] is False
    sample = result["unknown_or_derived_identity_rows"]["sample"][0]
    assert sample["derived_row_key"]
    assert sample["official_signal_id"] is None


def test_batch2_builder_has_all_sections() -> None:
    output = build_f5_t04bcd_diagnostics(facts=[], events=[], signals=[], candidates=[])
    assert set(output) == {
        "no_progress_root_cause_diagnostics",
        "zone_diagnostics_v2",
        "zone_mapping_quality",
        "entity_scope_reconciliation",
    }
    quality = build_zone_mapping_quality(facts=[])
    assert quality["total_rows"] == 0
