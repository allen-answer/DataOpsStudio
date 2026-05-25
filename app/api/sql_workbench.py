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

from app.api._authz import require_datasource_access
from app.models import User
from app.services.auth import require_role
from app.sqlide.executor import execute_sql
from app.sqlide.models import (
    ConsoleCreate,
    ConsoleUpdate,
    ExecuteRequest,
    ExecuteResponse,
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


# ─── execute ───────────────────────────────────────────────────────────────

@router.post("/api/sql-workbench/execute")
def execute(
    payload: ExecuteRequest = Body(...),
    current: User = Depends(require_role("editor")),
) -> dict[str, Any]:
    ds = require_datasource_access(current, payload.datasource_id)

    # datasource.allow_select fail-safe:默认 True,但 admin 可显式关掉(防 prod 误查)
    if not getattr(ds, "allow_select", True):
        raise HTTPException(
            status_code=403,
            detail=f"数据源 {ds.name} 已禁用 SELECT(allow_select=false)",
        )

    response = execute_sql(ds, payload.sql, max_rows=payload.max_rows)

    # 不管成败都落 history(失败也要追踪) —— ring buffer cap 防膨胀
    from app.sqlide.models import HistoryEntry
    history_entry = HistoryEntry(
        id=uuid.uuid4().hex,
        datasource_id=ds.id,
        datasource_name=ds.name,
        sql=payload.sql,
        executed_by=current.username,
        project_id=ds.project_id or "",
        executed_at=_now(),
        success=response.success,
        elapsed_ms=response.elapsed_ms,
        row_count=response.row_count,
        truncated=response.truncated,
        error=response.error,
    )
    try:
        sql_workbench_store.append_history(history_entry)
    except Exception:  # pragma: no cover —— history 落盘失败不阻塞用户查询
        logger.exception("history append failed")

    return response.model_dump(mode="json")


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
