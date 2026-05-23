"""SQL 血缘分析：单 SQL（JSON / form）+ 多脚本批量分析。"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Request, UploadFile

from app.models import LineageAnalyzeResult, LineageBatchAnalyzeResponse, User
from app.services import lineage_ai, lineage_service
from app.services.auth import ensure_recent_auth, get_current_user, require_role
from app.services.lineage_ai_config import get_public_lineage_ai_config, save_lineage_ai_config


# router 级 default：viewer 读 ai status / fixture / job 查询。
# analyze / trace-compare / ai config (admin) 各自单独升级。
router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get("/api/lineage/ai/status")
def lineage_ai_status_api():
    return lineage_service.ai_status()


@router.get("/api/lineage/ai/config")
def lineage_ai_config_api(_: User = Depends(require_role("admin"))):
    return get_public_lineage_ai_config()


@router.put("/api/lineage/ai/config")
def update_lineage_ai_config_api(
    request: Request,
    payload: dict[str, object] = Body(...),
    _: User = Depends(require_role("admin")),
):
    """保存 AI 配置（含加密落盘的 API Key）—— admin only + step-up（300s）。

    API Key 是高敏感凭据，跟「含密码导出」「删用户」同级处理。
    """
    ensure_recent_auth(request, max_age=300)
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


@router.post("/api/lineage/trace-compare")
def lineage_trace_compare_api(
    payload: dict = Body(...),
    user: User = Depends(require_role("editor")),
):
    """Phase 11 MVP：根据 focal `(table, column)` 的字段血缘链，生成一组
    compare 节点配置（每条 upstream → downstream 边一个节点），caller 拿到
    workflow_draft 可直接 POST /api/workflows 建为正式作业流。
    """
    from app.services.trace_compare import trace_compare
    try:
        return trace_compare(
            table=str(payload.get("table") or ""),
            column=str(payload.get("column") or ""),
            key_column=str(payload.get("key_column") or ""),
            base_task_id=str(payload.get("base_task_id") or ""),
            sample_keys=list(payload.get("sample_keys") or []),
            datasource_map=dict(payload.get("datasource_map") or {}),
            per_table_keys=dict(payload.get("per_table_keys") or {}),
            depth=int(payload.get("depth") or 3),
            project_id=str(payload.get("project_id") or ""),
            run_limit=int(payload.get("run_limit") or 50),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/lineage/analyze", response_model=LineageAnalyzeResult)
def lineage_api(payload: dict[str, str] = Body(...), _: User = Depends(require_role("editor"))):
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
    _: User = Depends(require_role("editor")),
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
    _: User = Depends(require_role("editor")),
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
