"""CSV / Parquet 上传与解析。

复用 `excel_uploads.UPLOADS_DIR` 和 `resolve_excel_path` 的安全检查
（确保 path 在 UPLOADS_DIR 内不会越权读其它路径）。
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile

from app.readers.csv_reader import list_columns as read_csv_columns
from app.readers.parquet_reader import list_columns as read_parquet_columns
from app.services.excel_uploads import MAX_UPLOAD_BYTES, UPLOADS_DIR
from app.utils.paths import RESULTS_DIR


_CSV_SUFFIXES = {".csv", ".tsv", ".txt"}
_PARQUET_SUFFIXES = {".parquet", ".pq"}


def save_uploaded_csv(file: UploadFile) -> dict[str, Any]:
    """保存上传的 CSV/TSV 文件，返回相对路径 + filename + 列名。
    跟 save_uploaded_excel 相同的流式上限保护。"""
    if not file.filename:
        raise HTTPException(status_code=400, detail="CSV filename is required")
    suffix = Path(file.filename).suffix.lower()
    if suffix not in _CSV_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail="Only .csv / .tsv / .txt files are supported",
        )

    saved_path = _save_upload_streaming(file, suffix)
    delimiter = "\t" if suffix == ".tsv" else ","
    encoding = "utf-8-sig"
    try:
        columns = read_csv_columns(
            saved_path,
            encoding=encoding,
            delimiter=delimiter,
            header_row=1,
        )
    except UnicodeDecodeError:
        # utf-8 失败时尝试 GBK —— 国内 ETL 常见
        try:
            columns = read_csv_columns(
                saved_path,
                encoding="gbk",
                delimiter=delimiter,
                header_row=1,
            )
            encoding = "gbk"
        except Exception as exc:
            saved_path.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail=f"Failed to read CSV: {exc}") from exc
    except Exception as exc:
        saved_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"Invalid CSV file: {exc}") from exc

    return {
        "path": str(saved_path.relative_to(RESULTS_DIR.parent)),
        "filename": Path(file.filename).name,
        "columns": columns,
        "encoding": encoding,
        "delimiter": delimiter,
    }


def save_uploaded_parquet(file: UploadFile) -> dict[str, Any]:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Parquet filename is required")
    suffix = Path(file.filename).suffix.lower()
    if suffix not in _PARQUET_SUFFIXES:
        raise HTTPException(status_code=400, detail="Only .parquet / .pq files are supported")

    saved_path = _save_upload_streaming(file, suffix)
    try:
        columns = read_parquet_columns(saved_path)
    except Exception as exc:
        saved_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"Invalid Parquet file: {exc}") from exc

    return {
        "path": str(saved_path.relative_to(RESULTS_DIR.parent)),
        "filename": Path(file.filename).name,
        "columns": columns,
    }


def _save_upload_streaming(file: UploadFile, suffix: str) -> Path:
    """流式落盘 + size 上限保护，跟 save_uploaded_excel 共享 MAX_UPLOAD_BYTES。"""
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    saved_path = UPLOADS_DIR / f"{uuid.uuid4().hex}{suffix}"
    try:
        bytes_written = 0
        with saved_path.open("wb") as out:
            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break
                bytes_written += len(chunk)
                if bytes_written > MAX_UPLOAD_BYTES:
                    out.close()
                    saved_path.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=413,
                        detail=f"上传文件过大（>{MAX_UPLOAD_BYTES // 1024 // 1024} MB）。"
                               "请先拆分，或改用 SQL 模式直连源数据。",
                    )
                out.write(chunk)
    except HTTPException:
        raise
    except Exception as exc:
        saved_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"Failed to save upload: {exc}") from exc
    return saved_path
