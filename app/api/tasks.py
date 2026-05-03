"""对比任务 CRUD + 同步 / 异步执行 + 行预览。"""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException

from app.api._shared import ensure_datasources_for_kind
from app.dbclients.factory import fetch_rows
from app.models import (
    CompareResult,
    CompareTask,
    CompareTaskCreate,
    JobInfo,
    OkResponse,
    PreviewRowsResponse,
    SourceKind,
    SqlMode,
)
from app.services.jobs import submit_task_run
from app.services.repositories import datasource_store, task_store
from app.services.runner import build_reader, run_task
from app.utils.sql_guard import validate_readonly_sql


router = APIRouter()


@router.get("/api/tasks", response_model=list[CompareTask])
def list_tasks(project_id: str = ""):
    items = task_store.list()
    if project_id:
        items = [t for t in items if t.project_id == project_id or not t.project_id]
    return items


@router.post("/api/tasks", response_model=CompareTask)
def create_task(payload: CompareTaskCreate):
    ensure_datasources_for_kind(payload)
    return task_store.create(payload)


@router.put("/api/tasks/{task_id}", response_model=CompareTask)
def update_task(task_id: str, payload: CompareTaskCreate):
    ensure_datasources_for_kind(payload)
    try:
        return task_store.update(task_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc


@router.delete("/api/tasks/{task_id}", response_model=OkResponse)
def delete_task(task_id: str):
    try:
        task_store.delete(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc
    return {"ok": True}


@router.post("/api/tasks/{task_id}/copy", response_model=CompareTask)
def copy_task_api(task_id: str):
    task = task_store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    payload = CompareTaskCreate(
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
def run_task_api(task_id: str):
    try:
        return run_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/tasks/{task_id}/run-async", response_model=JobInfo)
def run_task_async_api(task_id: str, payload: dict[str, object] | None = Body(None)):
    if task_store.get(task_id) is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return submit_task_run(task_id, max_retries=(payload or {}).get("max_retries"))


@router.post("/api/tasks/{task_id}/preview", response_model=PreviewRowsResponse)
def preview_task_api(task_id: str, payload: dict[str, object] | None = Body(None)):
    payload = payload or {}
    task = task_store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    side = str(payload.get("side") or "source")
    limit = int(payload.get("limit") or 20)
    preview_limit = min(limit, 200)
    kind = task.target_kind if side == "target" else task.source_kind
    if kind != SourceKind.SQL:
        try:
            rows = build_reader(task, side).fetch_all(max_rows=preview_limit)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"side": side, "limit": preview_limit, "truncated": len(rows) == preview_limit, "rows": rows}

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
        rows = fetch_rows(datasource, sql, max_rows=preview_limit, raise_on_overflow=False)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"side": side, "limit": preview_limit, "truncated": len(rows) == preview_limit, "rows": rows}
