from __future__ import annotations

import csv
import json
import zipfile
from pathlib import Path
from typing import Any, Iterable


def write_json(payload: Any, path: str | Path) -> Path:
    selected = Path(path)
    selected.parent.mkdir(parents=True, exist_ok=True)
    selected.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return selected


def write_text(text: str, path: str | Path) -> Path:
    selected = Path(path)
    selected.parent.mkdir(parents=True, exist_ok=True)
    selected.write_text(text, encoding="utf-8", newline="\n")
    return selected


def write_rows_csv(rows: list[dict[str, Any]], path: str | Path, fieldnames: Iterable[str] | None = None) -> Path:
    selected = Path(path)
    selected.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        keys: list[str] = []
        for row in rows:
            for key in row.keys():
                if key not in keys:
                    keys.append(key)
        fieldnames = keys
    fields = list(fieldnames)
    with selected.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fields})
    return selected


def create_zip(paths: list[str | Path], zip_path: str | Path, base_dir: str | Path | None = None) -> Path:
    selected = Path(zip_path)
    selected.parent.mkdir(parents=True, exist_ok=True)
    base = Path(base_dir) if base_dir else selected.parent
    with zipfile.ZipFile(selected, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in paths:
            p = Path(path)
            if p.exists() and p.is_file():
                try:
                    arcname = p.relative_to(base)
                except Exception:
                    arcname = p.name
                zf.write(p, arcname=str(arcname))
    return selected
