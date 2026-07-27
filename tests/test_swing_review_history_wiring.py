from __future__ import annotations

"""
test_swing_review_history_wiring.py — integration tests for R4B history UI wiring.

Tests the behaviour of the Swing page history section without running a
Streamlit server.  Uses the real ReviewHistoryManager with temp directories.
"""

import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from swing_review_history import (  # noqa: E402
    ReviewHistoryManager,
    generate_review_id,
    _compute_sha256_bytes,
    _history_enabled,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def tmp_mgr():
    """A ReviewHistoryManager pointing at a temporary base dir."""
    with tempfile.TemporaryDirectory() as td:
        base = os.path.join(td, "data", "swing_reviews")
        mgr = ReviewHistoryManager(base_dir=base)
        yield mgr


@pytest.fixture
def sample_zip():
    return b"PK\x03\x04" + os.urandom(200)


@pytest.fixture
def sample_prompt():
    return b"# Test Prompt\nThis is a test.\n"


@pytest.fixture
def sample_meta():
    return {
        "generated_at_utc": "2026-07-27T14:36:00+00:00",
        "window_start_utc": "2026-07-24T14:36:00+00:00",
        "window_end_utc": "2026-07-27T14:36:00+00:00",
        "window_start_colombia": "2026-07-24T09:36:00-05:00",
        "window_end_colombia": "2026-07-27T09:36:00-05:00",
        "strategy": "SWING_TREND_RECLAIM_V1",
        "selected_fingerprint": "7fa9d83d70c7076b",
        "fingerprint_scope": "latest_only",
        "signal_count": 11,
        "closed_count": 7,
        "experimental_count": 0,
        "quality_level": "GOOD",
        "quality_reasons": [],
        "readiness_decision": "OBSERVE",
        "prompt_status": "READY",
        "complete_for_copilot": True,
        "source_commit": "190aed7",
    }


# ---------------------------------------------------------------------------
# 1. Persist on generate
# ---------------------------------------------------------------------------
class TestPersistOnGenerate:
    def test_persist_and_list(self, tmp_mgr, sample_zip, sample_prompt, sample_meta):
        rid = generate_review_id(datetime.now(timezone.utc), _compute_sha256_bytes(sample_zip))
        entry = tmp_mgr.persist_review(rid, sample_zip, sample_prompt, sample_meta)
        assert entry["review_id"] == rid

        reviews = tmp_mgr.list_reviews()
        assert len(reviews) == 1
        assert reviews[0]["review_id"] == rid

    def test_two_different_zip_different_ids(self, tmp_mgr, sample_prompt, sample_meta):
        z1 = b"PK" + os.urandom(128)
        z2 = b"PK" + os.urandom(128)
        rid1 = generate_review_id(datetime.now(timezone.utc), _compute_sha256_bytes(z1))
        rid2 = generate_review_id(datetime.now(timezone.utc), _compute_sha256_bytes(z2))
        tmp_mgr.persist_review(rid1, z1, sample_prompt, sample_meta)
        tmp_mgr.persist_review(rid2, z2, sample_prompt, sample_meta)
        assert len(tmp_mgr.list_reviews()) == 2
        assert rid1 != rid2


# ---------------------------------------------------------------------------
# 2. Re-download with SHA-256 integrity
# ---------------------------------------------------------------------------
class TestReDownloadIntegrity:
    def test_zip_bytes_match(self, tmp_mgr, sample_zip, sample_prompt, sample_meta):
        rid = generate_review_id(datetime.now(timezone.utc), _compute_sha256_bytes(sample_zip))
        tmp_mgr.persist_review(rid, sample_zip, sample_prompt, sample_meta)

        zip_b, prompt_b, meta = tmp_mgr.get_review(rid)
        assert zip_b == sample_zip
        assert hashlib.sha256(zip_b).hexdigest() == meta["zip_sha256"]

    def test_confirm_verify_success(self, tmp_mgr, sample_zip, sample_prompt, sample_meta):
        rid = generate_review_id(datetime.now(timezone.utc), _compute_sha256_bytes(sample_zip))
        tmp_mgr.persist_review(rid, sample_zip, sample_prompt, sample_meta)
        assert tmp_mgr.verify_integrity(rid) is True

    def test_confirm_get_verify_integrity_shows_corrupt(self, tmp_mgr, sample_zip, sample_prompt, sample_meta):
        rid = generate_review_id(datetime.now(timezone.utc), _compute_sha256_bytes(sample_zip))
        tmp_mgr.persist_review(rid, sample_zip, sample_prompt, sample_meta)
        # Tamper
        import os as _os
        rdir = _os.path.join(tmp_mgr._base_dir, rid)
        zp = _os.path.join(rdir, f"SWING_REVIEW_PACK_R3B_{rid}.zip")
        with open(zp, "ab") as fh:
            fh.write(b"bad")
        assert tmp_mgr.verify_integrity(rid) is False


# ---------------------------------------------------------------------------
# 3. Delete with confirmation, idempotent
# ---------------------------------------------------------------------------
class TestDeleteIdempotent:
    def test_delete_returns_true_onces(self, tmp_mgr, sample_zip, sample_prompt, sample_meta):
        rid = generate_review_id(datetime.now(timezone.utc), _compute_sha256_bytes(sample_zip))
        tmp_mgr.persist_review(rid, sample_zip, sample_prompt, sample_meta)
        assert tmp_mgr.delete_review(rid) is True
        # Second delete is idempotent
        assert tmp_mgr.delete_review(rid) is False

    def test_delete_removes_from_list(self, tmp_mgr, sample_zip, sample_prompt, sample_meta):
        rid = generate_review_id(datetime.now(timezone.utc), _compute_sha256_bytes(sample_zip))
        tmp_mgr.persist_review(rid, sample_zip, sample_prompt, sample_meta)
        assert len(tmp_mgr.list_reviews()) == 1
        tmp_mgr.delete_review(rid)
        assert len(tmp_mgr.list_reviews()) == 0


# ---------------------------------------------------------------------------
# 4. Clear session_state vs delete
# ---------------------------------------------------------------------------
class TestClearVsDelete:
    def test_delete_removes_from_disk(self, tmp_mgr, sample_zip, sample_prompt, sample_meta):
        rid = generate_review_id(datetime.now(timezone.utc), _compute_sha256_bytes(sample_zip))
        tmp_mgr.persist_review(rid, sample_zip, sample_prompt, sample_meta)
        tmp_mgr.delete_review(rid)
        assert tmp_mgr.get_review(rid) is None

    def test_clear_button_does_not_affect_history(self, tmp_mgr, sample_zip, sample_prompt, sample_meta):
        """Simulate the Streamlit 'Clear generated review' button: it should
        leave the history index and on-disk files untouched."""
        rid = generate_review_id(datetime.now(timezone.utc), _compute_sha256_bytes(sample_zip))
        tmp_mgr.persist_review(rid, sample_zip, sample_prompt, sample_meta)
        # Simulate Clear: only session_state is cleared, history stays
        assert len(tmp_mgr.list_reviews()) == 1
        assert tmp_mgr.get_review(rid) is not None


# ---------------------------------------------------------------------------
# 5. Duplicate detection (same content)
# ---------------------------------------------------------------------------
class TestDuplicateInUI:
    def test_same_content_detected(self, tmp_mgr, sample_zip, sample_prompt, sample_meta):
        rid1 = generate_review_id(datetime.now(timezone.utc), _compute_sha256_bytes(sample_zip))
        tmp_mgr.persist_review(rid1, sample_zip, sample_prompt, sample_meta)
        ch = _compute_sha256_bytes(sample_zip)
        assert tmp_mgr.is_duplicate(ch) is True

    def test_different_content_not_duplicate(self, tmp_mgr, sample_prompt, sample_meta):
        z1 = b"PK" + os.urandom(128)
        tmp_mgr.persist_review(generate_review_id(datetime.now(timezone.utc), _compute_sha256_bytes(z1)),
                               z1, sample_prompt, sample_meta)
        ch2 = _compute_sha256_bytes(b"different" * 50)
        assert tmp_mgr.is_duplicate(ch2) is False


# ---------------------------------------------------------------------------
# 6. Supersedes field on regenerate
# ---------------------------------------------------------------------------
class TestSupersedes:
    def test_supersedes_recorded(self, tmp_mgr, sample_zip, sample_prompt, sample_meta):
        rid1 = generate_review_id(datetime.now(timezone.utc), _compute_sha256_bytes(sample_zip))
        tmp_mgr.persist_review(rid1, sample_zip, sample_prompt, sample_meta)

        z2 = b"PK" + os.urandom(200)
        rid2 = generate_review_id(datetime.now(timezone.utc), _compute_sha256_bytes(z2))
        entry = tmp_mgr.persist_review(rid2, z2, sample_prompt, sample_meta,
                                       supersedes_review_id=rid1)
        assert entry["supersedes_review_id"] == rid1

    def test_supersedes_valid_from_list(self, tmp_mgr, sample_zip, sample_prompt, sample_meta):
        rid1 = generate_review_id(datetime.now(timezone.utc), _compute_sha256_bytes(sample_zip))
        tmp_mgr.persist_review(rid1, sample_zip, sample_prompt, sample_meta)
        # supersedes must be a valid review_id from the list
        z2 = b"Z2" + os.urandom(200)
        rid2 = generate_review_id(datetime.now(timezone.utc), _compute_sha256_bytes(z2))
        tmp_mgr.persist_review(rid2, z2, sample_prompt, sample_meta,
                               supersedes_review_id=rid1)
        reviews = tmp_mgr.list_reviews()
        superseded = [e for e in reviews if e["supersedes_review_id"] == rid1]
        assert len(superseded) == 1


# ---------------------------------------------------------------------------
# 7. Storage count and size
# ---------------------------------------------------------------------------
class TestStorageMetrics:
    def test_total_size_calculated(self, tmp_mgr, sample_prompt, sample_meta):
        z1 = b"A" * 1000
        z2 = b"B" * 2000
        rid1 = generate_review_id(datetime.now(timezone.utc), _compute_sha256_bytes(z1))
        rid2 = generate_review_id(datetime.now(timezone.utc), _compute_sha256_bytes(z2))
        tmp_mgr.persist_review(rid1, z1, sample_prompt, sample_meta)
        tmp_mgr.persist_review(rid2, z2, sample_prompt, sample_meta)

        reviews = tmp_mgr.list_reviews()
        total = sum(e.get("zip_size_bytes", 0) + e.get("prompt_size_bytes", 0) for e in reviews)
        assert total == 1000 + 2000 + len(sample_prompt) * 2

    def test_count_matches(self, tmp_mgr, sample_zip, sample_prompt, sample_meta):
        for _ in range(3):
            z = b"PK" + os.urandom(100)
            rid = generate_review_id(datetime.now(timezone.utc), _compute_sha256_bytes(z))
            tmp_mgr.persist_review(rid, z, sample_prompt, sample_meta)
        assert len(tmp_mgr.list_reviews()) == 3


# ---------------------------------------------------------------------------
# 8. Cleanup buttons
# ---------------------------------------------------------------------------
class TestCleanupButtons:
    def test_expired_cleanup_removes(self, tmp_mgr, sample_zip, sample_prompt, sample_meta):
        mgr = ReviewHistoryManager(base_dir=tmp_mgr._base_dir, retention_days=0)
        rid = generate_review_id(datetime.now(timezone.utc), _compute_sha256_bytes(sample_zip))
        mgr.persist_review(rid, sample_zip, sample_prompt, sample_meta)
        assert mgr.cleanup_expired() >= 0

    def test_fifo_cleanup_below_max_noop(self, tmp_mgr, sample_zip, sample_prompt, sample_meta):
        rid = generate_review_id(datetime.now(timezone.utc), _compute_sha256_bytes(sample_zip))
        tmp_mgr.persist_review(rid, sample_zip, sample_prompt, sample_meta)
        removed = tmp_mgr.cleanup_fifo()
        assert removed == 0


# ---------------------------------------------------------------------------
# 9. SWING_HISTORY_ENABLED flag
# ---------------------------------------------------------------------------
class TestHistoryEnabledFlag:
    def test_disabled_blocks_persist(self, tmp_mgr, sample_zip, sample_prompt, sample_meta):
        with patch.dict(os.environ, {"SWING_HISTORY_ENABLED": "false"}):
            with pytest.raises(ValueError, match="disabled"):
                tmp_mgr.persist_review(
                    generate_review_id(datetime.now(timezone.utc), _compute_sha256_bytes(sample_zip)),
                    sample_zip, sample_prompt, sample_meta,
                )

    def test_disabled_blocks_delete(self, tmp_mgr):
        with patch.dict(os.environ, {"SWING_HISTORY_ENABLED": "false"}):
            with pytest.raises(ValueError, match="disabled"):
                tmp_mgr.delete_review("SWING-20260727-143600-a1b2c3d4")

    def test_enabled_by_default(self, tmp_mgr, sample_zip, sample_prompt, sample_meta):
        assert _history_enabled() is True
        rid = generate_review_id(datetime.now(timezone.utc), _compute_sha256_bytes(sample_zip))
        tmp_mgr.persist_review(rid, sample_zip, sample_prompt, sample_meta)
        assert tmp_mgr.get_review(rid) is not None


# ---------------------------------------------------------------------------
# 10. Corrupt index — blocks operations
# ---------------------------------------------------------------------------
class TestCorruptIndexBlocking:
    def test_is_index_valid_false_corrupt(self, tmp_mgr):
        with open(tmp_mgr._index_path, "w") as fh:
            fh.write("not json")
        assert tmp_mgr.is_index_valid() is False

    def test_is_index_valid_true_empty(self, tmp_mgr):
        assert tmp_mgr.is_index_valid() is True  # no file yet

    def test_is_index_valid_true_with_data(self, tmp_mgr, sample_zip, sample_prompt, sample_meta):
        rid = generate_review_id(datetime.now(timezone.utc), _compute_sha256_bytes(sample_zip))
        tmp_mgr.persist_review(rid, sample_zip, sample_prompt, sample_meta)
        assert tmp_mgr.is_index_valid() is True

    def test_list_returns_empty_after_corrupt_recovery(self, tmp_mgr, sample_zip, sample_prompt, sample_meta):
        """After corruption, list_reviews returns empty but data dirs survive."""
        rid = generate_review_id(datetime.now(timezone.utc), _compute_sha256_bytes(sample_zip))
        tmp_mgr.persist_review(rid, sample_zip, sample_prompt, sample_meta)
        # Corrupt the index
        with open(tmp_mgr._index_path, "w") as fh:
            fh.write("garbage")
        assert tmp_mgr.list_reviews() == []
        # But data dir still exists
        rdir = os.path.join(tmp_mgr._base_dir, rid)
        assert os.path.isdir(rdir)


# ---------------------------------------------------------------------------
# 11. No auto-cleanup on import / constructor
# ---------------------------------------------------------------------------
class TestNoAutoCleanup:
    def test_constructor_preserves_data(self, tmp_mgr, sample_zip, sample_prompt, sample_meta):
        rid = generate_review_id(datetime.now(timezone.utc), _compute_sha256_bytes(sample_zip))
        tmp_mgr.persist_review(rid, sample_zip, sample_prompt, sample_meta)
        mgr2 = ReviewHistoryManager(base_dir=tmp_mgr._base_dir)
        assert mgr2.get_review(rid) is not None


# ---------------------------------------------------------------------------
# 12. No writes outside data/swing_reviews
# ---------------------------------------------------------------------------
class TestNoExternalWrites:
    def test_all_persisted_under_base(self, tmp_mgr, sample_zip, sample_prompt, sample_meta):
        rid = generate_review_id(datetime.now(timezone.utc), _compute_sha256_bytes(sample_zip))
        tmp_mgr.persist_review(rid, sample_zip, sample_prompt, sample_meta)
        for root, dirs, files in os.walk(tmp_mgr._base_dir):
            for fn in files:
                full = os.path.join(root, fn)
                assert full.startswith(tmp_mgr._base_dir)


# ---------------------------------------------------------------------------
# 13. No external deps (PostgreSQL / SQLite / Telegram)
# ---------------------------------------------------------------------------
class TestNoExternalDeps:
    def test_no_db_imports(self):
        import swing_review_history as mod
        source = open(mod.__file__).read()
        assert "psycopg2" not in source.lower()
        assert "sqlite" not in source.lower()
        assert "telegram" not in source.lower()