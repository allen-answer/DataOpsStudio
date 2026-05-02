"""通用工具型 endpoint：列名预览 / SQL 辅助 / Excel 上传。

这三个不属于任何特定业务实体（task / workflow / lineage），都是字段
选择 / SQL 编辑 / Excel 数据准备 UI 在保存任务前的辅助调用。
"""
from __future__ import annotations

from fastapi import APIRouter, Body, File, HTTPException, UploadFile

from app.dbclients.factory import fetch_columns
from app.models import (
    ExcelUploadResponse,
    PreviewColumnsResponse,
    SqlAssistResponse,
)
from app.readers.excel_reader import list_columns as read_excel_columns
from app.services import excel_uploads
from app.services.repositories import datasource_store
from app.services.sql_tools import sql_assist
from app.utils.sql_guard import validate_readonly_sql


router = APIRouter()


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
