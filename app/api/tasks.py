"""对比任务 CRUD + 同步 / 异步执行 + 行预览。

项目级隔离见 docs/PROJECT_AUTHORIZATION.md：list 按用户可访问项目过滤，
mutation / run / preview 校验用户对任务所在项目有权。
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Body, Depends, HTTPException

from app.api._authz import (
    filter_by_project,
    require_datasource_access,
    require_project_access,
)
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
from app.services.repositories import task_store
from app.services.resource_guard import decision_detail, guard_compare_run
from app.services.runner import build_reader, run_task
from app.services.sql_preflight import SQLPreflightDecision, assess_sql
from app.utils.sql_guard import validate_readonly_sql


# router 级默认：viewer 也要登录。mutation / run / preview 单独升级 editor。
router = APIRouter(dependencies=[Depends(get_current_user)])


def _preflight_or_raise(task: CompareTask) -> None:
    """sql_preflight enforce 模式下：block 级规则命中即 429。dry-run 跳过。

    `DATAOPS_SQL_PREFLIGHT_ENFORCE=true` 才强制；默认 false（advisory）。Excel /
    CSV / Parquet 源没 SQL 可查，跳过。
    """
    if os.getenv("DATAOPS_SQL_PREFLIGHT_ENFORCE", "false").strip().lower() not in {"1", "true", "yes", "on"}:
        return
    keys = list(task.key_columns or [])
    messages: list[str] = []
    for side, sql, kind in (
        ("source", task.source_sql, task.source_kind),
        ("target", task.target_sql if task.sql_mode == SqlMode.DOUBLE else "", task.target_kind),
    ):
        if kind != SourceKind.SQL or not sql:
            continue
        decision = assess_sql(
            sql=sql, dialect="", key_columns=keys, mode="compare",
            max_rows=task.limits.max_rows, stream_compare=task.limits.stream_compare,
        )
        if decision.blocking:
            block_lines = "; ".join(r.message for r in decision.rules if r.level == "block")
            messages.append(f"{side} SQL: {block_lines}")
    if messages:
        raise HTTPException(
            status_code=429,
            detail="SQL 静态体检命中阻断规则：" + " / ".join(messages),
        )


def _guard_or_raise(task: CompareTask, *, allow_queue: bool) -> None:
    """resource_guard 准入检查。dry-run（DATAOPS_GUARD_ENFORCE=false）只记不拦。

    enforce 模式下 deny → 429；queue 在异步路径放行（自然进 executor 队列），
    在同步路径拒绝并建议改后台执行（同步 run 不排队）。
    """
    decision = guard_compare_run(task)
    if not decision.enforced:
        return
    if decision.decision == "deny":
        raise HTTPException(status_code=429, detail=decision_detail(decision))
    if decision.decision == "queue" and not allow_queue:
        raise HTTPException(
            status_code=429,
            detail=decision_detail(decision) + "；同步执行不排队，请改用「后台执行」",
        )


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
    _guard_or_raise(task, allow_queue=False)
    _preflight_or_raise(task)
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
    _guard_or_raise(task, allow_queue=True)
    _preflight_or_raise(task)
    return submit_task_run(
        task_id,
        max_retries=(payload or {}).get("max_retries"),
        owner_user_id=current.id,
        project_id=task.project_id or "",
    )


@router.post("/api/sql/preflight", response_model=SQLPreflightDecision)
def sql_preflight_api(
    payload: dict[str, object] | None = Body(None),
    current: User = Depends(require_role("editor")),
) -> SQLPreflightDecision:
    """对比 SQL 运行前静态体检（advisory，不连库、不拦）。Workbench 点运行前调用。

    body: `{sql, dialect?, key_columns?, mode?, max_rows?, stream_compare?}`。
    路由挂在 tasks 模块下 —— preflight 紧贴 run，Workbench 同处调用。
    """
    payload = payload or {}
    sql = str(payload.get("sql") or "")
    if not sql.strip():
        raise HTTPException(status_code=400, detail="sql is required")
    raw_keys = payload.get("key_columns") or []
    if isinstance(raw_keys, str):
        key_columns = [c.strip() for c in raw_keys.split(",") if c.strip()]
    else:
        key_columns = [str(c) for c in raw_keys if str(c).strip()]
    mode = payload.get("mode")
    mode = mode if mode in ("preview", "compare") else "compare"
    try:
        max_rows = int(payload.get("max_rows") or 100_000)
    except (TypeError, ValueError):
        max_rows = 100_000
    return assess_sql(
        sql=sql,
        dialect=str(payload.get("dialect") or ""),
        key_columns=key_columns,
        mode=mode,
        max_rows=max_rows,
        stream_compare=bool(payload.get("stream_compare")),
    )


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
    # 项目级授权：override 路径下尤其关键 —— 否则任意能 preview 自己项目 task
    # 的 editor 都能把 override 指向别的项目的 datasource。非 override 路径也校验
    # 一遍，避免 task 被移动 / datasource project 变更后历史越权。
    datasource = require_datasource_access(current, datasource_id)
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
