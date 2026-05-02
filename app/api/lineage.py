"""SQL 血缘分析：单 SQL（JSON / form）+ 多脚本批量分析。"""
from __future__ import annotations

from fastapi import APIRouter, Body, File, Form, HTTPException, UploadFile

from app.models import LineageAnalyzeResult, LineageBatchAnalyzeResponse
from app.services import lineage_service


router = APIRouter()


@router.post("/api/lineage/analyze", response_model=LineageAnalyzeResult)
def lineage_api(payload: dict[str, str] = Body(...)):
    try:
        return lineage_service.analyze_json(payload)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/lineage/analyze-form", response_model=LineageAnalyzeResult)
def lineage_form_api(
    sql: str = Form(""),
    dialect: str = Form(""),
    schema_datasource_id: str = Form(""),
    schema_name: str = Form(""),
    schema_table_filter: str = Form(""),
    schema_only_sql_tables: str = Form(""),
    schema_dialect: str = Form(""),
    sql_file: UploadFile | None = File(None),
    schema_file: list[UploadFile] = File(default=[]),
):
    try:
        return lineage_service.analyze_form(
            sql, dialect, schema_datasource_id, schema_name,
            schema_table_filter, schema_only_sql_tables, schema_dialect,
            sql_file, schema_file,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/lineage/batch/analyze", response_model=LineageBatchAnalyzeResponse)
def lineage_batch_api(
    dialect: str = Form(""),
    schema_datasource_id: str = Form(""),
    schema_name: str = Form(""),
    schema_table_filter: str = Form(""),
    schema_only_sql_tables: str = Form(""),
    schema_dialect: str = Form(""),
    sql_files: list[UploadFile] = File(default=[]),
    schema_file: list[UploadFile] = File(default=[]),
):
    try:
        return lineage_service.analyze_batch(
            dialect, schema_datasource_id, schema_name,
            schema_table_filter, schema_only_sql_tables, schema_dialect,
            sql_files, schema_file,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
