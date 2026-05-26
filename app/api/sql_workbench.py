"""SQL Workbench v0.1 endpoints —— 数据工程师 / DBA 日常跑 SELECT 工作台。

约束:
- 仅 SELECT / WITH,sql_guard 校验
- editor 角色 + datasource project 鉴权
- console 跨用户隔离(只列自己的)
- history append-only ring buffer

端点:
- GET    /api/sql-workbench/consoles               列自己的 tabs
- POST   /api/sql-workbench/consoles               新建 tab
- PUT    /api/sql-workbench/consoles/{id}          改 tab(partial)
- DELETE /api/sql-workbench/consoles/{id}          删 tab
- POST   /api/sql-workbench/execute                跑 SQL
- GET    /api/sql-workbench/history                查历史(可按 datasource 过滤)
- GET    /api/sql-workbench/metadata/schemas       (Phase 3 接 introspect,Phase 1 stub)
- GET    /api/sql-workbench/metadata/tables        同上
- GET    /api/sql-workbench/metadata/columns       同上
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from pydantic import BaseModel, Field

from app.api._authz import require_datasource_access
from app.models import User
from app.services.auth import require_role
from app.sqlide.executor import execute_sql
from app.sqlide.explain import explain_sql as _explain_sql
from app.sqlide.format import format_sql as _format_sql
from app.sqlide.models import (
    ConsoleCreate,
    ConsoleUpdate,
    ExecuteRequest,
    ExecuteResponse,
    HistoryEntry,
)
from app.sqlide.runtime import (
    Execution,
    get_execution,
    request_cancel,
    start_execution,
)
from app.sqlide.storage import sql_workbench_store


logger = logging.getLogger(__name__)


# editor+ 才能用,跟数据对比 / 慢 SQL 同口径。SELECT 烧 DB 资源不给 viewer 玩。
router = APIRouter(dependencies=[Depends(require_role("editor"))])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ─── consoles CRUD ─────────────────────────────────────────────────────────

@router.get("/api/sql-workbench/consoles")
def list_consoles(
    current: User = Depends(require_role("editor")),
) -> dict[str, Any]:
    items = sql_workbench_store.list_consoles(owner_user_id=current.id)
    return {"items": [c.model_dump(mode="json") for c in items]}


@router.post("/api/sql-workbench/consoles")
def create_console(
    payload: ConsoleCreate = Body(...),
    current: User = Depends(require_role("editor")),
) -> dict[str, Any]:
    # 选了 datasource 就校验权限(空表示用户先建空 tab 再选)
    if payload.datasource_id:
        require_datasource_access(current, payload.datasource_id)
    console = sql_workbench_store.create_console(payload, owner_user_id=current.id)
    return console.model_dump(mode="json")


@router.put("/api/sql-workbench/consoles/{console_id}")
def update_console(
    console_id: str,
    payload: ConsoleUpdate = Body(...),
    current: User = Depends(require_role("editor")),
) -> dict[str, Any]:
    existing = sql_workbench_store.get_console(console_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Console 不存在")
    if existing.owner_user_id != current.id:
        raise HTTPException(status_code=403, detail="无权修改他人的 console")
    # 若改了 datasource_id,新 ds 也得校验权限
    if payload.datasource_id is not None and payload.datasource_id:
        require_datasource_access(current, payload.datasource_id)
    try:
        updated = sql_workbench_store.update_console(console_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Console 不存在") from exc
    return updated.model_dump(mode="json")


@router.delete("/api/sql-workbench/consoles/{console_id}")
def delete_console(
    console_id: str,
    current: User = Depends(require_role("editor")),
) -> dict[str, Any]:
    existing = sql_workbench_store.get_console(console_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Console 不存在")
    if existing.owner_user_id != current.id:
        raise HTTPException(status_code=403, detail="无权删除他人的 console")
    sql_workbench_store.delete_console(console_id)
    return {"ok": True}


# ─── execute (v0.2 异步化 + 可中断) ────────────────────────────────────────

def _execution_envelope(exe: Execution) -> dict[str, Any]:
    """v0.2 envelope:execution_id + status + (done/failed 时平铺 ExecuteResponse 字段)。

    保留 v0.1 顶层字段(success / columns / rows / row_count / elapsed_ms / truncated /
    error)兼容 v0.1 客户端 —— 完成的快查询本质 shape 没变,只是多了 execution_id / status。
    """
    env: dict[str, Any] = {
        "execution_id": exe.id,
        "status": exe.status,
        "cancel_requested": exe.cancel_requested,
    }
    if exe.result is not None:
        env.update(exe.result.model_dump(mode="json"))
    elif exe.error:
        env["error"] = exe.error
        env["success"] = False
    return env


def _append_history_for_execution(exe: Execution, ds_name: str, project_id: str, username: str) -> None:
    """worker 完成时落 history。cancelled 也记一条标识。"""
    if exe.result is not None:
        success = exe.result.success
        elapsed_ms = exe.result.elapsed_ms
        row_count = exe.result.row_count
        truncated = exe.result.truncated
        error = exe.result.error
    else:
        success = False
        elapsed_ms = 0
        row_count = 0
        truncated = False
        error = exe.error or ("cancelled" if exe.status == "cancelled" else None)
    entry = HistoryEntry(
        id=uuid.uuid4().hex,
        datasource_id=exe.datasource_id,
        datasource_name=ds_name,
        sql=exe.sql,
        executed_by=username,
        project_id=project_id,
        executed_at=exe.finished_at or _now(),
        success=success,
        elapsed_ms=elapsed_ms,
        row_count=row_count,
        truncated=truncated,
        error=error,
    )
    try:
        sql_workbench_store.append_history(entry)
    except Exception:  # pragma: no cover
        logger.exception("history append failed")


@router.post("/api/sql-workbench/execute")
def execute(
    payload: ExecuteRequest = Body(...),
    current: User = Depends(require_role("editor")),
) -> dict[str, Any]:
    ds = require_datasource_access(current, payload.datasource_id)

    if not getattr(ds, "allow_select", True):
        raise HTTPException(
            status_code=403,
            detail=f"数据源 {ds.name} 已禁用 SELECT(allow_select=false)",
        )

    ds_name = ds.name
    project_id = ds.project_id or ""
    username = current.username

    def _on_finish(exe: Execution) -> None:
        _append_history_for_execution(exe, ds_name=ds_name, project_id=project_id, username=username)

    # 提交执行(线程池跑),sync_wait 内能完成则直接返 done;否则返 running + execution_id
    exe = start_execution(
        user_id=current.id,
        datasource=ds,
        sql=payload.sql,
        max_rows=payload.max_rows,
        console_id=payload.console_id,
    )
    # 注册 on_finish:这里手动 wrap —— start_execution 没接 callback,我们在
    # _execution_envelope 返回后,若 status 还是 running,前端会 poll 拿状态;
    # history 写入由 cancel/poll 路径里检测 finished 时触发。但更稳是 worker
    # 完成时自己写 history —— 因 worker 完成时 caller 已没 ds_name/username
    # 上下文。把 callback 改成显式 on-finish 注入更优雅:
    from app.sqlide import runtime as _runtime
    with _runtime._lock:  # type: ignore[attr-defined]
        # 若已经 done(快查询在 sync_wait 内完成),直接 append history
        if exe.status in ("done", "failed", "cancelled"):
            _runtime._executions[exe.id]._already_history = True  # type: ignore[attr-defined]
            _append_history_for_execution(exe, ds_name=ds_name, project_id=project_id, username=username)
        else:
            # 还在跑,挂在 exe 上当 metadata,poll 路径里读取并消费
            exe.__dict__["_history_ctx"] = (ds_name, project_id, username)

    return _execution_envelope(exe)


@router.get("/api/sql-workbench/executions/{execution_id}")
def get_execution_status(
    execution_id: str,
    current: User = Depends(require_role("editor")),
) -> dict[str, Any]:
    exe = get_execution(execution_id)
    if exe is None:
        raise HTTPException(status_code=404, detail="execution 不存在或已过期")
    if exe.user_id != current.id:
        raise HTTPException(status_code=403, detail="无权查看他人的 execution")
    # 检测 worker 完成且 history 还没 append → append 一次
    if exe.status != "running":
        ctx = exe.__dict__.get("_history_ctx")
        if ctx and not exe.__dict__.get("_already_history"):
            exe.__dict__["_already_history"] = True
            ds_name, project_id, username = ctx
            _append_history_for_execution(exe, ds_name=ds_name, project_id=project_id, username=username)
    return _execution_envelope(exe)


@router.post("/api/sql-workbench/executions/{execution_id}/cancel")
def cancel_execution(
    execution_id: str,
    current: User = Depends(require_role("editor")),
) -> dict[str, Any]:
    ok, reason = request_cancel(execution_id, user_id=current.id)
    if not ok:
        # execution 不存在/无权 → 404;状态不对 → 409
        if "不存在" in reason:
            raise HTTPException(status_code=404, detail=reason)
        if "无权" in reason:
            raise HTTPException(status_code=403, detail=reason)
        raise HTTPException(status_code=409, detail=reason)
    return {"ok": True, "execution_id": execution_id, "cancel_requested": True}


# ─── format (v0.2) ─────────────────────────────────────────────────────────

class FormatRequest(BaseModel):
    datasource_id: str = ""   # 可选,空表示走默认 mysql 方言
    sql: str = Field(..., min_length=1)


@router.post("/api/sql-workbench/format")
def format_endpoint(
    payload: FormatRequest = Body(...),
    current: User = Depends(require_role("editor")),
) -> dict[str, Any]:
    db_type = ""
    if payload.datasource_id:
        ds = require_datasource_access(current, payload.datasource_id)
        db_type = getattr(ds.db_type, "value", str(ds.db_type or ""))
    result = _format_sql(payload.sql, db_type=db_type)
    if not result.success:
        # 200 + success=false envelope —— 前端能优雅展示错误而不覆盖原 SQL
        return {
            "success": False,
            "formatted_sql": "",
            "dialect": result.dialect,
            "error": result.error,
        }
    return {
        "success": True,
        "formatted_sql": result.formatted_sql,
        "dialect": result.dialect,
    }


# ─── explain (v0.2) ────────────────────────────────────────────────────────

class ExplainRequest(BaseModel):
    datasource_id: str = Field(..., min_length=1)
    sql: str = Field(..., min_length=1)


@router.post("/api/sql-workbench/explain")
def explain_endpoint(
    payload: ExplainRequest = Body(...),
    current: User = Depends(require_role("editor")),
) -> dict[str, Any]:
    ds = require_datasource_access(current, payload.datasource_id)
    if not getattr(ds, "allow_select", True):
        raise HTTPException(status_code=403, detail=f"数据源 {ds.name} 已禁用 SELECT")
    result = _explain_sql(ds, payload.sql)
    return {
        "success": result.success,
        "dialect": result.dialect,
        "columns": result.columns,
        "rows": result.rows,
        "explain_sql": result.explain_sql,
        "elapsed_ms": result.elapsed_ms,
        "unsupported": result.unsupported,
        "error": result.error,
    }


# ─── history ───────────────────────────────────────────────────────────────

@router.get("/api/sql-workbench/history")
def list_history(
    datasource_id: str = Query(default=""),
    limit: int = Query(default=100, ge=1, le=1000),
    current: User = Depends(require_role("editor")),
) -> dict[str, Any]:
    # 跟 console 一致:只看自己的历史
    items = sql_workbench_store.list_history(
        owner_user_id=current.username,
        datasource_id=datasource_id,
        limit=limit,
    )
    return {"items": [h.model_dump(mode="json") for h in items]}


# ─── metadata (Phase 3 真实接 datasource_introspect / sqlide.metadata) ────

from app.sqlide import metadata as _metadata


def _metadata_error(exc: Exception) -> dict[str, Any]:
    """统一 200 + items=[] + error 字段,前端能优雅 fallback。"""
    return {"items": [], "error": str(exc)}


@router.get("/api/sql-workbench/metadata/schemas")
def list_schemas(
    datasource_id: str = Query(..., min_length=1),
    current: User = Depends(require_role("editor")),
) -> dict[str, Any]:
    ds = require_datasource_access(current, datasource_id)
    try:
        items = _metadata.list_schemas(ds)
    except Exception as exc:
        logger.warning("list_schemas failed: %s", exc)
        return _metadata_error(exc)
    return {"items": items}


@router.get("/api/sql-workbench/metadata/tables")
def list_tables(
    datasource_id: str = Query(..., min_length=1),
    schema: str = Query(default=""),
    current: User = Depends(require_role("editor")),
) -> dict[str, Any]:
    ds = require_datasource_access(current, datasource_id)
    try:
        items = _metadata.list_tables(ds, schema=schema)
    except Exception as exc:
        logger.warning("list_tables failed: %s", exc)
        return _metadata_error(exc)
    return {"items": items}


@router.get("/api/sql-workbench/metadata/columns")
def list_columns(
    datasource_id: str = Query(..., min_length=1),
    table: str = Query(..., min_length=1),
    schema: str = Query(default=""),
    current: User = Depends(require_role("editor")),
) -> dict[str, Any]:
    ds = require_datasource_access(current, datasource_id)
    try:
        items = _metadata.list_columns(ds, table=table, schema=schema)
    except Exception as exc:
        logger.warning("list_columns failed: %s", exc)
        return _metadata_error(exc)
    return {"items": items}
