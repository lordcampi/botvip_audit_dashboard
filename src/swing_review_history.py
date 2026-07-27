from __future__ import annotations

"""
swing_review_history.py — R4A local SWING review history store.

Persists ZIP + prompt under data/swing_reviews/{review_id}/.
Maintains an atomic, lock-protected JSON index.
Never writes to PostgreSQL, or outside the authorised directory.
"""

import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
REVIEW_ID_REGEX = re.compile(r"^SWING-\d{8}-\d{4,6}(-[a-f0-9]{8})?$")


def generate_review_id(gen_at: datetime, content_hash: str) -> str:
    """Generate a collision-free review_id.

    Format: ``SWING-YYYYMMDD-HHMMSS-<hash8>`` where hash8 is the first 8 hex
    characters of the ZIP content SHA-256.

    Two reviews generated in the same second with different content produce
    different IDs.  Two reviews with the same content produce the same ID
    (and will be rejected as duplicates by persist_review).
    """
    ts = gen_at.strftime("%Y%m%d-%H%M%S")
    short_hash = content_hash[:8]
    return f"SWING-{ts}-{short_hash}"


INDEX_SCHEMA_VERSION = "r4a_swing_review_history_v1"
MAX_ZIP_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_PROMPT_BYTES = 5 * 1024 * 1024  # 5 MB (generous, individual file limit)
DEFAULT_RETENTION_DAYS = 90
DEFAULT_MAX_REVIEWS = 250
LOCK_TIMEOUT = 15  # seconds
DIR_PERMISSIONS = 0o700
FILE_PERMISSIONS = 0o600

# Metadata keys that are always serialised in the index entry
INDEX_ENTRY_KEYS = [
    "review_id",
    "generated_at_utc",
    "data_loaded_at_utc",
    "window_start_utc",
    "window_end_utc",
    "window_start_colombia",
    "window_end_colombia",
    "strategy",
    "selected_fingerprint",
    "fingerprint_scope",
    "signal_count",
    "closed_count",
    "experimental_count",
    "quality_level",
    "quality_reasons",
    "readiness_decision",
    "prompt_status",
    "complete_for_copilot",
    "zip_sha256",
    "prompt_sha256",
    "zip_size_bytes",
    "prompt_size_bytes",
    "content_hash",
    "source_commit",
    "supersedes_review_id",
    "retained_until",
]


# ---------------------------------------------------------------------------
# Environment flag
# ---------------------------------------------------------------------------
def _history_enabled() -> bool:
    """Check SWING_HISTORY_ENABLED env var. Defaults to enabled."""
    val = os.environ.get("SWING_HISTORY_ENABLED", "true").strip().lower()
    return val not in ("0", "false", "no", "off", "disabled")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _validate_review_id(review_id: str) -> None:
    """Raise ValueError if review_id does not match the strict pattern."""
    if not isinstance(review_id, str) or not REVIEW_ID_REGEX.match(review_id):
        raise ValueError(
            f"Invalid review_id: {review_id!r}. "
            f"Must match pattern SWING-YYYYMMDD-HHMMSS[-hash8]."
        )


def _resolve_safe_path(base_dir: str, *segments: str) -> str:
    """Resolve a path under base_dir, preventing traversal escapes.

    Returns the normalised, absolute path.  Raises ValueError if the resolved
    path lands outside *base_dir*.
    """
    base = os.path.realpath(os.path.abspath(base_dir))
    target = os.path.realpath(os.path.abspath(os.path.join(base, *segments)))
    # On Windows, normalise case for comparison
    if os.path.normcase(target) != os.path.normcase(base) and not os.path.normcase(
        target
    ).startswith(os.path.normcase(base) + os.sep):
        raise ValueError(
            f"Path traversal detected: {target!r} is not under {base!r}"
        )
    return target


def _compute_sha256_bytes(data: bytes) -> str:
    """Return hex digest of SHA-256 for a bytes payload."""
    return hashlib.sha256(data).hexdigest()


def _compute_sha256_file(path: str) -> str:
    """Return hex digest of SHA-256 for a file on disk."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _iso_now() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(val: Any) -> Optional[datetime]:
    """Parse an ISO-8601 string to a timezone-aware datetime, or return None."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    try:
        dt = datetime.fromisoformat(str(val))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# File-based cross-process lock
# ---------------------------------------------------------------------------
class _FileLock:
    """Simple cross-process lock using an exclusive-create file.

    Combines an in-process threading.Lock with a filesystem-level lock so both
    multi-threaded and multi-process scenarios are safe on the same machine.
    """

    def __init__(self, lock_path: str, timeout: float = LOCK_TIMEOUT):
        self._lock_path = lock_path
        self._timeout = timeout
        self._local = threading.Lock()
        self._fd: Optional[int] = None

    def acquire(self) -> None:
        self._local.acquire()
        deadline = time.monotonic() + self._timeout
        while True:
            try:
                self._fd = os.open(
                    self._lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600
                )
                return
            except FileExistsError:
                if time.monotonic() > deadline:
                    self._local.release()
                    raise TimeoutError(
                        f"Could not acquire lock on {self._lock_path} "
                        f"within {self._timeout}s"
                    )
                time.sleep(0.05)

    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
            try:
                os.unlink(self._lock_path)
            except OSError:
                pass
        try:
            self._local.release()
        except RuntimeError:
            pass  # already released

    def __enter__(self) -> _FileLock:
        self.acquire()
        return self

    def __exit__(self, *args: Any) -> None:
        self.release()


# ---------------------------------------------------------------------------
# ReviewHistoryManager
# ---------------------------------------------------------------------------
class ReviewHistoryManager:
    """Local, file-system-backed store for SWING review ZIPs and prompts.

    All persistence happens under *base_dir* (default: ``data/swing_reviews/``).
    The index file lives alongside *base_dir* at ``data/swing_review_index.json``.
    """

    def __init__(
        self,
        base_dir: str = "data/swing_reviews",
        retention_days: int = DEFAULT_RETENTION_DAYS,
        max_reviews: int = DEFAULT_MAX_REVIEWS,
    ) -> None:
        self._base_dir = os.path.realpath(os.path.abspath(base_dir))
        self._index_path = os.path.join(
            os.path.dirname(self._base_dir), "swing_review_index.json"
        )
        self._lock_path = self._index_path + ".lock"
        self._retention_days = retention_days
        self._max_reviews = max_reviews

        # Ensure the base directory exists
        os.makedirs(self._base_dir, mode=DIR_PERMISSIONS, exist_ok=True)
        # Attempt to set permissions (best-effort on Windows)
        try:
            os.chmod(self._base_dir, DIR_PERMISSIONS)
        except OSError:
            pass

    # -------------------------------------------------------------------
    # Index I/O
    # -------------------------------------------------------------------
    def _load_index(self) -> dict:
        """Load the review index from disk.  Returns a fresh empty structure
        if the file does not exist.

        On corruption the existing file is renamed to ``.corrupted_<ts>`` and a
        fresh index is returned (fail-closed — never overwrites corrupt data).
        """
        if not os.path.isfile(self._index_path):
            return {"schema_version": INDEX_SCHEMA_VERSION, "reviews": []}

        try:
            with open(self._index_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError):
            self._archive_corrupt_index()
            return {"schema_version": INDEX_SCHEMA_VERSION, "reviews": []}

        if not isinstance(data, dict) or "reviews" not in data:
            self._archive_corrupt_index()
            return {"schema_version": INDEX_SCHEMA_VERSION, "reviews": []}

        # Schema migration placeholder — accept known versions
        sv = data.get("schema_version", "unknown")
        if sv != INDEX_SCHEMA_VERSION:
            # Future: add migration logic here
            pass

        # Ensure reviews is a list
        if not isinstance(data.get("reviews"), list):
            self._archive_corrupt_index()
            return {"schema_version": INDEX_SCHEMA_VERSION, "reviews": []}

        return data

    def _archive_corrupt_index(self) -> None:
        """Rename a corrupted index file out of the way so it is never
        silently overwritten."""
        if not os.path.isfile(self._index_path):
            return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        corrupt_path = f"{self._index_path}.corrupted_{ts}"
        try:
            os.rename(self._index_path, corrupt_path)
        except OSError:
            pass  # best-effort

    def _atomic_write_index(self, index_data: dict) -> None:
        """Write *index_data* to the index file atomically (temp + rename)."""
        tmp_fd, tmp_path = tempfile.mkstemp(
            suffix=".json",
            prefix=".idx_tmp_",
            dir=os.path.dirname(self._index_path),
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                json.dump(index_data, fh, ensure_ascii=False, indent=2, sort_keys=True, default=str)
            os.chmod(tmp_path, FILE_PERMISSIONS)
            os.replace(tmp_path, self._index_path)
        except Exception:
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    # -------------------------------------------------------------------
    # Core operations
    # -------------------------------------------------------------------
    def persist_review(
        self,
        review_id: str,
        zip_bytes: bytes,
        prompt_bytes: bytes,
        metadata: dict,
        supersedes_review_id: Optional[str] = None,
    ) -> dict:
        """Persist a review (ZIP + prompt) to the history store.

        Parameters
        ----------
        review_id:
            Unique identifier matching ``SWING-YYYYMMDD-HHMM``.
        zip_bytes:
            The deterministic R3B ZIP payload.
        prompt_bytes:
            The Copilot prompt Markdown payload.
        metadata:
            Dict with at least: generated_at_utc, selected_fingerprint,
            fingerprint_scope, signal_count, quality_level, readiness_decision,
            complete_for_copilot, data_loaded_at_utc, window_* fields, strategy,
            source_commit, closed_count, experimental_count, quality_reasons,
            prompt_status, closed_count.
        supersedes_review_id:
            If this review supersedes a previous one, reference its id here.

        Returns
        -------
        dict
            The full index entry that was persisted.

        Raises
        ------
        ValueError
            If inputs are invalid, a duplicate is detected, or history is disabled.
        """
        if not _history_enabled():
            raise ValueError("SWING_HISTORY_ENABLED is disabled — cannot persist.")

        _validate_review_id(review_id)

        if not isinstance(zip_bytes, bytes) or len(zip_bytes) == 0:
            raise ValueError("zip_bytes must be non-empty bytes")
        if not isinstance(prompt_bytes, bytes) or len(prompt_bytes) == 0:
            raise ValueError("prompt_bytes must be non-empty bytes")
        if len(zip_bytes) > MAX_ZIP_BYTES:
            raise ValueError(
                f"ZIP size {len(zip_bytes)} exceeds limit {MAX_ZIP_BYTES}"
            )
        if len(prompt_bytes) > MAX_PROMPT_BYTES:
            raise ValueError(
                f"Prompt size {len(prompt_bytes)} exceeds limit {MAX_PROMPT_BYTES}"
            )

        if supersedes_review_id is not None:
            _validate_review_id(supersedes_review_id)

        # Compute content hash for dedup
        content_hash = _compute_sha256_bytes(zip_bytes)
        zip_sha256 = content_hash
        prompt_sha256 = _compute_sha256_bytes(prompt_bytes)

        # Check duplicate by content hash under lock
        with _FileLock(self._lock_path):
            index = self._load_index()
            for entry in index["reviews"]:
                if entry.get("content_hash") == content_hash:
                    raise ValueError(
                        f"Duplicate review detected (content hash {content_hash[:12]}…). "
                        f"Existing review_id: {entry.get('review_id')}. "
                        "Content-identical review already persisted."
                    )

            # Build target dir
            review_dir = _resolve_safe_path(self._base_dir, review_id)

            if os.path.exists(review_dir):
                raise ValueError(
                    f"Review directory already exists: {review_id}. "
                    "Delete the existing review first or use a different review_id."
                )

            # Build the index entry
            gen_at = metadata.get("generated_at_utc", _iso_now())
            retained_until_dt = datetime.now(timezone.utc) + timedelta(
                days=self._retention_days
            )
            if isinstance(gen_at, datetime):
                retained_until_dt = gen_at + timedelta(days=self._retention_days)
            else:
                parsed = _parse_iso(gen_at)
                if parsed:
                    retained_until_dt = parsed + timedelta(days=self._retention_days)

            entry: dict[str, Any] = {
                "review_id": review_id,
                "content_hash": content_hash,
                "zip_sha256": zip_sha256,
                "prompt_sha256": prompt_sha256,
                "zip_size_bytes": len(zip_bytes),
                "prompt_size_bytes": len(prompt_bytes),
                "supersedes_review_id": supersedes_review_id,
                "retained_until": retained_until_dt.isoformat(),
            }
            # Copy known metadata keys
            for key in INDEX_ENTRY_KEYS:
                if key in metadata and key not in entry:
                    entry[key] = metadata[key]
            # Ensure required fields have defaults
            entry.setdefault("generated_at_utc", _iso_now())
            entry.setdefault("selected_fingerprint", "unknown")
            entry.setdefault("fingerprint_scope", "unknown")
            entry.setdefault("signal_count", 0)
            entry.setdefault("closed_count", 0)
            entry.setdefault("experimental_count", 0)
            entry.setdefault("quality_level", "UNKNOWN")
            entry.setdefault("quality_reasons", [])
            entry.setdefault("readiness_decision", "UNKNOWN")
            entry.setdefault("prompt_status", "UNKNOWN")
            entry.setdefault("complete_for_copilot", False)
            entry.setdefault("data_loaded_at_utc", None)
            entry.setdefault("source_commit", None)

        # ---- transactional write (outside lock for I/O, but after dedup check) ----
        tmp_dir = _resolve_safe_path(
            self._base_dir, f".tmp_{review_id}_{uuid.uuid4().hex[:8]}"
        )
        try:
            os.makedirs(tmp_dir, mode=DIR_PERMISSIONS, exist_ok=False)

            zip_path = os.path.join(tmp_dir, f"SWING_REVIEW_PACK_R3B_{review_id}.zip")
            prompt_path = os.path.join(
                tmp_dir, f"10_prompt_for_copilot_{review_id}.md"
            )

            # Write files
            with open(zip_path, "wb") as fh:
                fh.write(zip_bytes)
            os.chmod(zip_path, FILE_PERMISSIONS)

            with open(prompt_path, "wb") as fh:
                fh.write(prompt_bytes)
            os.chmod(prompt_path, FILE_PERMISSIONS)

            # Verify hashes
            actual_zip_hash = _compute_sha256_file(zip_path)
            if actual_zip_hash != zip_sha256:
                raise ValueError(
                    f"ZIP hash mismatch on disk: expected {zip_sha256[:12]}…, "
                    f"got {actual_zip_hash[:12]}…"
                )
            actual_prompt_hash = _compute_sha256_file(prompt_path)
            if actual_prompt_hash != prompt_sha256:
                raise ValueError(
                    f"Prompt hash mismatch on disk: expected {prompt_sha256[:12]}…, "
                    f"got {actual_prompt_hash[:12]}…"
                )

            # Atomic rename of temp dir → final dir
            os.rename(tmp_dir, review_dir)
            try:
                os.chmod(review_dir, DIR_PERMISSIONS)
            except OSError:
                pass
        except Exception:
            # Clean up temp dir on any failure
            if os.path.exists(tmp_dir):
                shutil.rmtree(tmp_dir, ignore_errors=True)
            raise

        # ---- update index atomically ----
        with _FileLock(self._lock_path):
            index = self._load_index()

            # Double-check duplicate (race condition guard)
            for e in index["reviews"]:
                if e.get("content_hash") == content_hash:
                    # Already persisted by another process — clean up
                    if os.path.exists(review_dir):
                        shutil.rmtree(review_dir, ignore_errors=True)
                    raise ValueError(
                        f"Duplicate review detected after write "
                        f"(content hash {content_hash[:12]}…)."
                    )

            index["reviews"].append(entry)
            index["reviews"].sort(
                key=lambda e: e.get("generated_at_utc", ""), reverse=True
            )
            self._atomic_write_index(index)

        # Run retention (best-effort, outside lock to avoid holding it long)
        try:
            self.cleanup_expired()
            self.cleanup_fifo()
        except Exception:
            pass

        return dict(entry)

    def get_review(self, review_id: str) -> Optional[tuple[bytes, bytes, dict]]:
        """Retrieve ZIP bytes, prompt bytes, and metadata for a review.

        Returns ``None`` if the review does not exist.
        Verifies SHA-256 integrity on read — raises ``ValueError`` if files are
        corrupted.
        """
        _validate_review_id(review_id)
        review_dir = _resolve_safe_path(self._base_dir, review_id)

        if not os.path.isdir(review_dir):
            return None

        with _FileLock(self._lock_path):
            index = self._load_index()
            entry = None
            for e in index["reviews"]:
                if e.get("review_id") == review_id:
                    entry = e
                    break

        if entry is None:
            return None

        zip_path = os.path.join(review_dir, f"SWING_REVIEW_PACK_R3B_{review_id}.zip")
        prompt_path = os.path.join(
            review_dir, f"10_prompt_for_copilot_{review_id}.md"
        )

        if not os.path.isfile(zip_path) or not os.path.isfile(prompt_path):
            return None

        # Verify integrity
        actual_zip_hash = _compute_sha256_file(zip_path)
        expected_zip_hash = entry.get("zip_sha256", "")
        if actual_zip_hash != expected_zip_hash:
            raise ValueError(
                f"ZIP integrity check failed for {review_id}: "
                f"expected {expected_zip_hash[:12]}…, got {actual_zip_hash[:12]}…"
            )

        actual_prompt_hash = _compute_sha256_file(prompt_path)
        expected_prompt_hash = entry.get("prompt_sha256", "")
        if actual_prompt_hash != expected_prompt_hash:
            raise ValueError(
                f"Prompt integrity check failed for {review_id}: "
                f"expected {expected_prompt_hash[:12]}…, got {actual_prompt_hash[:12]}…"
            )

        with open(zip_path, "rb") as fh:
            zip_bytes = fh.read()
        with open(prompt_path, "r", encoding="utf-8") as fh:
            prompt_bytes = fh.read().encode("utf-8")

        return zip_bytes, prompt_bytes, dict(entry)

    def list_reviews(self) -> list[dict]:
        """Return all review metadata entries, sorted by generated_at_utc
        descending (newest first)."""
        if not _history_enabled():
            # Read still works even when disabled
            pass
        with _FileLock(self._lock_path):
            index = self._load_index()
        return list(index.get("reviews", []))

    def delete_review(self, review_id: str) -> bool:
        """Delete a review from disk and the index.

        Idempotent — returns ``True`` if a review was actually deleted,
        ``False`` if it did not exist.
        """
        if not _history_enabled():
            raise ValueError("SWING_HISTORY_ENABLED is disabled — cannot delete.")

        _validate_review_id(review_id)
        review_dir = _resolve_safe_path(self._base_dir, review_id)

        deleted = False

        # Remove directory
        if os.path.isdir(review_dir):
            shutil.rmtree(review_dir, ignore_errors=True)
            deleted = True

        # Remove from index
        with _FileLock(self._lock_path):
            index = self._load_index()
            before = len(index["reviews"])
            index["reviews"] = [
                e for e in index["reviews"] if e.get("review_id") != review_id
            ]
            if len(index["reviews"]) < before:
                deleted = True
                self._atomic_write_index(index)

        return deleted

    def is_duplicate(self, content_hash: str) -> bool:
        """Check whether a review with the given *content_hash* already exists."""
        with _FileLock(self._lock_path):
            index = self._load_index()
        for e in index["reviews"]:
            if e.get("content_hash") == content_hash:
                return True
        return False

    def is_index_valid(self) -> bool:
        """Check whether the on-disk index file exists and is valid JSON.

        Returns ``False`` if the index is corrupt, missing, or was recovered
        via ``_archive_corrupt_index``.
        """
        if not os.path.isfile(self._index_path):
            return True  # no file → not corrupt, just empty
        try:
            with open(self._index_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return isinstance(data, dict) and isinstance(data.get("reviews"), list)
        except (json.JSONDecodeError, OSError):
            return False

    def verify_integrity(self, review_id: str) -> bool:
        """Check on-disk SHA-256 hashes against index entries. Returns True if
        the review files are intact."""
        _validate_review_id(review_id)
        review_dir = _resolve_safe_path(self._base_dir, review_id)
        if not os.path.isdir(review_dir):
            return False

        with _FileLock(self._lock_path):
            index = self._load_index()
        entry = None
        for e in index["reviews"]:
            if e.get("review_id") == review_id:
                entry = e
                break
        if entry is None:
            return False

        zip_path = os.path.join(review_dir, f"SWING_REVIEW_PACK_R3B_{review_id}.zip")
        prompt_path = os.path.join(
            review_dir, f"10_prompt_for_copilot_{review_id}.md"
        )
        if not os.path.isfile(zip_path) or not os.path.isfile(prompt_path):
            return False

        return (
            _compute_sha256_file(zip_path) == entry.get("zip_sha256", "")
            and _compute_sha256_file(prompt_path) == entry.get("prompt_sha256", "")
        )

    # -------------------------------------------------------------------
    # Retention / cleanup
    # -------------------------------------------------------------------
    def cleanup_expired(self) -> int:
        """Delete reviews whose ``retained_until`` is in the past.

        Returns the number of reviews removed.
        """
        if not _history_enabled():
            return 0

        now = datetime.now(timezone.utc)
        removed = 0

        with _FileLock(self._lock_path):
            index = self._load_index()
            expired_ids = []
            for e in index["reviews"]:
                ru = _parse_iso(e.get("retained_until"))
                if ru is not None and ru < now:
                    expired_ids.append(e["review_id"])

            for rid in expired_ids:
                review_dir = _resolve_safe_path(self._base_dir, rid)
                if os.path.isdir(review_dir):
                    shutil.rmtree(review_dir, ignore_errors=True)
                removed += 1

            index["reviews"] = [
                e for e in index["reviews"] if e["review_id"] not in expired_ids
            ]
            self._atomic_write_index(index)

        return removed

    def cleanup_fifo(self) -> int:
        """If the number of reviews exceeds *max_reviews*, delete the oldest
        (by ``generated_at_utc`` ascending).

        Returns the number of reviews removed.
        """
        if not _history_enabled():
            return 0

        removed = 0

        with _FileLock(self._lock_path):
            index = self._load_index()
            if len(index["reviews"]) <= self._max_reviews:
                return 0

            # Already sorted descending; oldest are at the end
            overflow = len(index["reviews"]) - self._max_reviews
            oldest = index["reviews"][-overflow:]

            for e in oldest:
                review_dir = _resolve_safe_path(self._base_dir, e["review_id"])
                if os.path.isdir(review_dir):
                    shutil.rmtree(review_dir, ignore_errors=True)
                removed += 1

            index["reviews"] = index["reviews"][: self._max_reviews]
            self._atomic_write_index(index)

        return removed