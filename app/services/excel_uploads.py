from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile

from app.readers.excel_reader import list_columns, list_sheets
from app.utils.paths import RESULTS_DIR

UPLOADS_DIR = RESULTS_DIR / "uploads"


def save_uploaded_excel(file: UploadFile) -> dict[str, Any]:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Excel filename is required")
    suffix = Path(file.filename).suffix.lower()
    if suffix not in {".xlsx", ".xlsm"}:
        raise HTTPException(status_code=400, detail="Only .xlsx and .xlsm files are supported")

    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    saved_path = UPLOADS_DIR / f"{uuid.uuid4().hex}{suffix}"
    try:
        saved_path.write_bytes(file.file.read())
    except Exception as exc:
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


def resolve_excel_path(stored_path: str) -> Path:
    """Resolve a task-stored relative Excel path back to an absolute path,
    verifying it stays inside the uploads directory."""
    if not stored_path.strip():
        raise HTTPException(status_code=400, detail="Excel path is empty")
    candidate = (RESULTS_DIR.parent / stored_path).resolve()
    if UPLOADS_DIR.resolve() not in candidate.parents:
        raise HTTPException(status_code=400, detail="Excel path is outside the uploads directory")
    if not candidate.exists():
        raise HTTPException(status_code=400, detail=f"Excel file not found: {stored_path}")
    return candidate
