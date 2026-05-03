"""CSV / Parquet 上传与解析。

复用 `excel_uploads.UPLOADS_DIR` 和 `resolve_excel_path` 的安全检查
（确保 path 在 UPLOADS_DIR 内不会越权读其它路径）。
"""
from __future__ import annotations

import uuid
import zipfile
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile

from app.readers.csv_reader import list_columns as read_csv_columns
from app.readers.parquet_reader import list_columns as read_parquet_columns
from app.services._io_utils import check_zip_safety, decode_sql_content
from app.services import excel_uploads


_CSV_SUFFIXES = {".csv", ".tsv", ".txt"}
_PARQUET_SUFFIXES = {".parquet", ".pq"}
_LINEAGE_SCRIPT_SUFFIXES = {".sql", ".txt", ".zip"}


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
        "path": str(saved_path.relative_to(excel_uploads.RESULTS_DIR.parent)),
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
        "path": str(saved_path.relative_to(excel_uploads.RESULTS_DIR.parent)),
        "filename": Path(file.filename).name,
        "columns": columns,
    }


def save_uploaded_lineage_script(file: UploadFile) -> dict[str, Any]:
    """Persist a SQL/TXT/ZIP lineage input for workflow nodes.

    Workflow definitions cannot keep a browser File object. They store the
    returned relative path and the runner resolves it later for manual,
    async, rerun, scheduler, and sensor-triggered executions.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="Lineage script filename is required")
    filename = Path(file.filename).name
    suffix = Path(filename).suffix.lower()
    if suffix not in _LINEAGE_SCRIPT_SUFFIXES:
        raise HTTPException(status_code=400, detail="Only .sql, .txt and .zip lineage files are supported")

    saved_path = _save_upload_streaming(file, suffix)
    try:
        result: dict[str, Any] = {
            "path": str(saved_path.relative_to(excel_uploads.RESULTS_DIR.parent)),
            "filename": filename,
            "suffix": suffix,
            "kind": "zip" if suffix == ".zip" else "file",
            "size_bytes": saved_path.stat().st_size,
        }
        if suffix == ".zip":
            with zipfile.ZipFile(saved_path) as archive:
                check_zip_safety(archive)
                scripts = [
                    item.filename.replace("\\", "/")
                    for item in archive.infolist()
                    if not item.is_dir() and Path(item.filename).suffix.lower() in {".sql", ".txt"}
                ]
            if not scripts:
                raise HTTPException(status_code=400, detail="Zip file does not contain .sql or .txt files")
            result["script_count"] = len(scripts)
            result["script_names"] = scripts[:50]
        else:
            text = decode_sql_content(saved_path.read_bytes(), filename)
            result["preview"] = text[:300]
        return result
    except HTTPException:
        saved_path.unlink(missing_ok=True)
        raise
    except zipfile.BadZipFile as exc:
        saved_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Invalid zip file") from exc
    except Exception as exc:
        saved_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"Invalid lineage file: {exc}") from exc


def _save_upload_streaming(file: UploadFile, suffix: str) -> Path:
    """流式落盘 + size 上限保护，跟 save_uploaded_excel 共享 MAX_UPLOAD_BYTES。"""
    excel_uploads.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    saved_path = excel_uploads.UPLOADS_DIR / f"{uuid.uuid4().hex}{suffix}"
    try:
        bytes_written = 0
        with saved_path.open("wb") as out:
            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break
                bytes_written += len(chunk)
                if bytes_written > excel_uploads.MAX_UPLOAD_BYTES:
                    out.close()
                    saved_path.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=413,
                        detail=f"上传文件过大（>{excel_uploads.MAX_UPLOAD_BYTES // 1024 // 1024} MB）。"
                               "请先拆分，或改用 SQL 模式直连源数据。",
                    )
                out.write(chunk)
    except HTTPException:
        raise
    except Exception as exc:
        saved_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"Failed to save upload: {exc}") from exc
    return saved_path
