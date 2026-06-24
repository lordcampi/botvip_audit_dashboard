from __future__ import annotations

import csv
import json
import zipfile
from pathlib import Path

from src.safe_zip_chunking import prepare_zip_files_for_char_limit, validate_zip_input_char_limit


def test_small_json_is_not_split(tmp_path: Path) -> None:
    path = tmp_path / "small.json"
    path.write_text(json.dumps({"ok": True}), encoding="utf-8")
    files = prepare_zip_files_for_char_limit([path], report_dir=tmp_path, max_chars=100000)
    assert files == [path]
    validate_zip_input_char_limit(files, max_chars=100000)


def test_large_json_list_is_split_into_valid_parts(tmp_path: Path) -> None:
    path = tmp_path / "large.json"
    payload = [{"i": i, "text": "x" * 200} for i in range(250)]
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    files = prepare_zip_files_for_char_limit([path], report_dir=tmp_path, max_chars=8000)
    assert path not in files
    assert any(p.name == "large_index.json" for p in files)
    part_files = [p for p in files if "_part" in p.name]
    assert len(part_files) > 1
    for part in part_files:
        data = json.loads(part.read_text(encoding="utf-8"))
        assert data["content_type"] == "json_list_items"
        assert len(part.read_text(encoding="utf-8")) <= 8000
    validate_zip_input_char_limit(files, max_chars=8000)


def test_large_json_dict_list_key_is_split_into_valid_parts(tmp_path: Path) -> None:
    path = tmp_path / "sections.json"
    payload = {"schema_version": "x", "rows": [{"i": i, "text": "y" * 120} for i in range(200)]}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    files = prepare_zip_files_for_char_limit([path], report_dir=tmp_path, max_chars=7000)
    assert path not in files
    for part in [p for p in files if "_part" in p.name]:
        data = json.loads(part.read_text(encoding="utf-8"))
        assert data["content_type"] == "json_dict_list_key"
        assert "rows" in data
        assert len(part.read_text(encoding="utf-8")) <= 7000


def test_large_csv_is_split_with_header(tmp_path: Path) -> None:
    path = tmp_path / "large.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "text"])
        for i in range(200):
            writer.writerow([i, "z" * 120])
    files = prepare_zip_files_for_char_limit([path], report_dir=tmp_path, max_chars=5000)
    assert path not in files
    part_files = [p for p in files if "_part" in p.name]
    assert len(part_files) > 1
    for part in part_files:
        first_line = part.read_text(encoding="utf-8").splitlines()[0]
        assert first_line == "id,text"
        assert len(part.read_text(encoding="utf-8")) <= 5000


def test_large_text_is_split_and_original_excluded(tmp_path: Path) -> None:
    path = tmp_path / "large.txt"
    path.write_text("".join("line %03d %s\n" % (i, "a" * 80) for i in range(300)), encoding="utf-8")
    files = prepare_zip_files_for_char_limit([path], report_dir=tmp_path, max_chars=5000)
    assert path not in files
    assert any(p.name == "large_index.json" for p in files)
    assert all(len(p.read_text(encoding="utf-8")) <= 5000 for p in files)


def test_zip_inputs_have_no_large_original_file(tmp_path: Path) -> None:
    readme = tmp_path / "00_README_FOR_AI.md"
    manifest = tmp_path / "report_manifest.json"
    large = tmp_path / "f5_t03b_integration_sections.json"
    readme.write_text("readme\n", encoding="utf-8")
    manifest.write_text(json.dumps({"summary": True}), encoding="utf-8")
    large.write_text(json.dumps({"rows": [{"i": i, "text": "q" * 200} for i in range(300)]}, indent=2), encoding="utf-8")

    files = prepare_zip_files_for_char_limit(
        [readme, manifest, large],
        report_dir=tmp_path,
        manifest_path=manifest,
        readme_path=readme,
        max_chars=8000,
    )
    assert large not in files
    validate_zip_input_char_limit(files, max_chars=8000)

    zip_path = tmp_path / "AI_REVIEW_test.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in files:
            zf.write(file, arcname=file.name)
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
    assert "f5_t03b_integration_sections.json" not in names
    assert "f5_t03b_integration_sections_index.json" in names
