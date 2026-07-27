from __future__ import annotations

"""
swing_reminder_state.py — R5A local state for SWING reminder cooldown/dedup.

Persists reminder state under data/swing_reminder_state.json.
Follows the same atomic-write + file-lock pattern as swing_review_history.py.
Never writes to PostgreSQL.  Corruption → fail-closed (raise, don't silently default).
"""

import json
import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
STATE_SCHEMA_VERSION = "r5a_swing_reminder_v1"
LOCK_TIMEOUT = 15  # seconds
FILE_PERMISSIONS = 0o600
DIR_PERMISSIONS = 0o700

# Valid reminder types
VALID_REMINDER_TYPES = ("TECH", "STRAT", "CALIB")

# Default entry for a reminder type that has never been triggered
_DEFAULT_TYPE_ENTRY: dict[str, Any] = {
    "last_shadow_evaluated_at_utc": None,
    "last_shadow_candidate_hash": None,
    "last_sent_at_utc": None,
    "last_sent_message_hash": None,
    "last_known_signal_count": 0,
    "last_known_closed_count": 0,
    "last_known_latest_review_id": None,
}

# Keys allowed at the top level of the state file
_KNOWN_TOP_KEYS = {"schema_version", "reminders", "last_check_at_utc", "telegram_errors_since_last_ok", "telegram_last_error_at_utc", "telegram_last_error_message"}


# ---------------------------------------------------------------------------
# File-based cross-process lock (same pattern as R4)
# ---------------------------------------------------------------------------
class _FileLock:
    """Simple cross-process lock using an exclusive-create file."""

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
            pass

    def __enter__(self) -> _FileLock:
        self.acquire()
        return self

    def __exit__(self, *args: Any) -> None:
        self.release()


# ---------------------------------------------------------------------------
# State manager
# ---------------------------------------------------------------------------
class SwingReminderState:
    """Local, file-system-backed state store for SWING reminder engine.

    All persistence happens under a single JSON file:
    ``data/swing_reminder_state.json``.
    """

    def __init__(self, state_path: str = "data/swing_reminder_state.json") -> None:
        self._state_path = os.path.realpath(os.path.abspath(state_path))
        self._lock_path = self._state_path + ".lock"

        # Ensure parent directory exists
        parent = os.path.dirname(self._state_path)
        if parent:
            os.makedirs(parent, mode=DIR_PERMISSIONS, exist_ok=True)
            try:
                os.chmod(parent, DIR_PERMISSIONS)
            except OSError:
                pass

    # -------------------------------------------------------------------
    # Load / save
    # -------------------------------------------------------------------
    def _load_state(self) -> dict:
        """Load the reminder state from disk.

        Returns a fresh empty structure if the file does not exist.
        On corruption: archives the corrupt file and raises RuntimeError
        (fail-closed — never silently returns defaults for mutable operations).
        """
        if not os.path.isfile(self._state_path):
            return self._build_default_state()

        try:
            with open(self._state_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError):
            self._archive_corrupt_state()
            raise RuntimeError(
                f"Reminder state file is corrupt: {self._state_path}. "
                "The corrupt file has been archived. A fresh state must be "
                "initialised explicitly before mutable operations can proceed."
            )

        if not isinstance(data, dict):
            self._archive_corrupt_state()
            raise RuntimeError(
                f"Reminder state file has invalid structure: {self._state_path}."
            )

        # Validate schema version and structure
        sv = data.get("schema_version")
        if sv != STATE_SCHEMA_VERSION:
            # Future: add migration logic here
            pass

        if not isinstance(data.get("reminders"), dict):
            self._archive_corrupt_state()
            raise RuntimeError(
                f"Reminder state file is corrupt (missing 'reminders' dict): "
                f"{self._state_path}."
            )

        return data

    def _build_default_state(self) -> dict:
        """Return a fresh default state structure."""
        reminders = {t: dict(_DEFAULT_TYPE_ENTRY) for t in VALID_REMINDER_TYPES}
        return {
            "schema_version": STATE_SCHEMA_VERSION,
            "reminders": reminders,
            "last_check_at_utc": None,
            "telegram_errors_since_last_ok": 0,
            "telegram_last_error_at_utc": None,
            "telegram_last_error_message": None,
        }

    def _archive_corrupt_state(self) -> None:
        """Rename a corrupted state file out of the way."""
        if not os.path.isfile(self._state_path):
            return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        corrupt_path = f"{self._state_path}.corrupted_{ts}"
        try:
            os.rename(self._state_path, corrupt_path)
        except OSError:
            pass

    def _atomic_write_state(self, data: dict) -> None:
        """Write state data to the state file atomically (temp + rename)."""
        tmp_fd, tmp_path = tempfile.mkstemp(
            suffix=".json",
            prefix=".rst_tmp_",
            dir=os.path.dirname(self._state_path) or ".",
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True, default=str)
            os.chmod(tmp_path, FILE_PERMISSIONS)
            os.replace(tmp_path, self._state_path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    # -------------------------------------------------------------------
    # Initialisation (for first-time setup, explicit)
    # -------------------------------------------------------------------
    def initialise_if_missing(self) -> bool:
        """Create a fresh state file if one does not exist.

        Returns True if a new file was created, False if it already existed.
        Safe to call multiple times — idempotent.
        """
        if os.path.isfile(self._state_path):
            return False
        with _FileLock(self._lock_path):
            # Double-check after acquiring lock
            if os.path.isfile(self._state_path):
                return False
            self._atomic_write_state(self._build_default_state())
        return True

    # -------------------------------------------------------------------
    # Read operations (always safe, even when corrupt)
    # -------------------------------------------------------------------
    def get_reminder_entry(self, reminder_type: str) -> dict:
        """Return the state entry for a reminder type.

        If the file does not exist, returns defaults (read-only, no mutation).
        If the file is corrupt, raises RuntimeError.
        """
        if reminder_type not in VALID_REMINDER_TYPES:
            raise ValueError(
                f"Invalid reminder_type: {reminder_type!r}. "
                f"Must be one of {VALID_REMINDER_TYPES}."
            )

        if not os.path.isfile(self._state_path):
            return dict(_DEFAULT_TYPE_ENTRY)

        with _FileLock(self._lock_path):
            data = self._load_state()
        return dict(data["reminders"].get(reminder_type, _DEFAULT_TYPE_ENTRY))

    def get_all_state(self) -> dict:
        """Return the entire state dict (read-only snapshot)."""
        if not os.path.isfile(self._state_path):
            return self._build_default_state()
        with _FileLock(self._lock_path):
            return dict(self._load_state())

    # -------------------------------------------------------------------
    # Write operations (require valid, non-corrupt state)
    # -------------------------------------------------------------------
    def update_reminder_entry(
        self,
        reminder_type: str,
        updates: dict[str, Any],
    ) -> None:
        """Update fields for a reminder type.

        Only known keys are updated; unknown keys are silently ignored.
        Raises RuntimeError if the state file is corrupt.
        """
        if reminder_type not in VALID_REMINDER_TYPES:
            raise ValueError(
                f"Invalid reminder_type: {reminder_type!r}."
            )

        # Filter to known keys only
        safe_updates = {k: v for k, v in updates.items() if k in _DEFAULT_TYPE_ENTRY}

        with _FileLock(self._lock_path):
            data = self._load_state()  # will raise if corrupt
            entry = data["reminders"].get(reminder_type)
            if entry is None:
                entry = dict(_DEFAULT_TYPE_ENTRY)
                data["reminders"][reminder_type] = entry
            entry.update(safe_updates)
            self._atomic_write_state(data)

    def record_shadow_evaluation(
        self,
        reminder_type: str,
        candidate_hash: str,
        signal_count: int,
        closed_count: int,
        latest_review_id: Optional[str] = None,
    ) -> None:
        """Record a shadow evaluation (no actual send).

        Updates:
          - last_shadow_evaluated_at_utc
          - last_shadow_candidate_hash
          - last_known_signal_count
          - last_known_closed_count
          - last_known_latest_review_id

        Does NOT touch last_sent_* fields.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        updates = {
            "last_shadow_evaluated_at_utc": now_iso,
            "last_shadow_candidate_hash": candidate_hash,
            "last_known_signal_count": signal_count,
            "last_known_closed_count": closed_count,
        }
        if latest_review_id is not None:
            updates["last_known_latest_review_id"] = latest_review_id

        self.update_reminder_entry(reminder_type, updates)

    def record_check(self) -> None:
        """Update last_check_at_utc to now."""
        with _FileLock(self._lock_path):
            data = self._load_state()
            data["last_check_at_utc"] = datetime.now(timezone.utc).isoformat()
            self._atomic_write_state(data)

    def record_telegram_error(self, error_message: str) -> None:
        """Increment the error counter and record the last error."""
        with _FileLock(self._lock_path):
            data = self._load_state()
            data["telegram_errors_since_last_ok"] = data.get("telegram_errors_since_last_ok", 0) + 1
            data["telegram_last_error_at_utc"] = datetime.now(timezone.utc).isoformat()
            # Sanitize: truncate error message to 500 chars
            data["telegram_last_error_message"] = str(error_message)[:500]
            self._atomic_write_state(data)

    def reset_telegram_error_counter(self) -> None:
        """Reset the error counter after a successful operation."""
        with _FileLock(self._lock_path):
            data = self._load_state()
            data["telegram_errors_since_last_ok"] = 0
            data["telegram_last_error_at_utc"] = None
            data["telegram_last_error_message"] = None
            self._atomic_write_state(data)

    # -------------------------------------------------------------------
    # Integrity checks
    # -------------------------------------------------------------------
    def is_valid(self) -> bool:
        """Check whether the on-disk state file exists and is valid JSON."""
        if not os.path.isfile(self._state_path):
            return True  # no file → not corrupt, just empty
        try:
            with open(self._state_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return isinstance(data, dict) and isinstance(data.get("reminders"), dict)
        except (json.JSONDecodeError, OSError):
            return False