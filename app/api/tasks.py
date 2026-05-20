"""对比任务 CRUD + 同步 / 异步执行 + 行预览。

项目级隔离见 docs/PROJECT_AUTHORIZATION.md：list 按用户可访问项目过滤，
mutation / run / preview 校验用户对任务所在项目有权。
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException

from app.api._authz import filter_by_project, require_project_access
from app.api._shared import ensure_datasources_for_kind_authorized
from app.dbclients.factory import fetch_rows_with_schema
from app.models import (
    CompareResult,
    CompareTask,
    CompareTaskCreate,
    JobInfo,
    OkResponse,
    PreviewRowsResponse,
    SourceKind,
    SqlMode,
    User,
)
from app.services.auth import get_current_user, require_role
from app.services.jobs import submit_task_run
from app.services.repositories import datasource_store, task_store
from app.services.runner import build_reader, run_task
from app.utils.sql_guard import validate_readonly_sql


# router 级默认：viewer 也要登录。mutation / run / preview 单独升级 editor。
router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get("/api/tasks", response_model=list[CompareTask])
def list_tasks(project_id: str = "", current: User = Depends(get_current_user)):
    items = filter_by_project(task_store.list(), current)
    if project_id:
        items = [t for t in items if t.project_id == project_id or not t.project_id]
    return items


@router.post("/api/tasks", response_model=CompareTask)
def create_task(payload: CompareTaskCreate, current: User = Depends(require_role("editor"))):
    require_project_access(current, payload.project_id, detail="无权在该项目下创建对比任务")
    ensure_datasources_for_kind_authorized(payload, current)
    return task_store.create(payload)


@router.put("/api/tasks/{task_id}", response_model=CompareTask)
def update_task(task_id: str, payload: CompareTaskCreate, current: User = Depends(require_role("editor"))):
    existing = task_store.get(task_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Task not found")
    require_project_access(current, existing.project_id)
    if payload.project_id != existing.project_id:
        require_project_access(current, payload.project_id, detail="无权把对比任务移动到该项目")
    ensure_datasources_for_kind_authorized(payload, current)
    try:
        return task_store.update(task_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc


@router.delete("/api/tasks/{task_id}", response_model=OkResponse)
def delete_task(task_id: str, current: User = Depends(require_role("editor"))):
    existing = task_store.get(task_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Task not found")
    require_project_access(current, existing.project_id)
    try:
        task_store.delete(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc
    return {"ok": True}


@router.post("/api/tasks/{task_id}/copy", response_model=CompareTask)
def copy_task_api(task_id: str, current: User = Depends(require_role("editor"))):
    task = task_store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    require_project_access(current, task.project_id)
    payload = CompareTaskCreate(
        project_id=task.project_id,
        name=f"{task.name} 副本",
        source_kind=task.source_kind,
        target_kind=task.target_kind,
        source_id=task.source_id,
        target_id=task.target_id,
        sql_mode=task.sql_mode,
        source_sql=task.source_sql,
        target_sql=task.target_sql,
        source_excel_path=task.source_excel_path,
        source_sheet=task.source_sheet,
        source_header_row=task.source_header_row,
        target_excel_path=task.target_excel_path,
        target_sheet=task.target_sheet,
        target_header_row=task.target_header_row,
        source_file_path=task.source_file_path,
        source_file_encoding=task.source_file_encoding,
        source_csv_delimiter=task.source_csv_delimiter,
        target_file_path=task.target_file_path,
        target_file_encoding=task.target_file_encoding,
        target_csv_delimiter=task.target_csv_delimiter,
        key_columns=list(task.key_columns),
        rules=task.rules,
        limits=task.limits,
    )
    return task_store.create(payload)


@router.post("/api/tasks/{task_id}/run", response_model=CompareResult)
def run_task_api(task_id: str, current: User = Depends(require_role("editor"))):
    task = task_store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    require_project_access(current, task.project_id, detail="无权运行该项目的对比任务")
    try:
        return run_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/tasks/{task_id}/run-async", response_model=JobInfo)
def run_task_async_api(
    task_id: str,
    payload: dict[str, object] | None = Body(None),
    current: User = Depends(require_role("editor")),
):
    task = task_store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    require_project_access(current, task.project_id, detail="无权运行该项目的对比任务")
    return submit_task_run(task_id, max_retries=(payload or {}).get("max_retries"))


@router.post("/api/tasks/{task_id}/preview", response_model=PreviewRowsResponse)
def preview_task_api(
    task_id: str,
    payload: dict[str, object] | None = Body(None),
    current: User = Depends(require_role("editor")),
):
    payload = payload or {}
    task = task_store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    require_project_access(current, task.project_id)
    side = str(payload.get("side") or "source")
    limit = int(payload.get("limit") or 20)
    preview_limit = min(limit, 200)
    kind = task.target_kind if side == "target" else task.source_kind
    if kind != SourceKind.SQL:
        try:
            rows = []
            truncated = False
            for index, row in enumerate(build_reader(task, side).iter_rows(max_rows=None), start=1):
                if index > preview_limit:
                    truncated = True
                    break
                rows.append(row)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "side": side,
            "limit": preview_limit,
            "truncated": truncated,
            "columns": list(rows[0]) if rows else [],
            "rows": rows,
        }

    datasource_id = task.target_id if side == "target" else task.source_id
    override_datasource_id = payload.get("datasource_id")
    if isinstance(override_datasource_id, str) and override_datasource_id.strip():
        datasource_id = override_datasource_id.strip()
    datasource = datasource_store.get(datasource_id)
    if datasource is None:
        raise HTTPException(status_code=404, detail="Datasource not found")
    sql = task.target_sql if side == "target" and task.sql_mode == SqlMode.DOUBLE else task.source_sql
    override_sql = payload.get("sql")
    if isinstance(override_sql, str) and override_sql.strip():
        sql = override_sql
    try:
        validate_readonly_sql(sql)
        result = fetch_rows_with_schema(datasource, sql, max_rows=preview_limit, raise_on_overflow=False)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "side": side,
        "limit": preview_limit,
        "truncated": len(result.rows) == preview_limit,
        "columns": result.columns,
        "warnings": result.warnings,
        "rows": result.rows,
    }
