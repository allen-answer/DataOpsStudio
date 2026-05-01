"""Tests for app.services.excel_uploads — focused on the hard size guard."""
from __future__ import annotations

import io
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.services import excel_uploads


def _fake_upload(filename: str, content: bytes):
    """Mimic FastAPI's UploadFile shape for the bits save_uploaded_excel reaches."""
    upload = MagicMock()
    upload.filename = filename
    upload.file = io.BytesIO(content)
    return upload


def test_oversized_upload_rejected_with_413(monkeypatch, tmp_path):
    """Files above MAX_UPLOAD_BYTES must be rejected with 413, and any
    partially-written file on disk must be cleaned up so the next upload
    starts fresh."""
    monkeypatch.setattr(excel_uploads, "UPLOADS_DIR", tmp_path)
    monkeypatch.setattr(excel_uploads, "MAX_UPLOAD_BYTES", 1024)   # 1 KB cap for the test

    big = b"x" * 4096
    upload = _fake_upload("huge.xlsx", big)

    with pytest.raises(HTTPException) as exc_info:
        excel_uploads.save_uploaded_excel(upload)

    assert exc_info.value.status_code == 413
    assert "过大" in exc_info.value.detail
    # No leftover partial files in the uploads dir
    assert not any(tmp_path.iterdir())


def test_disallowed_extension_rejected_before_io(monkeypatch, tmp_path):
    monkeypatch.setattr(excel_uploads, "UPLOADS_DIR", tmp_path)
    upload = _fake_upload("evil.csv", b"id,name\n1,a")

    with pytest.raises(HTTPException) as exc_info:
        excel_uploads.save_uploaded_excel(upload)

    assert exc_info.value.status_code == 400
    assert "xlsx" in exc_info.value.detail.lower()


def test_run_limits_reject_unreasonable_max_rows():
    """The Pydantic ceiling on RunLimits.max_rows blocks 1B-row configs."""
    from app.models import RunLimits

    # Just below ceiling: OK
    RunLimits(max_rows=10_000_000)

    # Above ceiling: validation error
    with pytest.raises(Exception):  # Pydantic ValidationError
        RunLimits(max_rows=100_000_000)
