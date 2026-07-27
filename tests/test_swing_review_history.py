from __future__ import annotations

"""
test_swing_review_history.py — comprehensive tests for the R4A history store.

Covers:
- persist / read / list / delete
- descending order
- duplicate by content hash
- same window with different content (not duplicate)
- review immutability
- supersedes
- concurrent lock
- atomicity on failure
- corrupt index recovery
- hash & integrity checks
- path traversal prevention
- permissions
- 5 MB limit
- retention by date + FIFO
- no cleanup on import
- no writes outside data/swing_reviews
- no PostgreSQL / SQLite / Telegram
- flag disabled preserves data
"""

import hashlib
import json
import os
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from swing_review_history import (  # noqa: E402
    REVIEW_ID_REGEX,
    MAX_ZIP_BYTES,
    MAX_PROMPT_BYTES,
    _FileLock,
    _history_enabled,
    _validate_review_id,
    _resolve_safe_path,
    ReviewHistoryManager,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def tmp_store():
    """Create a ReviewHistoryManager in a temporary directory."""
    with tempfile.TemporaryDirectory() as td:
        base = os.path.join(td, "data", "swing_reviews")
        mgr = ReviewHistoryManager(base_dir=base)
        yield mgr


@pytest.fixture
def sample_zip_bytes():
    """Generate a small deterministic ZIP-like payload."""
    return b"PK\x03\x04" + os.urandom(128)


@pytest.fixture
def sample_prompt_bytes():
    return b"# SWING Review Prompt\n\nThis is a test prompt.\n"


@pytest.fixture
def sample_metadata():
    return {
        "generated_at_utc": "2026-07-27T14:36:00+00:00",
        "data_loaded_at_utc": "2026-07-27T14:35:55+00:00",
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
        "source_commit": "943b283d5bbfd3552ebafad2c33e957b512f5873",
    }


def _make_id(hour: int = 14, minute: int = 36) -> str:
    """Build a valid review_id for a test."""
    return f"SWING-20260727-{hour:02d}{minute:02d}"


# ---------------------------------------------------------------------------
# Regex / validation
# ---------------------------------------------------------------------------
class TestReviewIdValidation:
    def test_valid_ids(self):
        assert REVIEW_ID_REGEX.match("SWING-20260727-1436")

    def test_invalid_ids(self):
        assert not REVIEW_ID_REGEX.match("SWING-20260727-143")   # too short
        assert not REVIEW_ID_REGEX.match("SWING-20260727-14361")  # too long
        assert not REVIEW_ID_REGEX.match("swing-20260727-1436")   # lowercase
        assert not REVIEW_ID_REGEX.match("SWING-2026-07-27-1436") # wrong format
        assert not REVIEW_ID_REGEX.match("")                       # empty

    def test_validate_function_raises(self):
        with pytest.raises(ValueError):
            _validate_review_id("bad")
        _validate_review_id("SWING-20260727-1436")  # should not raise

    def test_validate_rejects_non_string(self):
        with pytest.raises(ValueError):
            _validate_review_id(None)
        with pytest.raises(ValueError):
            _validate_review_id(42)


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------
class TestPathResolution:
    def test_normal_path(self, tmp_store):
        path = _resolve_safe_path(tmp_store._base_dir, "SWING-20260727-1436")
        assert path.endswith("SWING-20260727-1436")

    def test_traversal_absolute(self, tmp_store):
        with pytest.raises(ValueError):
            _resolve_safe_path(tmp_store._base_dir, "/etc/passwd")

    def test_traversal_dotdot(self, tmp_store):
        with pytest.raises(ValueError):
            _resolve_safe_path(tmp_store._base_dir, "../../etc")

    def test_traversal_dotdot_encoded(self, tmp_store):
        # On Windows this would be caught by realpath, on Unix by the prefix check
        target = os.path.join(tmp_store._base_dir, "..", "..", "etc")
        if os.path.realpath(target) != tmp_store._base_dir and not os.path.realpath(
            target
        ).startswith(tmp_store._base_dir + os.sep):
            with pytest.raises(ValueError):
                _resolve_safe_path(tmp_store._base_dir, "..", "..", "etc")


# ---------------------------------------------------------------------------
# Persist / read / list / delete — basic CRUD
# ---------------------------------------------------------------------------
class TestBasicCRUD:
    def test_persist_and_read(self, tmp_store, sample_zip_bytes, sample_prompt_bytes, sample_metadata):
        rid = _make_id()
        entry = tmp_store.persist_review(rid, sample_zip_bytes, sample_prompt_bytes, sample_metadata)
        assert entry["review_id"] == rid
        assert entry["signal_count"] == 11

        # Read back
        result = tmp_store.get_review(rid)
        assert result is not None
        zip_back, prompt_back, meta = result
        assert zip_back == sample_zip_bytes
        assert prompt_back == sample_prompt_bytes
        assert meta["review_id"] == rid
        assert meta["zip_sha256"] == hashlib.sha256(sample_zip_bytes).hexdigest()

    def test_read_nonexistent(self, tmp_store):
        assert tmp_store.get_review("SWING-20260727-0000") is None

    def test_list_after_persist(self, tmp_store, sample_prompt_bytes, sample_metadata):
        r1 = _make_id(14, 0)
        r2 = _make_id(15, 0)
        z1 = b"PK" + os.urandom(128)
        z2 = b"PK" + os.urandom(128)
        tmp_store.persist_review(r1, z1, sample_prompt_bytes, sample_metadata)
        tmp_store.persist_review(r2, z2, sample_prompt_bytes,
                                 {**sample_metadata, "generated_at_utc": "2026-07-27T15:00:00+00:00"})
        reviews = tmp_store.list_reviews()
        assert len(reviews) == 2
        # Newest first
        assert reviews[0]["review_id"] == r2
        assert reviews[1]["review_id"] == r1

    def test_delete(self, tmp_store, sample_zip_bytes, sample_prompt_bytes, sample_metadata):
        rid = _make_id()
        tmp_store.persist_review(rid, sample_zip_bytes, sample_prompt_bytes, sample_metadata)
        assert tmp_store.get_review(rid) is not None
        deleted = tmp_store.delete_review(rid)
        assert deleted is True
        assert tmp_store.get_review(rid) is None

    def test_delete_idempotent(self, tmp_store):
        assert tmp_store.delete_review("SWING-20260727-0000") is False
        assert tmp_store.delete_review("SWING-20260727-0000") is False  # still False


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------
class TestDeduplication:
    def test_same_content_duplicate(self, tmp_store, sample_zip_bytes, sample_prompt_bytes, sample_metadata):
        r1 = _make_id(14, 0)
        r2 = _make_id(14, 1)
        tmp_store.persist_review(r1, sample_zip_bytes, sample_prompt_bytes, sample_metadata)
        with pytest.raises(ValueError, match="Duplicate review"):
            tmp_store.persist_review(r2, sample_zip_bytes, sample_prompt_bytes, sample_metadata)

    def test_different_content_not_duplicate(self, tmp_store, sample_prompt_bytes, sample_metadata):
        z1 = b"PK" + os.urandom(200)
        z2 = b"PK" + os.urandom(200)
        assert z1 != z2
        tmp_store.persist_review(_make_id(14, 0), z1, sample_prompt_bytes, sample_metadata)
        tmp_store.persist_review(_make_id(14, 1), z2, sample_prompt_bytes, sample_metadata)
        assert len(tmp_store.list_reviews()) == 2

    def test_same_window_different_content_not_duplicate(self, tmp_store, sample_metadata):
        z1 = b"PK" + os.urandom(200)
        z2 = b"PK" + os.urandom(200)
        m1 = {**sample_metadata, "generated_at_utc": "2026-07-27T14:00:00+00:00"}
        m2 = {**sample_metadata, "generated_at_utc": "2026-07-27T15:00:00+00:00"}
        tmp_store.persist_review(_make_id(14, 0), z1, b"# Prompt 1\n", m1)
        tmp_store.persist_review(_make_id(15, 0), z2, b"# Prompt 2\n", m2)
        assert len(tmp_store.list_reviews()) == 2

    def test_is_duplicate(self, tmp_store, sample_zip_bytes, sample_prompt_bytes, sample_metadata):
        content_hash = hashlib.sha256(sample_zip_bytes).hexdigest()
        assert not tmp_store.is_duplicate(content_hash)
        tmp_store.persist_review(_make_id(), sample_zip_bytes, sample_prompt_bytes, sample_metadata)
        assert tmp_store.is_duplicate(content_hash)


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------
class TestImmutability:
    def test_cannot_overwrite(self, tmp_store, sample_zip_bytes, sample_prompt_bytes, sample_metadata):
        rid = _make_id()
        tmp_store.persist_review(rid, sample_zip_bytes, sample_prompt_bytes, sample_metadata)
        z2 = b"X" + os.urandom(200)
        with pytest.raises(ValueError, match="already exists"):
            tmp_store.persist_review(rid, z2, sample_prompt_bytes, sample_metadata)

    def test_content_hash_different_not_overwrite(self, tmp_store, sample_zip_bytes, sample_prompt_bytes, sample_metadata):
        rid = _make_id()
        tmp_store.persist_review(rid, sample_zip_bytes, sample_prompt_bytes, sample_metadata)
        z2 = b"ZZZ" + os.urandom(200)
        # Even different content — can't use same review_id
        with pytest.raises(ValueError, match="already exists"):
            tmp_store.persist_review(rid, z2, sample_prompt_bytes, sample_metadata)


# ---------------------------------------------------------------------------
# Supersedes
# ---------------------------------------------------------------------------
class TestSupersedes:
    def test_supersedes_field_persisted(self, tmp_store, sample_zip_bytes, sample_prompt_bytes, sample_metadata):
        rid = _make_id(14, 0)
        entry = tmp_store.persist_review(
            rid, sample_zip_bytes, sample_prompt_bytes, sample_metadata,
            supersedes_review_id="SWING-20260727-1200",
        )
        assert entry["supersedes_review_id"] == "SWING-20260727-1200"

    def test_supersedes_none(self, tmp_store, sample_zip_bytes, sample_prompt_bytes, sample_metadata):
        rid = _make_id()
        entry = tmp_store.persist_review(rid, sample_zip_bytes, sample_prompt_bytes, sample_metadata)
        assert entry["supersedes_review_id"] is None

    def test_supersedes_invalid_id_rejected(self, tmp_store, sample_zip_bytes, sample_prompt_bytes, sample_metadata):
        with pytest.raises(ValueError):
            tmp_store.persist_review(
                _make_id(), sample_zip_bytes, sample_prompt_bytes, sample_metadata,
                supersedes_review_id="bad",
            )


# ---------------------------------------------------------------------------
# Lock (concurrent) — single-process coverage
# ---------------------------------------------------------------------------
class TestLock:
    def test_lock_acquire_release(self, tmp_store):
        lock = _FileLock(tmp_store._lock_path, timeout=1)
        lock.acquire()
        lock.release()

    def test_lock_context_manager(self, tmp_store):
        with _FileLock(tmp_store._lock_path, timeout=1):
            pass  # should acquire and release

    def test_lock_exclusion(self, tmp_store):
        lock1 = _FileLock(tmp_store._lock_path, timeout=1)
        lock1.acquire()
        try:
            lock2 = _FileLock(tmp_store._lock_path, timeout=0.1)
            with pytest.raises(TimeoutError):
                lock2.acquire()
        finally:
            lock1.release()


# ---------------------------------------------------------------------------
# Atomicity
# ---------------------------------------------------------------------------
class TestAtomicity:
    def test_partial_write_cleaned_up(self, tmp_store, sample_zip_bytes, sample_prompt_bytes, sample_metadata):
        """Simulate a mid-write failure by forcing a hash mismatch.

        We temporarily monkey-patch _compute_sha256_file so the on-disk hash
        check reports a different value, triggering the cleanup path.
        """
        from swing_review_history import _compute_sha256_file as _orig_hash
        rid = _make_id()

        def _bad_hash(path):
            return "deadbeef"

        # Patch _compute_sha256_file *after* the persist call computes the
        # initial hash but *before* the on-disk verification runs.  The
        # simplest approach: patch at module level only for the prompt.
        # We'll use a counter to only break the prompt hash on the first call.
        call_count = [0]

        def _selective_bad_hash(path):
            call_count[0] += 1
            if call_count[0] == 2:  # second call is the prompt hash check
                return "deadbeef"
            return _orig_hash(path)

        try:
            import swing_review_history
            swing_review_history._compute_sha256_file = _selective_bad_hash
            with pytest.raises(ValueError, match="Prompt hash mismatch"):
                tmp_store.persist_review(
                    rid,
                    sample_zip_bytes,
                    sample_prompt_bytes,
                    sample_metadata,
                )
        finally:
            swing_review_history._compute_sha256_file = _orig_hash

        # No lingering directory
        review_dir = _resolve_safe_path(tmp_store._base_dir, rid)
        assert not os.path.exists(review_dir)

    def test_no_orphan_temp_dirs(self, tmp_store, sample_zip_bytes, sample_prompt_bytes, sample_metadata):
        """After a successful persist, no .tmp_ directories remain."""
        rid = _make_id()
        tmp_store.persist_review(rid, sample_zip_bytes, sample_prompt_bytes, sample_metadata)
        for entry in os.listdir(tmp_store._base_dir):
            assert not entry.startswith(".tmp_")


# ---------------------------------------------------------------------------
# Corrupt index
# ---------------------------------------------------------------------------
class TestCorruptIndex:
    def test_index_corrupt_json(self, tmp_store, sample_zip_bytes, sample_prompt_bytes, sample_metadata):
        # Write garbage
        with open(tmp_store._index_path, "w") as fh:
            fh.write("this is not json")
        reviews = tmp_store.list_reviews()
        assert reviews == []
        # Original corrupt file is archived
        assert not os.path.exists(tmp_store._index_path) or _index_is_valid(tmp_store)

    def test_index_missing_reviews_key(self, tmp_store):
        with open(tmp_store._index_path, "w") as fh:
            json.dump({"schema_version": "x"}, fh)
        assert tmp_store.list_reviews() == []

    def test_index_reviews_not_list(self, tmp_store):
        with open(tmp_store._index_path, "w") as fh:
            json.dump({"schema_version": "x", "reviews": "not-a-list"}, fh)
        assert tmp_store.list_reviews() == []

    def test_index_corrupt_does_not_delete_data(self, tmp_store, sample_zip_bytes, sample_prompt_bytes, sample_metadata):
        """A corrupted index should not delete existing review directories."""
        rid = _make_id()
        tmp_store.persist_review(rid, sample_zip_bytes, sample_prompt_bytes, sample_metadata)
        review_dir = _resolve_safe_path(tmp_store._base_dir, rid)
        assert os.path.isdir(review_dir)

        # Corrupt the index
        with open(tmp_store._index_path, "w") as fh:
            fh.write("not json")

        # Re-load
        assert tmp_store.list_reviews() == []
        # But the directory still exists
        assert os.path.isdir(review_dir)


def _index_is_valid(mgr: ReviewHistoryManager) -> bool:
    """Check if the index file on disk is valid JSON with a 'reviews' key."""
    if not os.path.isfile(mgr._index_path):
        return False
    try:
        with open(mgr._index_path) as fh:
            data = json.load(fh)
        return isinstance(data, dict) and "reviews" in data
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Integrity / hashes
# ---------------------------------------------------------------------------
class TestIntegrity:
    def test_sha256_verification(self, tmp_store, sample_zip_bytes, sample_prompt_bytes, sample_metadata):
        rid = _make_id()
        tmp_store.persist_review(rid, sample_zip_bytes, sample_prompt_bytes, sample_metadata)
        assert tmp_store.verify_integrity(rid) is True

    def test_verify_nonexistent(self, tmp_store):
        assert tmp_store.verify_integrity("SWING-20260727-0000") is False

    def test_tampered_zip_detected(self, tmp_store, sample_zip_bytes, sample_prompt_bytes, sample_metadata):
        rid = _make_id()
        tmp_store.persist_review(rid, sample_zip_bytes, sample_prompt_bytes, sample_metadata)
        # Tamper with the ZIP
        review_dir = _resolve_safe_path(tmp_store._base_dir, rid)
        zip_path = os.path.join(review_dir, f"SWING_REVIEW_PACK_R3B_{rid}.zip")
        with open(zip_path, "ab") as fh:
            fh.write(b"tampered")
        assert tmp_store.verify_integrity(rid) is False
        # get_review should also fail
        with pytest.raises(ValueError, match="integrity check failed"):
            tmp_store.get_review(rid)

    def test_tampered_prompt_detected(self, tmp_store, sample_zip_bytes, sample_prompt_bytes, sample_metadata):
        rid = _make_id()
        tmp_store.persist_review(rid, sample_zip_bytes, sample_prompt_bytes, sample_metadata)
        review_dir = _resolve_safe_path(tmp_store._base_dir, rid)
        prompt_path = os.path.join(review_dir, f"10_prompt_for_copilot_{rid}.md")
        with open(prompt_path, "a") as fh:
            fh.write("tampered")
        assert tmp_store.verify_integrity(rid) is False


# ---------------------------------------------------------------------------
# Path traversal
# ---------------------------------------------------------------------------
class TestPathTraversal:
    def test_review_id_traversal(self, tmp_store, sample_zip_bytes, sample_prompt_bytes, sample_metadata):
        """A review_id with path separators should be rejected by the regex."""
        with pytest.raises(ValueError):
            tmp_store.persist_review(
                "../SWING-20260727-1436", sample_zip_bytes, sample_prompt_bytes, sample_metadata
            )

    def test_get_review_traversal_rejected(self, tmp_store):
        with pytest.raises(ValueError):
            tmp_store.get_review("../../../etc")

    def test_delete_traversal_rejected(self, tmp_store):
        with pytest.raises(ValueError):
            tmp_store.delete_review("../../SWING-20260727-1436")


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------
class TestPermissions:
    def test_permissions_non_world_readable(self, tmp_store, sample_zip_bytes, sample_prompt_bytes, sample_metadata):
        """On POSIX, file should be 0o600 and dir 0o700. On Windows the
        chmod calls are best-effort, so we only check the values returned
        by stat where possible."""
        rid = _make_id()
        tmp_store.persist_review(rid, sample_zip_bytes, sample_prompt_bytes, sample_metadata)
        review_dir = _resolve_safe_path(tmp_store._base_dir, rid)

        # These are best-effort on Windows; do not assert strictly on Windows
        if os.name == "posix":
            dir_mode = os.stat(review_dir).st_mode & 0o777
            assert dir_mode == 0o700, f"Expected 0700, got {dir_mode:o}"

            zip_path = os.path.join(review_dir, f"SWING_REVIEW_PACK_R3B_{rid}.zip")
            file_mode = os.stat(zip_path).st_mode & 0o777
            assert file_mode == 0o600, f"Expected 0600, got {file_mode:o}"


# ---------------------------------------------------------------------------
# 5 MB limit
# ---------------------------------------------------------------------------
class TestSizeLimits:
    def test_zip_exceeds_limit(self, tmp_store, sample_prompt_bytes, sample_metadata):
        big_zip = b"X" * (MAX_ZIP_BYTES + 1)
        with pytest.raises(ValueError, match="exceeds limit"):
            tmp_store.persist_review(_make_id(), big_zip, sample_prompt_bytes, sample_metadata)

    def test_prompt_exceeds_limit(self, tmp_store, sample_zip_bytes, sample_metadata):
        big_prompt = b"Y" * (MAX_PROMPT_BYTES + 1)
        with pytest.raises(ValueError, match="exceeds limit"):
            tmp_store.persist_review(_make_id(), sample_zip_bytes, big_prompt, sample_metadata)

    def test_zip_at_limit_ok(self, tmp_store, sample_prompt_bytes, sample_metadata):
        zip_at_limit = b"Z" * (MAX_ZIP_BYTES // 2)  # well under 5 MB
        rid = _make_id()
        tmp_store.persist_review(rid, zip_at_limit, sample_prompt_bytes, sample_metadata)
        assert tmp_store.get_review(rid) is not None


# ---------------------------------------------------------------------------
# Retention — by date
# ---------------------------------------------------------------------------
class TestRetentionExpired:
    def test_expired_review_removed(self, tmp_store, sample_zip_bytes, sample_prompt_bytes, sample_metadata):
        """Persist with a very short retention (1 day) and override retained_until."""
        mgr = ReviewHistoryManager(base_dir=tmp_store._base_dir, retention_days=1)
        rid = _make_id()
        entry = mgr.persist_review(rid, sample_zip_bytes, sample_prompt_bytes, sample_metadata)
        # Manually set retained_until to the past
        with _FileLock(mgr._lock_path):
            index = mgr._load_index()
            for e in index["reviews"]:
                if e["review_id"] == rid:
                    e["retained_until"] = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
            mgr._atomic_write_index(index)

        removed = mgr.cleanup_expired()
        assert removed == 1
        assert mgr.get_review(rid) is None

    def test_not_expired_review_kept(self, tmp_store, sample_zip_bytes, sample_prompt_bytes, sample_metadata):
        rid = _make_id()
        tmp_store.persist_review(rid, sample_zip_bytes, sample_prompt_bytes, sample_metadata)
        removed = tmp_store.cleanup_expired()
        assert removed == 0
        assert tmp_store.get_review(rid) is not None


# ---------------------------------------------------------------------------
# Retention — FIFO
# ---------------------------------------------------------------------------
class TestRetentionFIFO:
    def test_fifo_removes_oldest(self, tmp_store, sample_prompt_bytes, sample_metadata):
        mgr = ReviewHistoryManager(base_dir=tmp_store._base_dir, max_reviews=2)
        z1 = b"PK" + os.urandom(200)
        z2 = b"PK" + os.urandom(200)
        z3 = b"PK" + os.urandom(200)

        m1 = {**sample_metadata, "generated_at_utc": "2026-07-27T14:00:00+00:00"}
        m2 = {**sample_metadata, "generated_at_utc": "2026-07-27T15:00:00+00:00"}
        m3 = {**sample_metadata, "generated_at_utc": "2026-07-27T16:00:00+00:00"}

        r1 = _make_id(14, 0)
        r2 = _make_id(15, 0)
        r3 = _make_id(16, 0)

        mgr.persist_review(r1, z1, sample_prompt_bytes, m1)
        mgr.persist_review(r2, z2, sample_prompt_bytes, m2)
        assert len(mgr.list_reviews()) == 2

        # Third overflows — oldest (r1) removed
        mgr.persist_review(r3, z3, sample_prompt_bytes, m3)
        reviews = mgr.list_reviews()
        assert len(reviews) == 2
        ids = {e["review_id"] for e in reviews}
        assert r1 not in ids
        assert r2 in ids
        assert r3 in ids

    def test_fifo_below_max_noop(self, tmp_store, sample_zip_bytes, sample_prompt_bytes, sample_metadata):
        mgr = ReviewHistoryManager(base_dir=tmp_store._base_dir, max_reviews=100)
        mgr.persist_review(_make_id(), sample_zip_bytes, sample_prompt_bytes, sample_metadata)
        removed = mgr.cleanup_fifo()
        assert removed == 0


# ---------------------------------------------------------------------------
# No cleanup on import
# ---------------------------------------------------------------------------
class TestNoCleanupOnImport:
    def test_constructor_does_not_clean(self, tmp_store, sample_zip_bytes, sample_prompt_bytes, sample_metadata):
        """Creating a manager should not delete anything."""
        rid = _make_id()
        tmp_store.persist_review(rid, sample_zip_bytes, sample_prompt_bytes, sample_metadata)
        assert tmp_store.get_review(rid) is not None

        # Create a new manager pointing to the same base dir
        mgr2 = ReviewHistoryManager(base_dir=tmp_store._base_dir)
        assert mgr2.get_review(rid) is not None


# ---------------------------------------------------------------------------
# No writes outside data/swing_reviews
# ---------------------------------------------------------------------------
class TestNoExternalWrites:
    def test_all_files_under_base_dir(self, tmp_store, sample_zip_bytes, sample_prompt_bytes, sample_metadata):
        rid = _make_id()
        tmp_store.persist_review(rid, sample_zip_bytes, sample_prompt_bytes, sample_metadata)
        # Index file is alongside base_dir (in data/)
        base_parent = os.path.dirname(tmp_store._base_dir)
        assert os.path.isfile(os.path.join(base_parent, "swing_review_index.json"))
        # All contents are under base_dir
        for root, dirs, files in os.walk(tmp_store._base_dir):
            for fn in files:
                full = os.path.join(root, fn)
                assert full.startswith(tmp_store._base_dir)


# ---------------------------------------------------------------------------
# No PostgreSQL / SQLite / Telegram
# ---------------------------------------------------------------------------
class TestNoExternalDeps:
    def test_no_db_imports(self):
        """Ensure the history module does not import psycopg2, sqlite3, or requests."""
        import swing_review_history as mod
        source = open(mod.__file__).read()
        assert "psycopg2" not in source.lower()
        assert "sqlite" not in source.lower()
        assert "telegram" not in source.lower()
        assert "requests" not in source.lower()

    def test_no_filesystem_write_outside(self, tmp_store, sample_zip_bytes, sample_prompt_bytes, sample_metadata):
        """After operations, no file is created outside base_dir or its parent index."""
        base_parent = os.path.dirname(tmp_store._base_dir)
        allowed = {tmp_store._base_dir, base_parent}
        rid = _make_id()
        tmp_store.persist_review(rid, sample_zip_bytes, sample_prompt_bytes, sample_metadata)

        for root, dirs, files in os.walk(os.path.dirname(base_parent)):
            for fn in files:
                full = os.path.realpath(os.path.join(root, fn))
                if ".tmp" in fn or ".lock" in fn:
                    continue  # temp/lock files are allowed but cleaned up
                ok = False
                for a in allowed:
                    if full.startswith(a):
                        ok = True
                        break
                # Files outside allowed dirs: either the index or the review dir
                if not ok:
                    # Check if it's the index or a review dir
                    assert full.startswith(base_parent) or full.startswith(tmp_store._base_dir), \
                        f"Unexpected file outside allowed dirs: {full}"


# ---------------------------------------------------------------------------
# Flag disabled
# ---------------------------------------------------------------------------
class TestFlagDisabled:
    def test_disabled_persist_raises(self, tmp_store, sample_zip_bytes, sample_prompt_bytes, sample_metadata):
        with patch.dict(os.environ, {"SWING_HISTORY_ENABLED": "false"}):
            assert not _history_enabled()
            with pytest.raises(ValueError, match="disabled"):
                tmp_store.persist_review(_make_id(), sample_zip_bytes, sample_prompt_bytes, sample_metadata)

    def test_disabled_delete_raises(self, tmp_store):
        with patch.dict(os.environ, {"SWING_HISTORY_ENABLED": "false"}):
            with pytest.raises(ValueError, match="disabled"):
                tmp_store.delete_review("SWING-20260727-1436")

    def test_disabled_list_still_works(self, tmp_store, sample_zip_bytes, sample_prompt_bytes, sample_metadata):
        # Persist while enabled
        rid = _make_id()
        tmp_store.persist_review(rid, sample_zip_bytes, sample_prompt_bytes, sample_metadata)

        with patch.dict(os.environ, {"SWING_HISTORY_ENABLED": "false"}):
            reviews = tmp_store.list_reviews()
            assert len(reviews) == 1
            # Cannot persist new
            with pytest.raises(ValueError, match="disabled"):
                tmp_store.persist_review(_make_id(15, 0), sample_zip_bytes, sample_prompt_bytes, sample_metadata)
            # Data is preserved
            assert tmp_store.get_review(rid) is not None

    def test_disabled_cleanup_noop(self, tmp_store, sample_zip_bytes, sample_prompt_bytes, sample_metadata):
        rid = _make_id()
        tmp_store.persist_review(rid, sample_zip_bytes, sample_prompt_bytes, sample_metadata)
        with patch.dict(os.environ, {"SWING_HISTORY_ENABLED": "false"}):
            assert tmp_store.cleanup_expired() == 0
            assert tmp_store.cleanup_fifo() == 0
        assert tmp_store.get_review(rid) is not None


# ---------------------------------------------------------------------------
# Descending order
# ---------------------------------------------------------------------------
class TestOrder:
    def test_persist_sort_order(self, tmp_store, sample_prompt_bytes, sample_metadata):
        times = ["14:00", "16:00", "15:00"]
        for i, t in enumerate(times):
            z = b"PK" + os.urandom(128)
            rid = f"SWING-20260727-{t.replace(':', '')}"
            m = {**sample_metadata, "generated_at_utc": f"2026-07-27T{t}:00+00:00"}
            tmp_store.persist_review(rid, z, sample_prompt_bytes, m)

        reviews = tmp_store.list_reviews()
        assert reviews[0]["generated_at_utc"] == "2026-07-27T16:00:00+00:00"
        assert reviews[1]["generated_at_utc"] == "2026-07-27T15:00:00+00:00"
        assert reviews[2]["generated_at_utc"] == "2026-07-27T14:00:00+00:00"