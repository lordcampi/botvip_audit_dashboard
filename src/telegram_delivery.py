from __future__ import annotations

import json
import mimetypes
import os
import ssl
import uuid
from pathlib import Path
from typing import Any
from urllib import request, error


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def telegram_configured() -> bool:
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"))


def extract_json_object_from_stdout(stdout: str) -> dict[str, Any] | None:
    if not stdout:
        return None
    start = stdout.find("{")
    end = stdout.rfind("}")
    if start < 0 or end <= start:
        return None
    raw = stdout[start : end + 1]
    try:
        value = json.loads(raw)
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def zip_path_from_report_stdout(stdout: str, root: Path) -> Path | None:
    payload = extract_json_object_from_stdout(stdout)
    if not payload:
        return None
    zip_path = payload.get("zip_path")
    if not zip_path:
        return None
    p = Path(str(zip_path))
    if not p.is_absolute():
        p = root / p
    return p


def _multipart_form_data(fields: dict[str, str], file_field: str, file_path: Path) -> tuple[bytes, str]:
    boundary = "----BotVIPBoundary" + uuid.uuid4().hex
    lines: list[bytes] = []
    for name, value in fields.items():
        lines.append(("--" + boundary).encode())
        lines.append(f'Content-Disposition: form-data; name="{name}"'.encode())
        lines.append(b"")
        lines.append(str(value).encode("utf-8"))
    content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
    lines.append(("--" + boundary).encode())
    lines.append(f'Content-Disposition: form-data; name="{file_field}"; filename="{file_path.name}"'.encode())
    lines.append(f"Content-Type: {content_type}".encode())
    lines.append(b"")
    lines.append(file_path.read_bytes())
    lines.append(("--" + boundary + "--").encode())
    lines.append(b"")
    return b"\r\n".join(lines), boundary


def send_document(file_path: str | Path, caption: str = "") -> dict[str, Any]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
    path = Path(file_path)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(str(path))
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    fields = {"chat_id": chat_id}
    if caption:
        fields["caption"] = caption[:1024]
    body, boundary = _multipart_form_data(fields, "document", path)
    req = request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    context = ssl.create_default_context()
    try:
        with request.urlopen(req, timeout=60, context=context) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Telegram HTTP error {exc.code}: {raw}") from exc
    payload = json.loads(raw)
    if not payload.get("ok"):
        raise RuntimeError("Telegram API returned not ok: " + raw)
    return payload


def send_report_zip_if_enabled(root: Path, stdout: str) -> bool:
    if not env_bool("TELEGRAM_SEND_ENABLED", default=False):
        print("Telegram delivery disabled: TELEGRAM_SEND_ENABLED is not true")
        return False
    if not telegram_configured():
        print("Telegram delivery skipped: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing")
        return False
    zip_path = zip_path_from_report_stdout(stdout, root)
    if not zip_path:
        print("Telegram delivery skipped: could not find zip_path in report output")
        return False
    if not zip_path.exists():
        print("Telegram delivery skipped: zip_path does not exist: " + str(zip_path))
        return False
    caption = os.environ.get("TELEGRAM_CAPTION", "BotVIP Daily AI Review Pack")
    send_document(zip_path, caption=caption)
    print("Telegram delivery OK: " + str(zip_path))
    return True
