from __future__ import annotations

import io
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.services import excel_uploads, file_uploads


def _fake_upload(filename: str, content: bytes):
    upload = MagicMock()
    upload.filename = filename
    upload.file = io.BytesIO(content)
    return upload


def _patch_upload_dirs(monkeypatch, tmp_path):
    results = tmp_path / "results"
    uploads = results / "uploads"
    uploads.mkdir(parents=True)
    monkeypatch.setattr(excel_uploads, "RESULTS_DIR", results)
    monkeypatch.setattr(excel_uploads, "UPLOADS_DIR", uploads)
    return results, uploads


def test_csv_upload_detects_columns_and_returns_relative_path(monkeypatch, tmp_path):
    _patch_upload_dirs(monkeypatch, tmp_path)
    upload = _fake_upload("users.csv", b"id,name\n1,Alice\n")

    result = file_uploads.save_uploaded_csv(upload)

    assert result["filename"] == "users.csv"
    assert result["columns"] == ["id", "name"]
    assert result["encoding"] == "utf-8-sig"
    assert result["delimiter"] == ","
    assert Path(result["path"]).parts[-2:] == ("uploads", Path(result["path"]).name)


def test_csv_upload_uses_tsv_delimiter(monkeypatch, tmp_path):
    _patch_upload_dirs(monkeypatch, tmp_path)
    upload = _fake_upload("users.tsv", b"id\tname\n1\tAlice\n")

    result = file_uploads.save_uploaded_csv(upload)

    assert result["columns"] == ["id", "name"]
    assert result["delimiter"] == "\t"


def test_csv_upload_size_guard_reuses_excel_limit(monkeypatch, tmp_path):
    _patch_upload_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(excel_uploads, "MAX_UPLOAD_BYTES", 8)
    upload = _fake_upload("huge.csv", b"x" * 32)

    with pytest.raises(HTTPException) as exc_info:
        file_uploads.save_uploaded_csv(upload)

    assert exc_info.value.status_code == 413


def test_lineage_script_upload_accepts_txt(monkeypatch, tmp_path):
    _patch_upload_dirs(monkeypatch, tmp_path)
    upload = _fake_upload("job.txt", "select * from ods.orders".encode("utf-8"))

    result = file_uploads.save_uploaded_lineage_script(upload)

    assert result["filename"] == "job.txt"
    assert result["kind"] == "file"
    assert "select *" in result["preview"].lower()
    assert Path(result["path"]).parts[-2:] == ("uploads", Path(result["path"]).name)


def test_lineage_script_upload_accepts_zip(monkeypatch, tmp_path):
    _patch_upload_dirs(monkeypatch, tmp_path)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("a.sql", "select * from a")
        archive.writestr("nested/b.txt", "select * from b")
        archive.writestr("README.md", "ignored")
    upload = _fake_upload("jobs.zip", buf.getvalue())

    result = file_uploads.save_uploaded_lineage_script(upload)

    assert result["filename"] == "jobs.zip"
    assert result["kind"] == "zip"
    assert result["script_count"] == 2
    assert "nested/b.txt" in result["script_names"]
