from __future__ import annotations

"""Tests for swing_review_pack_builder.py — R3A corrected pack builder."""

import json
import zipfile
from datetime import datetime, timedelta, timezone
from io import BytesIO
from unittest.mock import patch, MagicMock

import pytest

from src.swing_review_pack_builder import (
    build_review_contents,
    build_swing_review_zip,
    _assess_readiness,
    _validate_contract,
    _scan_sensitive_content,
    _common_metadata,
    _iso,
    _json_dumps,
    _review_id,
    _compute_sha256,
    _make_zip_info,
    _chunk_file,
    MAX_CHARS_PER_FILE,
    MAX_BYTES_PER_FILE,
    SCHEMA_VERSION,
    STRATEGY,
    ZIP_FIXED_TIMESTAMP,
    OBSERVE, DATA_INSUFFICIENT, DATA_QUALITY_PARTIAL, DO_NOT_CHANGE_CONTROL,
    PROHIBITED, VALID_SCOPES,
)


# ---------------------------------------------------------------------------
# Synthetic fixtures
# ---------------------------------------------------------------------------
def _dashboard_data(overrides=None) -> dict:
    now = datetime(2026, 7, 25, 22, 0, 0)
    base = {
        "window_start_utc": now - timedelta(days=7),
        "window_end_utc": now,
        "window_start_co": now - timedelta(days=7, hours=-5),
        "window_end_co": now + timedelta(hours=-5),
        "loaded_at": datetime(2026, 7, 25, 21, 55, 0),
        "fingerprint": "7fa9d83d70c7076b",
        "total_signals": 8,
        "excluded_non_swing": 1598,
        "excluded_by_fingerprint": 1,
        "data_quality": {"level": "PARTIAL", "reasons": ["Low adapter_parity"]},
        "signal_kpis": {
            "available": True, "total": 8, "lifecycle_closed": 8, "closed_evaluable": 8,
            "lifecycle_pending": 0, "lifecycle_activated": 0, "lifecycle_cancelled": 0,
            "lifecycle_expired": 0, "lifecycle_other": 0,
            "result_win": 4, "result_loss": 4, "result_be": 0, "result_unknown": 0,
            "result_canonical_count": 0, "result_derived_count": 8,
            "total_r": 2.0, "avg_r": 0.25, "profit_factor": 1.5011, "pf_warning": None,
            "latest_signal_id": 2498,
        },
        "fingerprint_segmentation": {
            "available": True, "num_distinct": 2,
            "fingerprints": {"7fa9d83d70c7076b": 8, "b2a85d44e6bc0000": 1},
            "primary": "7fa9d83d70c7076b",
        },
        "executability": {
            "available": True,
            "same_market_bar": {"true": 5, "false": 1, "none": 2, "derived": 6, "canonical": 0},
            "execution_detached": {"true": 3, "false": 0, "none": 5},
            "demo_compatibility": {"ACTIVATION_MISMATCH": 2, "UNAVAILABLE": 6},
            "retroactive_bar_fill": {"true": 0, "false": 0, "none": 8},
        },
        "experiments": {"available": True, "rows": 22},
        "scanner": {"available": False},
    }
    if overrides:
        base.update(overrides)
    return base


def _gen_at():
    return datetime(2026, 7, 25, 22, 0, 0)


def _window_co():
    return _gen_at() - timedelta(days=7)


def _window_utc():
    return _window_co() + timedelta(hours=5)


# ---------------------------------------------------------------------------
# Temporal contract
# ---------------------------------------------------------------------------
class TestTemporalContract:
    def test_loaded_at_distinct_from_generated_at(self):
        data = _dashboard_data({"loaded_at": datetime(2026, 7, 20, 10, 0, 0)})
        gen = datetime(2026, 7, 25, 22, 0, 0)
        draft = build_review_contents(data, "fp12345678", "latest_only",
                                      _window_utc(), _window_utc() + timedelta(days=7),
                                      _window_co(), _window_co() + timedelta(days=7), gen)
        manifest = json.loads(draft["files"]["00_manifest.json"])
        assert manifest["data_loaded_at_utc"] == "2026-07-20T10:00:00"
        assert "2026-07-25T22:00:00" in manifest["generated_at_utc"]

    def test_review_id_uses_generated_at(self):
        gen = datetime(2026, 7, 25, 22, 0, 0)
        draft = build_review_contents(_dashboard_data(), "fp12345678", "latest_only",
                                      _window_utc(), _window_utc() + timedelta(days=7),
                                      _window_co(), _window_co() + timedelta(days=7), gen)
        assert draft["review_id"] == "SWING-20260725-2200"

    def test_different_generated_at_produces_different_review_id(self):
        gen1 = datetime(2026, 7, 25, 22, 0, 0)
        gen2 = datetime(2026, 7, 26, 10, 0, 0)
        data = _dashboard_data()
        d1 = build_review_contents(data, "fp12345678", "latest_only",
                                   _window_utc(), _window_utc() + timedelta(days=7),
                                   _window_co(), _window_co() + timedelta(days=7), gen1)
        d2 = build_review_contents(data, "fp12345678", "latest_only",
                                   _window_utc(), _window_utc() + timedelta(days=7),
                                   _window_co(), _window_co() + timedelta(days=7), gen2)
        assert d1["review_id"] != d2["review_id"]


# ---------------------------------------------------------------------------
# Contract validation
# ---------------------------------------------------------------------------
class TestContractValidation:
    def test_latest_only_requires_fingerprint(self):
        with pytest.raises(ValueError, match="fingerprint"):
            _validate_contract(_dashboard_data(), "", "latest_only",
                               _window_utc(), _window_utc() + timedelta(days=7), _gen_at())

    def test_unknown_scope_rejected(self):
        with pytest.raises(ValueError, match="fingerprint_scope"):
            _validate_contract(_dashboard_data(), "fp", "unknown_scope",
                               _window_utc(), _window_utc() + timedelta(days=7), _gen_at())

    def test_inverted_window_rejected(self):
        with pytest.raises(ValueError, match="window_start_utc"):
            _validate_contract(_dashboard_data(), "fp12345678", "latest_only",
                               _window_utc() + timedelta(days=7), _window_utc(), _gen_at())

    def test_invalid_dashboard_data_rejected(self):
        with pytest.raises(ValueError, match="dict"):
            _validate_contract("not_a_dict", "fp", "latest_only",
                               _window_utc(), _window_utc() + timedelta(days=7), _gen_at())

    def test_negative_counts_rejected(self):
        data = _dashboard_data({"signal_kpis": {"available": True, "total": -1, "closed_evaluable": 5}})
        with pytest.raises(ValueError, match="Negative count"):
            _validate_contract(data, "fp12345678", "latest_only",
                               _window_utc(), _window_utc() + timedelta(days=7), _gen_at())

    def test_missing_generated_at_rejected(self):
        with pytest.raises(ValueError, match="generated_at_utc"):
            _validate_contract(_dashboard_data(), "fp12345678", "latest_only",
                               _window_utc(), _window_utc() + timedelta(days=7), None)


# ---------------------------------------------------------------------------
# Security scanner
# ---------------------------------------------------------------------------
class TestSecurityScanner:
    def test_sensitive_key_at_root(self):
        with pytest.raises(ValueError, match="password"):
            _scan_sensitive_content({"password": "secret123"})

    def test_sensitive_key_nested(self):
        with pytest.raises(ValueError, match="secret"):
            _scan_sensitive_content({"outer": {"inner": {"secret": "abc"}}})

    def test_sensitive_key_mixed_case(self):
        with pytest.raises(ValueError, match="token"):
            _scan_sensitive_content({"access_token": "tok"})

    def test_dsn_string_rejected(self):
        with pytest.raises(ValueError, match="connection string"):
            _scan_sensitive_content("postgresql://user:pass@host/db")

    def test_error_message_sanitized(self):
        with pytest.raises(ValueError) as exc:
            _scan_sensitive_content({"password": "super-secret-123"})
        assert "super-secret-123" not in str(exc.value)

    def test_no_zip_after_security_error(self):
        data = _dashboard_data()
        data["password"] = "leaked"
        with pytest.raises(ValueError):
            build_review_contents(data, "fp12345678", "latest_only",
                                  _window_utc(), _window_utc() + timedelta(days=7),
                                  _window_co(), _window_co() + timedelta(days=7), _gen_at())

    def test_legitimate_token_count_not_rejected(self):
        _scan_sensitive_content({"token_count": 42})
        _scan_sensitive_content({"signal_tokenization": "enabled"})


# ---------------------------------------------------------------------------
# Chunking JSON
# ---------------------------------------------------------------------------
class TestChunkingJson:
    def test_large_json_list_chunked(self):
        items = [{"id": i, "value": "x" * 1000} for i in range(200)]
        content = _json_dumps({"data": {"signals": items}})
        assert len(content) > MAX_CHARS_PER_FILE
        chunked = _chunk_file("04_official_performance.json", content)
        assert len(chunked) > 2
        assert any("_index.json" in k for k in chunked)

    def test_chunked_parts_parseable(self):
        items = [{"id": i, "value": "x" * 1000} for i in range(200)]
        content = _json_dumps({"data": {"signals": items}})
        chunked = _chunk_file("04_official_performance.json", content)
        for name, part_content in chunked.items():
            if name.endswith(".json"):
                parsed = json.loads(part_content)
                assert isinstance(parsed, dict), f"{name} is not parseable"

    def test_no_part_exceeds_chars(self):
        items = [{"id": i, "value": "x" * 500} for i in range(200)]
        content = _json_dumps({"data": {"signals": items}})
        chunked = _chunk_file("04_official_performance.json", content)
        for name, part_content in chunked.items():
            if "_index" not in name:
                assert len(part_content) <= MAX_CHARS_PER_FILE, f"{name} exceeds chars"
                assert len(part_content.encode("utf-8")) <= MAX_BYTES_PER_FILE, f"{name} exceeds bytes"


# ---------------------------------------------------------------------------
# Chunking Markdown
# ---------------------------------------------------------------------------
class TestChunkingMarkdown:
    def test_large_markdown_chunked(self):
        content = "# Report\n\n" + ("Long line " * 20000)
        assert len(content) > MAX_CHARS_PER_FILE
        chunked = _chunk_file("01_executive_summary.md", content)
        assert len(chunked) >= 2

    def test_chunked_markdown_utf8_valid(self):
        content = "# Report\n\n" + ("café niño año " * 20000)
        chunked = _chunk_file("01_executive_summary.md", content)
        for name, part_content in chunked.items():
            part_content.encode("utf-8")


# ---------------------------------------------------------------------------
# ZIP determinism
# ---------------------------------------------------------------------------
class TestZipDeterminism:
    def test_zip_bytes_identical_same_inputs(self):
        data = _dashboard_data()
        gen = _gen_at()
        zip1 = build_swing_review_zip(data, "fp12345678", "latest_only",
                                      _window_utc(), _window_utc() + timedelta(days=7),
                                      _window_co(), _window_co() + timedelta(days=7), gen)
        zip2 = build_swing_review_zip(data, "fp12345678", "latest_only",
                                      _window_utc(), _window_utc() + timedelta(days=7),
                                      _window_co(), _window_co() + timedelta(days=7), gen)
        assert zip1 == zip2

    def test_zip_info_timestamp_fixed(self):
        zinfo = _make_zip_info("test.json")
        assert zinfo.date_time == ZIP_FIXED_TIMESTAMP

    def test_zip_no_absolute_paths(self):
        zip_bytes = build_swing_review_zip(_dashboard_data(), "fp12345678", "latest_only",
                                           _window_utc(), _window_utc() + timedelta(days=7),
                                           _window_co(), _window_co() + timedelta(days=7), _gen_at())
        bio = BytesIO(zip_bytes)
        with zipfile.ZipFile(bio, "r") as zf:
            for name in zf.namelist():
                assert not name.startswith("/")
                assert "\\" not in name

    def test_zip_order_stable(self):
        zip_bytes = build_swing_review_zip(_dashboard_data(), "fp12345678", "latest_only",
                                           _window_utc(), _window_utc() + timedelta(days=7),
                                           _window_co(), _window_co() + timedelta(days=7), _gen_at())
        bio = BytesIO(zip_bytes)
        with zipfile.ZipFile(bio, "r") as zf:
            names = zf.namelist()
            assert names == sorted(names)


# ---------------------------------------------------------------------------
# Char/Bytes/SHA256
# ---------------------------------------------------------------------------
class TestSizeAndHashing:
    def test_unicode_chars_vs_bytes(self):
        text = "café niño año 🎯"
        assert len(text) < len(text.encode("utf-8"))

    def test_sha256_consistent(self):
        assert len(_compute_sha256("hello")) == 64
        assert _compute_sha256("hello") == _compute_sha256("hello")

    def test_manifest_entries_have_sha256(self):
        draft = build_review_contents(_dashboard_data(), "fp12345678", "latest_only",
                                      _window_utc(), _window_utc() + timedelta(days=7),
                                      _window_co(), _window_co() + timedelta(days=7), _gen_at())
        manifest = json.loads(draft["files"]["00_manifest.json"])
        for entry in manifest["files"]:
            if not entry.get("self_referential_size_omitted"):
                assert entry["sha256"] is not None
                assert len(entry["sha256"]) == 64


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------
class TestReadiness:
    def test_all_rules_evaluated(self):
        result = _assess_readiness(_dashboard_data())
        assert len(result["rules_evaluated"]) == 5
        assert result["control_change_allowed"] is False

    def test_zero_closed_insufficient(self):
        data = _dashboard_data({"signal_kpis": {"available": True, "closed_evaluable": 0, "result_derived_count": 0}})
        assert _assess_readiness(data)["decision"] == DATA_INSUFFICIENT

    def test_invalid_quality_insufficient(self):
        data = _dashboard_data({"data_quality": {"level": "INVALID", "reasons": []}})
        assert _assess_readiness(data)["decision"] == DATA_INSUFFICIENT

    def test_partial_with_data(self):
        data = _dashboard_data({"data_quality": {"level": "PARTIAL", "reasons": []}})
        result = _assess_readiness(data)
        assert result["decision"] == DATA_QUALITY_PARTIAL

    def test_good_small_sample_do_not_change(self):
        data = _dashboard_data({
            "signal_kpis": {"available": True, "closed_evaluable": 20, "result_derived_count": 5, "result_canonical_count": 15},
            "data_quality": {"level": "GOOD", "reasons": []},
            "fingerprint_segmentation": {"num_distinct": 1},
        })
        assert _assess_readiness(data)["decision"] == DO_NOT_CHANGE_CONTROL

    def test_good_large_sample_observe(self):
        data = _dashboard_data({
            "signal_kpis": {"available": True, "closed_evaluable": 35, "result_derived_count": 5, "result_canonical_count": 30},
            "data_quality": {"level": "GOOD", "reasons": []},
            "fingerprint_segmentation": {"num_distinct": 1},
        })
        assert _assess_readiness(data)["decision"] == OBSERVE

    def test_prohibited_actions_present(self):
        result = _assess_readiness(_dashboard_data())
        for action in PROHIBITED:
            assert action in result["prohibited_actions"]

    def test_never_returns_prohibited_decisions(self):
        for _ in range(20):
            result = _assess_readiness(_dashboard_data())
            assert result["decision"] not in PROHIBITED


# ---------------------------------------------------------------------------
# Manifest structure
# ---------------------------------------------------------------------------
class TestManifestStructure:
    def test_manifest_has_required_fields(self):
        draft = build_review_contents(_dashboard_data(), "fp12345678", "latest_only",
                                      _window_utc(), _window_utc() + timedelta(days=7),
                                      _window_co(), _window_co() + timedelta(days=7), _gen_at())
        manifest = json.loads(draft["files"]["00_manifest.json"])
        for field in ["schema_version", "review_id", "generated_at_utc", "data_loaded_at_utc",
                       "window", "strategy", "selected_fingerprint", "scope_validation",
                       "counts", "quality", "readiness", "files", "max_chars_per_file",
                       "complete_for_copilot", "prompt_status"]:
            assert field in manifest, f"Missing: {field}"

    def test_manifest_prompt_status_r3b_pending(self):
        draft = build_review_contents(_dashboard_data(), "fp12345678", "latest_only",
                                      _window_utc(), _window_utc() + timedelta(days=7),
                                      _window_co(), _window_co() + timedelta(days=7), _gen_at())
        manifest = json.loads(draft["files"]["00_manifest.json"])
        assert manifest["complete_for_copilot"] is False
        assert manifest["prompt_status"] == "R3B_PENDING"

    def test_manifest_self_referential_size_omitted(self):
        draft = build_review_contents(_dashboard_data(), "fp12345678", "latest_only",
                                      _window_utc(), _window_utc() + timedelta(days=7),
                                      _window_co(), _window_co() + timedelta(days=7), _gen_at())
        manifest = json.loads(draft["files"]["00_manifest.json"])
        mf = [f for f in manifest["files"] if f["name"] == "00_manifest.json"][0]
        assert mf["self_referential_size_omitted"] is True


# ---------------------------------------------------------------------------
# Content integrity
# ---------------------------------------------------------------------------
class TestContentIntegrity:
    def test_files_count_10(self):
        draft = build_review_contents(_dashboard_data(), "fp12345678", "latest_only",
                                      _window_utc(), _window_utc() + timedelta(days=7),
                                      _window_co(), _window_co() + timedelta(days=7), _gen_at())
        assert len(draft["files"]) == 10

    def test_no_prompt_file_in_r3a(self):
        draft = build_review_contents(_dashboard_data(), "fp12345678", "latest_only",
                                      _window_utc(), _window_utc() + timedelta(days=7),
                                      _window_co(), _window_co() + timedelta(days=7), _gen_at())
        for name in draft["files"]:
            assert "prompt_for_copilot" not in name.lower()

    def test_no_credentials_in_files(self):
        draft = build_review_contents(_dashboard_data(), "fp12345678", "latest_only",
                                      _window_utc(), _window_utc() + timedelta(days=7),
                                      _window_co(), _window_co() + timedelta(days=7), _gen_at())
        forbidden = {"PG_HOST", "PG_PASSWORD", "DB_PATH", "dsn", "postgresql://"}
        for name, content in draft["files"].items():
            upper = content.upper()
            for term in forbidden:
                assert term.upper() not in upper, f"{term} in {name}"


# ---------------------------------------------------------------------------
# ZIP integration
# ---------------------------------------------------------------------------
class TestZipIntegration:
    def test_zip_returns_bytes(self):
        zip_bytes = build_swing_review_zip(_dashboard_data(), "fp12345678", "latest_only",
                                           _window_utc(), _window_utc() + timedelta(days=7),
                                           _window_co(), _window_co() + timedelta(days=7), _gen_at())
        assert isinstance(zip_bytes, bytes)

    def test_zip_all_files_under_limits(self):
        zip_bytes = build_swing_review_zip(_dashboard_data(), "fp12345678", "latest_only",
                                           _window_utc(), _window_utc() + timedelta(days=7),
                                           _window_co(), _window_co() + timedelta(days=7), _gen_at())
        bio = BytesIO(zip_bytes)
        with zipfile.ZipFile(bio, "r") as zf:
            for name in zf.namelist():
                content = zf.read(name)
                assert len(content) <= MAX_BYTES_PER_FILE
                assert len(content.decode("utf-8")) <= MAX_CHARS_PER_FILE

    def test_no_filesystem_writes(self):
        with patch("builtins.open", side_effect=RuntimeError("no")):
            zip_bytes = build_swing_review_zip(_dashboard_data(), "fp12345678", "latest_only",
                                               _window_utc(), _window_utc() + timedelta(days=7),
                                               _window_co(), _window_co() + timedelta(days=7), _gen_at())
            assert isinstance(zip_bytes, bytes)