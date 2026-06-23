from __future__ import annotations

import json
from typing import Any, Iterable, Optional


def parse_json_safe(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    text = str(value).strip()
    if not text:
        return default
    try:
        return json.loads(text)
    except Exception:
        return default


def get_path(data: Any, path: str, default: Any = None) -> Any:
    if data is None or not path:
        return default
    current = data
    for part in str(path).split("."):
        if isinstance(current, dict):
            if part not in current:
                return default
            current = current.get(part)
        elif isinstance(current, list):
            if part == "[]":
                current = current
            else:
                try:
                    idx = int(part)
                    current = current[idx]
                except Exception:
                    return default
        else:
            return default
    return current


def first_path(data: Any, paths: Iterable[str], default: Any = None) -> Any:
    for path in paths:
        value = get_path(data, path, default=None)
        if value is not None:
            return value
    return default


def as_bool(value: Any, default: Optional[bool] = None) -> Optional[bool]:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def as_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except Exception:
        return default


def as_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    if value is None or value == "":
        return default
    try:
        return int(float(value))
    except Exception:
        return default


def compact_text(value: Any, limit: int = 500) -> str | None:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, default=str)
    else:
        text = str(value)
    if len(text) > limit:
        return text[:limit] + "...<truncated>"
    return text


def extract_json_fields(payload: Any, paths_by_name: dict[str, list[str]]) -> dict[str, Any]:
    parsed = parse_json_safe(payload, default={})
    out: dict[str, Any] = {}
    for name, paths in paths_by_name.items():
        out[name] = first_path(parsed, paths, default=None)
    return out
