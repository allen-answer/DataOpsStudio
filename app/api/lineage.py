"""SQL 血缘分析：单 SQL（JSON / form）+ 多脚本批量分析。"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile

from app.models import LineageAnalyzeResult, LineageBatchAnalyzeResponse, User
from app.services import lineage_ai, lineage_service
from app.services.auth import require_role
from app.services.lineage_ai_config import get_public_lineage_ai_config, save_lineage_ai_config


router = APIRouter()


@router.get("/api/lineage/ai/status")
def lineage_ai_status_api():
    return lineage_service.ai_status()


@router.get("/api/lineage/ai/config")
def lineage_ai_config_api(_: User = Depends(require_role("admin"))):
    return get_public_lineage_ai_config()


@router.put("/api/lineage/ai/config")
def update_lineage_ai_config_api(
    payload: dict[str, object] = Body(...),
    _: User = Depends(require_role("admin")),
):
    try:
        return save_lineage_ai_config(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/lineage/ai/test")
def test_lineage_ai_config_api(
    payload: dict[str, object] = Body(default={}),
    _: User = Depends(require_role("admin")),
):
    return lineage_ai.test_lineage_ai_connection(payload)


@router.get("/api/lineage/ai/jobs/{job_id}")
def lineage_ai_job_api(job_id: str):
    result = lineage_ai.get_lineage_ai_job(job_id)
    if not result:
        raise HTTPException(status_code=404, detail="AI job not found")
    return result


@router.get("/api/lineage/stress-fixture")
def lineage_stress_fixture_api(size: int = 1000):
    """Phase 10 #1：合成血缘大图压测 fixture（dev / 压测专用）。

    返回完整 lineage result（graph_groups + graph_edges + table_roles +
    target_summary + report），让前端能跳过分析直接渲染 N 张表的图，跑两个
    引擎对比。size 范围 [10, 10000]。同 size + 同 seed 永远生成同一份。
    """
    from app.services.lineage_stress import build_stress_fixture
    if size < 10 or size > 10000:
        raise HTTPException(status_code=400, detail="size 必须在 [10, 10000] 区间")
    try:
        return build_stress_fixture(size)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
    ai_enabled: str = Form(""),
    sql_file: UploadFile | None = File(None),
    schema_file: list[UploadFile] = File(default=[]),
):
    try:
        return lineage_service.analyze_form(
            sql, dialect, schema_datasource_id, schema_name,
            schema_table_filter, schema_only_sql_tables, schema_dialect,
            sql_file, schema_file, ai_enabled,
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
    ai_enabled: str = Form(""),
    sql_files: list[UploadFile] = File(default=[]),
    schema_file: list[UploadFile] = File(default=[]),
):
    try:
        return lineage_service.analyze_batch(
            dialect, schema_datasource_id, schema_name,
            schema_table_filter, schema_only_sql_tables, schema_dialect,
            sql_files, schema_file, ai_enabled,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
