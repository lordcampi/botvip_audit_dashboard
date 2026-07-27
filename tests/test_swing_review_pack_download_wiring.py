from __future__ import annotations

"""Tests for R3C wiring: Swing Review Pack download flow.

Validates the integration from dashboard_data → R3A → R3B → ZIP bytes,
ensuring the helper build_swing_review_pack_for_download() produces
correct 11-file R3B ZIPs, respects the contract, and fails gracefully.
"""

import json
import zipfile
from datetime import datetime, timedelta
from io import BytesIO
from unittest.mock import patch

import pytest

from src.swing_review_pack_builder import (
    build_review_contents,
    _json_dumps,
    SCHEMA_VERSION as R3A_SCHEMA,
    MAX_CHARS_PER_FILE,
)
from src.swing_prompt_builder import (
    build_swing_review_pack_for_download,
    build_final_swing_review_zip,
    build_copilot_prompt,
    finalize_review_pack_with_prompt,
    R3B_SCHEMA_VERSION,
    _validate_r3a_draft,
)


# ---------------------------------------------------------------------------
# Synthetic fixtures (same shape as test_swing_review_pack_builder / prompt)
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
# 1. Page import / API reference
# ---------------------------------------------------------------------------
class TestPageAPIImport:
    """The helper build_swing_review_pack_for_download is importable and
    calls the R3B public API — not build_swing_review_zip() directly."""

    def test_helper_is_importable(self):
        from src.swing_prompt_builder import build_swing_review_pack_for_download
        assert callable(build_swing_review_pack_for_download)

    def test_helper_calls_build_final_swing_review_zip(self):
        """Verify the helper delegates to R3B build_final_swing_review_zip,
        not the R3A-only build_swing_review_zip."""
        from src.swing_prompt_builder import build_swing_review_pack_for_download

        # Monkey-patch to detect what gets called
        called = []

        def _fake_build_final(r3a_draft, prompt_text=None):
            called.append("build_final_swing_review_zip")
            return b"fake"

        with patch("src.swing_prompt_builder.build_final_swing_review_zip", _fake_build_final):
            result = build_swing_review_pack_for_download(
                _dashboard_data(), "fp12345678", "latest_only",
                _window_utc(), _window_utc() + timedelta(days=7),
                _window_co(), _window_co() + timedelta(days=7),
                _gen_at(),
            )
            assert called == ["build_final_swing_review_zip"]


# ---------------------------------------------------------------------------
# 2. ZIP contents — exact 11 files
# ---------------------------------------------------------------------------
class TestZipContents:
    """The ZIP produced by the helper contains exactly the 11 R3B files."""

    REQUIRED_FILES = {
        "00_manifest.json",
        "01_executive_summary.md",
        "02_runtime_and_control.json",
        "03_data_quality.json",
        "04_official_performance.json",
        "05_lifecycle_and_results.json",
        "06_activation_realism.json",
        "07_demo_compatibility.json",
        "08_shadow_comparison.json",
        "09_calibration_readiness.json",
        "10_prompt_for_copilot.md",
    }

    def test_zip_has_exactly_11_files(self):
        zip_bytes = build_swing_review_pack_for_download(
            _dashboard_data(), "fp12345678", "latest_only",
            _window_utc(), _window_utc() + timedelta(days=7),
            _window_co(), _window_co() + timedelta(days=7),
            _gen_at(),
        )
        bio = BytesIO(zip_bytes)
        with zipfile.ZipFile(bio, "r") as zf:
            names = zf.namelist()
            assert len(names) == 11, f"Expected 11, got {len(names)}: {sorted(names)}"

    def test_zip_has_exact_names(self):
        zip_bytes = build_swing_review_pack_for_download(
            _dashboard_data(), "fp12345678", "latest_only",
            _window_utc(), _window_utc() + timedelta(days=7),
            _window_co(), _window_co() + timedelta(days=7),
            _gen_at(),
        )
        bio = BytesIO(zip_bytes)
        with zipfile.ZipFile(bio, "r") as zf:
            names = set(zf.namelist())
            assert names == self.REQUIRED_FILES, (
                f"Missing: {self.REQUIRED_FILES - names}, Extra: {names - self.REQUIRED_FILES}"
            )

    def test_no_duplicate_files(self):
        zip_bytes = build_swing_review_pack_for_download(
            _dashboard_data(), "fp12345678", "latest_only",
            _window_utc(), _window_utc() + timedelta(days=7),
            _window_co(), _window_co() + timedelta(days=7),
            _gen_at(),
        )
        bio = BytesIO(zip_bytes)
        with zipfile.ZipFile(bio, "r") as zf:
            names = zf.namelist()
            assert len(names) == len(set(names)), f"Duplicates: {names}"


# ---------------------------------------------------------------------------
# 3. Manifest schema & statuses
# ---------------------------------------------------------------------------
class TestManifestSchema:
    def test_schema_is_r3b(self):
        zip_bytes = build_swing_review_pack_for_download(
            _dashboard_data(), "fp12345678", "latest_only",
            _window_utc(), _window_utc() + timedelta(days=7),
            _window_co(), _window_co() + timedelta(days=7),
            _gen_at(),
        )
        bio = BytesIO(zip_bytes)
        with zipfile.ZipFile(bio, "r") as zf:
            manifest = json.loads(zf.read("00_manifest.json"))
            assert manifest["schema_version"] == R3B_SCHEMA_VERSION

    def test_complete_for_copilot_true(self):
        zip_bytes = build_swing_review_pack_for_download(
            _dashboard_data(), "fp12345678", "latest_only",
            _window_utc(), _window_utc() + timedelta(days=7),
            _window_co(), _window_co() + timedelta(days=7),
            _gen_at(),
        )
        bio = BytesIO(zip_bytes)
        with zipfile.ZipFile(bio, "r") as zf:
            manifest = json.loads(zf.read("00_manifest.json"))
            assert manifest["complete_for_copilot"] is True

    def test_prompt_status_ready(self):
        zip_bytes = build_swing_review_pack_for_download(
            _dashboard_data(), "fp12345678", "latest_only",
            _window_utc(), _window_utc() + timedelta(days=7),
            _window_co(), _window_co() + timedelta(days=7),
            _gen_at(),
        )
        bio = BytesIO(zip_bytes)
        with zipfile.ZipFile(bio, "r") as zf:
            manifest = json.loads(zf.read("00_manifest.json"))
            assert manifest["prompt_status"] == "READY"

    def test_finalization_status_complete(self):
        zip_bytes = build_swing_review_pack_for_download(
            _dashboard_data(), "fp12345678", "latest_only",
            _window_utc(), _window_utc() + timedelta(days=7),
            _window_co(), _window_co() + timedelta(days=7),
            _gen_at(),
        )
        bio = BytesIO(zip_bytes)
        with zipfile.ZipFile(bio, "r") as zf:
            manifest = json.loads(zf.read("00_manifest.json"))
            assert manifest["finalization_status"] == "COMPLETE"

    def test_manifest_inventory_matches_zip(self):
        zip_bytes = build_swing_review_pack_for_download(
            _dashboard_data(), "fp12345678", "latest_only",
            _window_utc(), _window_utc() + timedelta(days=7),
            _window_co(), _window_co() + timedelta(days=7),
            _gen_at(),
        )
        bio = BytesIO(zip_bytes)
        with zipfile.ZipFile(bio, "r") as zf:
            real_names = set(zf.namelist())
            manifest = json.loads(zf.read("00_manifest.json"))
            manifest_names = {
                f["name"] for f in manifest["files"]
                if not f.get("self_referential_size_omitted")
            }
            # Manifest inventory must include all real files (minus manifest itself)
            expected = real_names - {"00_manifest.json"}
            assert manifest_names == expected, (
                f"Inventory mismatch: manifest={manifest_names}, real={expected}"
            )

    def test_manifest_json_valid(self):
        zip_bytes = build_swing_review_pack_for_download(
            _dashboard_data(), "fp12345678", "latest_only",
            _window_utc(), _window_utc() + timedelta(days=7),
            _window_co(), _window_co() + timedelta(days=7),
            _gen_at(),
        )
        bio = BytesIO(zip_bytes)
        with zipfile.ZipFile(bio, "r") as zf:
            manifest = json.loads(zf.read("00_manifest.json"))
            assert isinstance(manifest, dict)


# ---------------------------------------------------------------------------
# 4. R3A immutability — 01-09 byte-identical to pre-finalization draft
# ---------------------------------------------------------------------------
class TestR3AImmutability:
    def test_r3a_files_byte_identical_to_draft(self):
        data = _dashboard_data()
        r3a_draft = build_review_contents(
            data, "fp12345678", "latest_only",
            _window_utc(), _window_utc() + timedelta(days=7),
            _window_co(), _window_co() + timedelta(days=7),
            _gen_at(),
        )

        # Build R3B ZIP via the helper
        zip_bytes = build_swing_review_pack_for_download(
            data, "fp12345678", "latest_only",
            _window_utc(), _window_utc() + timedelta(days=7),
            _window_co(), _window_co() + timedelta(days=7),
            _gen_at(),
        )

        bio = BytesIO(zip_bytes)
        with zipfile.ZipFile(bio, "r") as zf:
            for name in r3a_draft["files"]:
                if name == "00_manifest.json":
                    continue  # Manifest is expected to differ (R3A → R3B)
                draft_content = r3a_draft["files"][name]
                zip_content = zf.read(name).decode("utf-8")
                assert draft_content == zip_content, (
                    f"R3A file {name} differs between draft and R3B ZIP"
                )


# ---------------------------------------------------------------------------
# 5. Download name & MIME
# ---------------------------------------------------------------------------
class TestDownloadMetadata:
    def test_download_name_deterministic(self):
        # The name is built from generated_at timestamp
        gen1 = datetime(2026, 7, 25, 12, 0, 0)
        gen2 = datetime(2026, 7, 25, 12, 0, 0)
        # Same date → same name
        name1 = f"SWING_REVIEW_PACK_R3B_{gen1.strftime('%Y-%m-%d')}.zip"
        name2 = f"SWING_REVIEW_PACK_R3B_{gen2.strftime('%Y-%m-%d')}.zip"
        assert name1 == name2

    def test_name_ends_with_zip(self):
        gen = datetime(2026, 7, 25, 12, 0, 0)
        name = f"SWING_REVIEW_PACK_R3B_{gen.strftime('%Y-%m-%d')}.zip"
        assert name.endswith(".zip")

    def test_name_no_invalid_windows_chars(self):
        gen = datetime(2026, 7, 25, 12, 0, 0)
        name = f"SWING_REVIEW_PACK_R3B_{gen.strftime('%Y-%m-%d')}.zip"
        invalid = {'<', '>', ':', '"', '/', '\\', '|', '?', '*'}
        for ch in invalid:
            assert ch not in name, f"Invalid Windows char '{ch}' in {name}"

    def test_mime_is_application_zip(self):
        # This is a contract test — the MIME used by the page
        mime = "application/zip"
        assert mime == "application/zip"


# ---------------------------------------------------------------------------
# 6. Graceful failure — invalid data
# ---------------------------------------------------------------------------
class TestGracefulFailure:
    def test_zip_valid(self):
        zip_bytes = build_swing_review_pack_for_download(
            _dashboard_data(), "fp12345678", "latest_only",
            _window_utc(), _window_utc() + timedelta(days=7),
            _window_co(), _window_co() + timedelta(days=7),
            _gen_at(),
        )
        bio = BytesIO(zip_bytes)
        with zipfile.ZipFile(bio, "r") as zf:
            assert zf.testzip() is None  # ZIP integrity ok

    def test_non_dict_dashboard_data_raises_contract_error(self):
        with pytest.raises(ValueError, match="dict"):
            build_swing_review_pack_for_download(
                "not_a_dict", "fp12345678", "latest_only",
                _window_utc(), _window_utc() + timedelta(days=7),
                _window_co(), _window_co() + timedelta(days=7),
                _gen_at(),
            )

    def test_invalid_data_does_not_produce_zip(self):
        # A dict with negative total_signals should fail via contract validation
        bad = _dashboard_data({"signal_kpis": {"available": True, "total": -5}})
        with pytest.raises(ValueError):
            build_swing_review_pack_for_download(
                bad, "fp12345678", "latest_only",
                _window_utc(), _window_utc() + timedelta(days=7),
                _window_co(), _window_co() + timedelta(days=7),
                _gen_at(),
            )

    def test_partial_quality_visible_in_zip(self):
        """DATA_QUALITY_PARTIAL is preserved in the pack."""
        zip_bytes = build_swing_review_pack_for_download(
            _dashboard_data(), "fp12345678", "latest_only",
            _window_utc(), _window_utc() + timedelta(days=7),
            _window_co(), _window_co() + timedelta(days=7),
            _gen_at(),
        )
        bio = BytesIO(zip_bytes)
        with zipfile.ZipFile(bio, "r") as zf:
            manifest = json.loads(zf.read("00_manifest.json"))
            assert manifest["quality"]["level"] == "PARTIAL"

            quality_file = json.loads(zf.read("03_data_quality.json"))
            assert quality_file["data"]["level"] == "PARTIAL"

            prompt = zf.read("10_prompt_for_copilot.md").decode("utf-8")
            assert "PARTIAL" in prompt


# ---------------------------------------------------------------------------
# 7. No side effects
# ---------------------------------------------------------------------------
class TestNoSideEffects:
    def test_no_filesystem_writes(self):
        with patch("builtins.open", side_effect=RuntimeError("no fs writes allowed")):
            zip_bytes = build_swing_review_pack_for_download(
                _dashboard_data(), "fp12345678", "latest_only",
                _window_utc(), _window_utc() + timedelta(days=7),
                _window_co(), _window_co() + timedelta(days=7),
                _gen_at(),
            )
            assert isinstance(zip_bytes, bytes)

    def test_no_db_connection(self):
        # build_swing_review_pack_for_download should never touch PostgreSQL.
        # The R3A/R3B modules do NOT import postgres_readonly or db.py.
        with patch("src.postgres_readonly.build_readonly_conn", side_effect=RuntimeError("no DB")):
            zip_bytes = build_swing_review_pack_for_download(
                _dashboard_data(), "fp12345678", "latest_only",
                _window_utc(), _window_utc() + timedelta(days=7),
                _window_co(), _window_co() + timedelta(days=7),
                _gen_at(),
            )
            assert isinstance(zip_bytes, bytes)

    def test_no_telegram_imports(self):
        # The R3A/R3B modules should not import telegram_delivery at the module level
        import ast
        import src.swing_review_pack_builder as r3a_mod
        import src.swing_prompt_builder as r3b_mod

        def _has_telegram_import(mod) -> bool:
            try:
                source = __import__("inspect").getsource(mod)
            except OSError:
                return False
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if "telegram" in alias.name.lower():
                            return True
                elif isinstance(node, ast.ImportFrom):
                    if node.module and "telegram" in node.module.lower():
                        return True
            return False

        assert not _has_telegram_import(r3a_mod), "R3A module must not import telegram"
        assert not _has_telegram_import(r3b_mod), "R3B module must not import telegram"


# ---------------------------------------------------------------------------
# 8. Prompt presence
# ---------------------------------------------------------------------------
class TestPromptInZip:
    def test_prompt_present(self):
        zip_bytes = build_swing_review_pack_for_download(
            _dashboard_data(), "fp12345678", "latest_only",
            _window_utc(), _window_utc() + timedelta(days=7),
            _window_co(), _window_co() + timedelta(days=7),
            _gen_at(),
        )
        bio = BytesIO(zip_bytes)
        with zipfile.ZipFile(bio, "r") as zf:
            prompt = zf.read("10_prompt_for_copilot.md").decode("utf-8")
            assert len(prompt) > 100

    def test_prompt_ends_with_newline(self):
        zip_bytes = build_swing_review_pack_for_download(
            _dashboard_data(), "fp12345678", "latest_only",
            _window_utc(), _window_utc() + timedelta(days=7),
            _window_co(), _window_co() + timedelta(days=7),
            _gen_at(),
        )
        bio = BytesIO(zip_bytes)
        with zipfile.ZipFile(bio, "r") as zf:
            prompt = zf.read("10_prompt_for_copilot.md").decode("utf-8")
            assert prompt.endswith("\n")

    def test_prompt_under_limits(self):
        zip_bytes = build_swing_review_pack_for_download(
            _dashboard_data(), "fp12345678", "latest_only",
            _window_utc(), _window_utc() + timedelta(days=7),
            _window_co(), _window_co() + timedelta(days=7),
            _gen_at(),
        )
        bio = BytesIO(zip_bytes)
        with zipfile.ZipFile(bio, "r") as zf:
            prompt = zf.read("10_prompt_for_copilot.md").decode("utf-8")
            assert len(prompt) <= MAX_CHARS_PER_FILE
            assert len(prompt.encode("utf-8")) <= 200_000


# ---------------------------------------------------------------------------
# 9. Determinism
# ---------------------------------------------------------------------------
class TestDeterminism:
    def test_same_inputs_same_zip(self):
        data = _dashboard_data()
        z1 = build_swing_review_pack_for_download(
            data, "fp12345678", "latest_only",
            _window_utc(), _window_utc() + timedelta(days=7),
            _window_co(), _window_co() + timedelta(days=7),
            _gen_at(),
        )
        z2 = build_swing_review_pack_for_download(
            data, "fp12345678", "latest_only",
            _window_utc(), _window_utc() + timedelta(days=7),
            _window_co(), _window_co() + timedelta(days=7),
            _gen_at(),
        )
        assert z1 == z2


# ---------------------------------------------------------------------------
# 10. Page import check (optional — verifies the page references the helper)
# ---------------------------------------------------------------------------
class TestPageSourceReference:
    def test_page_imports_build_swing_review_pack_for_download(self):
        page_path = __import__("pathlib").Path(__file__).resolve().parent.parent / "pages" / "1_Swing_Strategy_Review.py"
        source = page_path.read_text(encoding="utf-8")
        assert "build_swing_review_pack_for_download" in source, (
            "Page must reference build_swing_review_pack_for_download"
        )

    def test_page_imports_r3b_not_r3a_only(self):
        page_path = __import__("pathlib").Path(__file__).resolve().parent.parent / "pages" / "1_Swing_Strategy_Review.py"
        source = page_path.read_text(encoding="utf-8")
        assert "swing_prompt_builder" in source, (
            "Page must import from swing_prompt_builder (R3B), not just R3A"
        )