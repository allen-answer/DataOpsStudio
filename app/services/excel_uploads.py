from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile

from app.readers.excel_reader import list_columns, list_sheets
from app.utils.paths import RESULTS_DIR

UPLOADS_DIR = RESULTS_DIR / "uploads"

# 上传上限：50 MB。openpyxl read_only 也会被超大文件拖慢甚至 OOM；
# 值偏保守，触发后给出明确错误，让用户先在源端做拆分或改 SQL 模式。
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def save_uploaded_excel(file: UploadFile) -> dict[str, Any]:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Excel filename is required")
    suffix = Path(file.filename).suffix.lower()
    if suffix not in {".xlsx", ".xlsm"}:
        raise HTTPException(status_code=400, detail="Only .xlsx and .xlsm files are supported")

    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    saved_path = UPLOADS_DIR / f"{uuid.uuid4().hex}{suffix}"
    try:
        # 流式读，超出上限直接拒绝（不要把整个文件读到内存再判断）
        bytes_written = 0
        with saved_path.open("wb") as out:
            while True:
                chunk = file.file.read(1024 * 1024)   # 1 MB 块
                if not chunk:
                    break
                bytes_written += len(chunk)
                if bytes_written > MAX_UPLOAD_BYTES:
                    out.close()
                    saved_path.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=413,
                        detail=f"Excel 文件过大（>{MAX_UPLOAD_BYTES // 1024 // 1024} MB）。请先拆分，或改用 SQL 模式直连源数据。",
                    )
                out.write(chunk)
    except HTTPException:
        raise
    except Exception as exc:
        saved_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"Failed to save Excel: {exc}") from exc

    try:
        sheets = list_sheets(saved_path)
        columns_by_sheet = {sheet: list_columns(saved_path, sheet=sheet, header_row=1) for sheet in sheets}
    except Exception as exc:
        saved_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"Invalid Excel file: {exc}") from exc

    # Path stored in task config is relative to BASE_DIR so the file resolves
    # the same whether the task runs in dev or in a rebuilt container.
    return {
        "path": str(saved_path.relative_to(RESULTS_DIR.parent)),
        "filename": Path(file.filename).name,
        "sheets": sheets,
        "columns_by_sheet": columns_by_sheet,
    }


def resolve_uploaded_path(
    stored_path: str,
    *,
    allowed_suffixes: set[str] | None = None,
    label: str = "uploaded file",
) -> Path:
    """Resolve a task-stored upload path back to an absolute path.

    Upload paths are stored relative to the repo root, but execution can happen
    from dev, tests, or a rebuilt container. Keep the path inside UPLOADS_DIR
    and optionally constrain the extension for the reader that will consume it.
    """
    if not stored_path.strip():
        raise HTTPException(status_code=400, detail=f"{label} path is empty")
    candidate = (RESULTS_DIR.parent / stored_path).resolve()
    if UPLOADS_DIR.resolve() not in candidate.parents:
        raise HTTPException(status_code=400, detail=f"{label} path is outside the uploads directory")
    if not candidate.exists():
        raise HTTPException(status_code=400, detail=f"{label} not found: {stored_path}")
    if allowed_suffixes and candidate.suffix.lower() not in allowed_suffixes:
        suffixes = ", ".join(sorted(allowed_suffixes))
        raise HTTPException(status_code=400, detail=f"{label} must use one of: {suffixes}")
    return candidate


def resolve_excel_path(stored_path: str) -> Path:
    """Resolve a task-stored relative Excel path back to an absolute path."""
    return resolve_uploaded_path(
        stored_path,
        allowed_suffixes={".xlsx", ".xlsm"},
        label="Excel file",
    )
