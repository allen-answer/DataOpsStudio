"""通用工具型 endpoint：列名预览 / SQL 辅助 / Excel 上传。

这三个不属于任何特定业务实体（task / workflow / lineage），都是字段
选择 / SQL 编辑 / Excel 数据准备 UI 在保存任务前的辅助调用。
"""
from __future__ import annotations

from fastapi import APIRouter, Body, File, HTTPException, UploadFile

from app.dbclients.factory import fetch_columns, fetch_rows
from app.models import (
    ExcelUploadResponse,
    PreviewColumnsResponse,
    PreviewRowsResponse,
    SqlAssistResponse,
)
from app.readers.csv_reader import CsvReader, list_columns as read_csv_columns
from app.readers.excel_reader import ExcelReader, list_columns as read_excel_columns
from app.readers.parquet_reader import ParquetReader, list_columns as read_parquet_columns
from app.services import excel_uploads, file_uploads
from app.services.repositories import datasource_store
from app.services.sql_tools import sql_assist
from app.utils.sql_guard import validate_readonly_sql


router = APIRouter()


def _preview_reader_rows(reader, limit: int) -> tuple[list[dict[str, object]], bool]:
    rows: list[dict[str, object]] = []
    truncated = False
    for index, row in enumerate(reader.iter_rows(max_rows=None), start=1):
        if index > limit:
            truncated = True
            break
        rows.append(row)
    return rows, truncated


@router.post("/api/preview/rows", response_model=PreviewRowsResponse)
def preview_rows_api(payload: dict[str, object] = Body(...)):
    """Preview rows from the current draft without requiring a saved task."""
    kind = str(payload.get("kind") or "sql").lower()
    side = str(payload.get("side") or "source")
    limit = min(max(1, int(payload.get("limit") or 20)), 200)

    if kind == "sql":
        datasource_id = str(payload.get("datasource_id") or "").strip()
        sql = str(payload.get("sql") or "")
        datasource = datasource_store.get(datasource_id)
        if datasource is None:
            raise HTTPException(status_code=404, detail="Datasource not found")
        try:
            validate_readonly_sql(sql)
            rows = fetch_rows(datasource, sql, max_rows=limit, raise_on_overflow=False)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"side": side, "limit": limit, "truncated": len(rows) == limit, "rows": rows}

    try:
        if kind == "excel":
            absolute_path = excel_uploads.resolve_excel_path(str(payload.get("excel_path") or ""))
            reader = ExcelReader(
                absolute_path,
                sheet=str(payload.get("sheet") or ""),
                header_row=int(payload.get("header_row") or 1),
            )
        elif kind == "csv":
            absolute_path = excel_uploads.resolve_uploaded_path(
                str(payload.get("file_path") or ""),
                allowed_suffixes={".csv", ".tsv", ".txt"},
                label="CSV file",
            )
            reader = CsvReader(
                absolute_path,
                encoding=str(payload.get("encoding") or "utf-8-sig"),
                delimiter=str(payload.get("delimiter") or ","),
                header_row=int(payload.get("header_row") or 1),
            )
        elif kind == "parquet":
            absolute_path = excel_uploads.resolve_uploaded_path(
                str(payload.get("file_path") or ""),
                allowed_suffixes={".parquet", ".pq"},
                label="Parquet file",
            )
            reader = ParquetReader(absolute_path)
        else:
            raise HTTPException(status_code=400, detail=f"Unknown kind: {kind}")
        rows, truncated = _preview_reader_rows(reader, limit)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"side": side, "limit": limit, "truncated": truncated, "rows": rows}


@router.post("/api/preview/columns", response_model=PreviewColumnsResponse)
def preview_columns_api(payload: dict[str, object] = Body(...)):
    """Return column names for a SQL query or Excel sheet without persisting
    a task. Used by the field-selection UI so users can pick include/exclude
    before saving the task."""
    kind = str(payload.get("kind") or "sql").lower()
    if kind == "sql":
        datasource_id = str(payload.get("datasource_id") or "").strip()
        sql = str(payload.get("sql") or "")
        datasource = datasource_store.get(datasource_id)
        if datasource is None:
            raise HTTPException(status_code=404, detail="Datasource not found")
        try:
            validate_readonly_sql(sql)
            columns = fetch_columns(datasource, sql)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"columns": columns}
    if kind == "excel":
        excel_path = str(payload.get("excel_path") or "")
        sheet = str(payload.get("sheet") or "")
        header_row = int(payload.get("header_row") or 1)
        try:
            absolute_path = excel_uploads.resolve_excel_path(excel_path)
            columns = read_excel_columns(absolute_path, sheet=sheet, header_row=header_row)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"columns": columns}
    if kind == "csv":
        file_path = str(payload.get("file_path") or "")
        encoding = str(payload.get("encoding") or "utf-8-sig")
        delimiter = str(payload.get("delimiter") or ",")
        header_row = int(payload.get("header_row") or 1)
        try:
            absolute_path = excel_uploads.resolve_uploaded_path(
                file_path,
                allowed_suffixes={".csv", ".tsv", ".txt"},
                label="CSV file",
            )
            columns = read_csv_columns(
                absolute_path,
                encoding=encoding,
                delimiter=delimiter,
                header_row=header_row,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"columns": columns}
    if kind == "parquet":
        file_path = str(payload.get("file_path") or "")
        try:
            absolute_path = excel_uploads.resolve_uploaded_path(
                file_path,
                allowed_suffixes={".parquet", ".pq"},
                label="Parquet file",
            )
            columns = read_parquet_columns(absolute_path)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"columns": columns}
    raise HTTPException(status_code=400, detail=f"Unknown kind: {kind}")


@router.post("/api/sql/assist", response_model=SqlAssistResponse)
def sql_assist_api(payload: dict[str, str] = Body(...)):
    try:
        return sql_assist(payload.get("sql", ""), payload.get("dialect") or None, payload.get("target_dialect") or None)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/uploads/excel", response_model=ExcelUploadResponse)
def upload_excel_api(file: UploadFile = File(...)):
    return excel_uploads.save_uploaded_excel(file)


@router.post("/api/uploads/csv")
def upload_csv_api(file: UploadFile = File(...)):
    return file_uploads.save_uploaded_csv(file)


@router.post("/api/uploads/parquet")
def upload_parquet_api(file: UploadFile = File(...)):
    return file_uploads.save_uploaded_parquet(file)


@router.post("/api/uploads/lineage-script")
def upload_lineage_script_api(file: UploadFile = File(...)):
    return file_uploads.save_uploaded_lineage_script(file)
