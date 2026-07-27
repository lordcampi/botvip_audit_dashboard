from __future__ import annotations

"""
swing_telegram_sender.py — R5B isolated SWING Telegram text sender.

Sends sendMessage text-only via stdlib urllib.
No ZIP, no documents, no signals, no orders.
Never imports telegram_delivery.py.
Never touches BotVIP, PostgreSQL, SQLite, or legacy modules.
"""

import json as _json
import os
import ssl
import urllib.request
import urllib.error
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
TELEGRAM_API_URL = "https://api.telegram.org"
REQUEST_TIMEOUT = 15  # seconds
MAX_MESSAGE_LENGTH = 4096  # Telegram limit for sendMessage text

# Error classes
ERR_CONFIG_INVALID = "CONFIG_INVALID"
ERR_RATE_LIMITED = "RATE_LIMITED"
ERR_API_ERROR = "API_ERROR"
ERR_NETWORK_ERROR = "NETWORK_ERROR"
ERR_PARSE_ERROR = "PARSE_ERROR"
ERR_MESSAGE_TOO_LONG = "MESSAGE_TOO_LONG"
ERR_BAD_REQUEST = "BAD_REQUEST"


# ---------------------------------------------------------------------------
# Sanitisation helpers
# ---------------------------------------------------------------------------
def _sanitize_token(token: str) -> str:
    """Return a safe placeholder — never reveal any part of the token."""
    if not token:
        return "***MISSING***"
    return "***TOKEN***"


def _sanitize_chat_id(chat_id: str) -> str:
    """Return a safe placeholder — never reveal the chat_id."""
    if not chat_id:
        return "***MISSING***"
    return "***CHAT_ID***"


def _sanitize_reason(exc: Exception) -> str:
    """Build a safe, structured error string without raw URLs, tokens, or response bodies."""
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}: {exc.reason}"
    if isinstance(exc, urllib.error.URLError):
        return f"Network error: {exc.reason}"
    if isinstance(exc, OSError):
        return f"OS error: {exc}"
    return f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# Sender
# ---------------------------------------------------------------------------
class SwingTelegramSender:
    """Isolated Telegram text sender for SWING review reminders.

    Uses SWING_TELEGRAM_BOT_TOKEN and SWING_TELEGRAM_CHAT_ID exclusively.
    Does NOT read TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID (legacy BotVIP).
    """

    def __init__(self, token: str, chat_id: str) -> None:
        if not token or not token.strip():
            raise ValueError("SWING_TELEGRAM_BOT_TOKEN is required and must not be empty")
        if not chat_id or not chat_id.strip():
            raise ValueError("SWING_TELEGRAM_CHAT_ID is required and must not be empty")

        self._token = token.strip()
        self._chat_id = chat_id.strip()

    @classmethod
    def from_env(cls) -> SwingTelegramSender:
        """Create a sender from SWING_TELEGRAM_* environment variables.

        Never reads TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID (legacy).
        Raises ValueError if either variable is missing or empty.
        """
        token = os.environ.get("SWING_TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.environ.get("SWING_TELEGRAM_CHAT_ID", "").strip()

        if not token:
            raise ValueError(
                "SWING_TELEGRAM_BOT_TOKEN is missing or empty. "
                "Set it in .env or the environment."
            )
        if not chat_id:
            raise ValueError(
                "SWING_TELEGRAM_CHAT_ID is missing or empty. "
                "Set it in .env or the environment."
            )
        return cls(token=token, chat_id=chat_id)

    # -------------------------------------------------------------------
    # send_message
    # -------------------------------------------------------------------
    def send_message(self, text: str) -> dict[str, Any]:
        """Send a plain-text message via Telegram sendMessage API.

        Parameters
        ----------
        text:
            Message body (up to 4096 characters).

        Returns
        -------
        dict with keys:
            success: bool
            error_class: str | None
            http_status: int | None
            retryable: bool
            retry_after_seconds: int | None
            sanitized_reason: str
        """
        # --- Validate text ---
        if not text:
            return {
                "success": False,
                "error_class": ERR_BAD_REQUEST,
                "http_status": None,
                "retryable": False,
                "retry_after_seconds": None,
                "sanitized_reason": "Message text must not be empty",
            }

        if len(text) > MAX_MESSAGE_LENGTH:
            return {
                "success": False,
                "error_class": ERR_MESSAGE_TOO_LONG,
                "http_status": None,
                "retryable": False,
                "retry_after_seconds": None,
                "sanitized_reason": (
                    f"Message length {len(text)} exceeds Telegram limit "
                    f"of {MAX_MESSAGE_LENGTH} characters"
                ),
            }

        # --- Build request ---
        url = f"{TELEGRAM_API_URL}/bot{self._token}/sendMessage"

        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "",  # plain text, no markdown parsing
            "disable_web_page_preview": True,
        }
        data = urllib.parse.urlencode(payload).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"},
        )

        # --- Send ---
        try:
            context = ssl.create_default_context()
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT, context=context) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            return self._handle_http_error(exc)
        except urllib.error.URLError as exc:
            return {
                "success": False,
                "error_class": ERR_NETWORK_ERROR,
                "http_status": None,
                "retryable": True,
                "retry_after_seconds": None,
                "sanitized_reason": _sanitize_reason(exc),
            }
        except OSError as exc:
            return {
                "success": False,
                "error_class": ERR_NETWORK_ERROR,
                "http_status": None,
                "retryable": True,
                "retry_after_seconds": None,
                "sanitized_reason": _sanitize_reason(exc),
            }

        # --- Parse response ---
        try:
            payload = _json.loads(raw)
        except (_json.JSONDecodeError, TypeError):
            return {
                "success": False,
                "error_class": ERR_PARSE_ERROR,
                "http_status": None,
                "retryable": True,
                "retry_after_seconds": None,
                "sanitized_reason": "Telegram API returned non-JSON response",
            }

        if not isinstance(payload, dict):
            return {
                "success": False,
                "error_class": ERR_PARSE_ERROR,
                "http_status": None,
                "retryable": True,
                "retry_after_seconds": None,
                "sanitized_reason": "Telegram API returned unexpected response format",
            }

        ok = payload.get("ok", False)
        if ok:
            return {
                "success": True,
                "error_class": None,
                "http_status": 200,
                "retryable": False,
                "retry_after_seconds": None,
                "sanitized_reason": "",
            }

        # ok=False — extract safe error info
        error_code = payload.get("error_code")
        description = str(payload.get("description", "Unknown Telegram error"))
        # Sanitize description: never include token, chat_id, or URLs
        description_safe = _sanitize_description(description)

        return {
            "success": False,
            "error_class": ERR_API_ERROR,
            "http_status": 200,
            "retryable": False,
            "retry_after_seconds": None,
            "sanitized_reason": f"Telegram API error {error_code}: {description_safe}",
        }

    # -------------------------------------------------------------------
    # HTTP error handler
    # -------------------------------------------------------------------
    def _handle_http_error(self, exc: urllib.error.HTTPError) -> dict[str, Any]:
        """Classify HTTP errors into retryable/non-retryable categories.

        Never includes the token, chat_id, or full URL in the result.
        """
        code = exc.code
        reason_safe = _sanitize_reason(exc)

        # 400 Bad Request — likely our payload issue
        if code == 400:
            return {
                "success": False,
                "error_class": ERR_BAD_REQUEST,
                "http_status": code,
                "retryable": False,
                "retry_after_seconds": None,
                "sanitized_reason": reason_safe,
            }

        # 401 Unauthorized / 403 Forbidden — invalid token/chat_id
        if code in (401, 403):
            return {
                "success": False,
                "error_class": ERR_CONFIG_INVALID,
                "http_status": code,
                "retryable": False,
                "retry_after_seconds": None,
                "sanitized_reason": reason_safe,
            }

        # 429 Too Many Requests — rate limited
        if code == 429:
            retry_after = _extract_retry_after(exc)
            return {
                "success": False,
                "error_class": ERR_RATE_LIMITED,
                "http_status": code,
                "retryable": True,
                "retry_after_seconds": retry_after,
                "sanitized_reason": reason_safe,
            }

        # 5xx — server error, transient
        if 500 <= code < 600:
            return {
                "success": False,
                "error_class": ERR_API_ERROR,
                "http_status": code,
                "retryable": True,
                "retry_after_seconds": None,
                "sanitized_reason": reason_safe,
            }

        # Unknown HTTP error
        return {
            "success": False,
            "error_class": ERR_API_ERROR,
            "http_status": code,
            "retryable": False,
            "retry_after_seconds": None,
            "sanitized_reason": reason_safe,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _extract_retry_after(exc: urllib.error.HTTPError) -> Optional[int]:
    """Extract Retry-After header from an HTTPError, if present."""
    headers = getattr(exc, "headers", {}) or {}
    retry_after = headers.get("Retry-After", headers.get("retry-after"))
    if retry_after is not None:
        try:
            return int(retry_after)
        except (ValueError, TypeError):
            return None
    return None


def _sanitize_description(description: str) -> str:
    """Sanitize a Telegram API error description.

    Removes anything that looks like a token, chat_id, or URL fragment.
    """
    if not description:
        return "Unknown Telegram error"

    # Truncate to safe length
    if len(description) > 200:
        description = description[:200] + "…"

    return description