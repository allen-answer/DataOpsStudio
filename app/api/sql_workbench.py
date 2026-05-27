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
from fastapi.responses import FileResponse
from pathlib import Path as _Path

from pydantic import BaseModel, Field

from app.api._authz import require_datasource_access
from app.models import User
from app.services.auth import require_role
from app.sqlide.executor import execute_sql
from app.sqlide import sql_hints as _sql_hints
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

    v0.5+:加 cancel_reason / timeout_seconds 给前端用("已超时取消" vs "已取消")。
    """
    env: dict[str, Any] = {
        "execution_id": exe.id,
        "status": exe.status,
        "cancel_requested": exe.cancel_requested,
        "timeout_seconds": getattr(exe, "timeout_seconds", None),
    }
    cancel_reason = getattr(exe, "cancel_reason", "")
    if cancel_reason:
        env["cancel_reason"] = cancel_reason
    if exe.result is not None:
        env.update(exe.result.model_dump(mode="json"))
    elif exe.error:
        env["error"] = exe.error
        env["success"] = False
    elif exe.status == "cancelled":
        # cancelled 时无 result 也无 error,给前端兜底的 error 文案
        env["success"] = False
        env["error"] = "已超时取消" if cancel_reason == "timeout" else "已取消"
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
        # v0.5:cancelled 时把 reason 一起带进 history,user / timeout 两种用户能区分
        if exe.status == "cancelled":
            reason = getattr(exe, "cancel_reason", "") or "user"
            error = f"cancelled ({reason})"
        else:
            error = exe.error
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

    # 提交执行(线程池跑),sync_wait 内能完成则直接返 success;否则返 pending/running + execution_id
    # P1-3:同用户 × 同数据源并发上限 —— start_execution 失败时返 429,不打 traceback
    from app.sqlide.runtime import QueryConcurrencyExceeded
    try:
        exe = start_execution(
            user_id=current.id,
            datasource=ds,
            sql=payload.sql,
            max_rows=payload.max_rows,
            console_id=payload.console_id,
            timeout_seconds=payload.timeout_seconds,
        )
    except QueryConcurrencyExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from None
    # 注册 on_finish:这里手动 wrap —— start_execution 没接 callback,我们在
    # _execution_envelope 返回后,若 status 还是 running,前端会 poll 拿状态;
    # history 写入由 cancel/poll 路径里检测 finished 时触发。但更稳是 worker
    # 完成时自己写 history —— 因 worker 完成时 caller 已没 ds_name/username
    # 上下文。把 callback 改成显式 on-finish 注入更优雅:
    from app.sqlide import runtime as _runtime
    with _runtime._lock:  # type: ignore[attr-defined]
        # 若已经 done(快查询在 sync_wait 内完成),直接 append history
        # v0.5:status 闭集改 pending/running/success/failed/cancelled。
        # 兼容老 "done"(理论上 v0.5 后不再产生,留作 paranoid 兜底)。
        if exe.status in ("success", "done", "failed", "cancelled"):
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
    # 终态判断:不在 pending/running 即已完成
    if exe.status not in ("pending", "running"):
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


# ─── export (v0.5+) ────────────────────────────────────────────────────────


class ExportRequest(BaseModel):
    datasource_id: str = Field(..., min_length=1)
    sql: str = Field(..., min_length=1)
    format: str = Field(..., description="csv | excel | json | sql")
    title: str = Field(default="", max_length=80)
    max_rows: int = Field(default=100_000, ge=1, le=1_000_000)
    # 若服务端检测到"SELECT * 无 WHERE 无 LIMIT"等高风险全表扫描,首次请求返
    # requires_confirmation=True + warnings,前端弹确认后带这个 flag 重提才真正执行
    confirm_full_scan: bool = Field(default=False)


_EXPORT_MIME = {
    "csv": "text/csv; charset=utf-8",
    "excel": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "json": "application/json",
    "sql": "text/plain; charset=utf-8",
}


@router.post("/api/sql-workbench/export")
def submit_export(
    payload: ExportRequest = Body(...),
    current: User = Depends(require_role("editor")),
) -> dict[str, Any]:
    """提交一次结果导出。后台异步跑 SELECT + 写文件 → registry 状态机:
    pending → running → success/failed。短同步 sync_wait 内能完成则直接返
    success(下载 URL 已就绪);大结果返 pending/running,前端 poll。

    project_id 校验通过 require_datasource_access — 无权用该 ds 的用户连
    submit 都进不来。
    """
    if payload.format not in _sql_export.SUPPORTED_FORMATS:
        raise HTTPException(status_code=400, detail=f"unsupported format: {payload.format}")
    ds = require_datasource_access(current, payload.datasource_id)
    if not getattr(ds, "allow_select", True):
        raise HTTPException(status_code=403, detail=f"数据源 {ds.name} 已禁用 SELECT")

    # Full-scan 风险检测 —— SELECT * 无 WHERE 无 LIMIT 等情况首次请求要求二次确认,
    # 避免用户手滑导出全表(几千万行 CSV 把磁盘 + 内存爆掉)。前端弹框确认后带
    # confirm_full_scan=true 重提即可。
    from app.utils.sql_safety import analyze_safety  # 局部 import 避免循环
    safety = analyze_safety(payload.sql)
    if safety.risk_level == "warn" and not payload.confirm_full_scan:
        logger.warning(
            "export blocked pending confirmation user=%s ds=%s warnings=%s",
            current.id, ds.id, safety.warnings,
        )
        return {
            "status": "requires_confirmation",
            "requires_confirmation": True,
            "warnings": safety.warnings,
            "risk_level": safety.risk_level,
            "message": "检测到全表扫描风险,请确认是否继续导出",
        }

    ex = _sql_export.start_export(
        user_id=current.id,
        datasource=ds,
        sql=payload.sql,
        fmt=payload.format,  # type: ignore[arg-type]
        title=payload.title,
        max_rows=payload.max_rows,
    )
    if ex.status == "success":
        # 额外审计行 —— 给 admin 跟踪谁导了多少数据
        logger.info(
            "sql export complete export_id=%s user=%s ds=%s fmt=%s rows=%d bytes=%d",
            ex.id, current.id, ds.id, ex.format, ex.row_count, ex.file_size,
        )
    return ex.to_envelope()


@router.get("/api/sql-workbench/export/{export_id}")
def get_export_status(
    export_id: str,
    current: User = Depends(require_role("editor")),
) -> dict[str, Any]:
    ex = _sql_export.get_export(export_id)
    if ex is None:
        raise HTTPException(status_code=404, detail="export 不存在或已过期")
    if ex.user_id != current.id:
        raise HTTPException(status_code=403, detail="无权查看他人的 export")
    return ex.to_envelope()


@router.get("/api/sql-workbench/export/{export_id}/download")
def download_export(
    export_id: str,
    current: User = Depends(require_role("editor")),
):
    ex = _sql_export.get_export(export_id)
    if ex is None:
        raise HTTPException(status_code=404, detail="export 不存在或已过期")
    if ex.user_id != current.id:
        raise HTTPException(status_code=403, detail="无权下载他人的 export")
    if ex.status != "success":
        raise HTTPException(status_code=409, detail=f"export 状态 {ex.status},不能下载")
    p = _Path(ex.file_path)
    if not p.exists():
        raise HTTPException(status_code=410, detail="导出文件已被清理")
    logger.info(
        "sql export download export_id=%s user=%s file=%s",
        ex.id, current.id, ex.file_name,
    )
    return FileResponse(
        p,
        media_type=_EXPORT_MIME.get(ex.format, "application/octet-stream"),
        filename=ex.file_name,
    )


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


# ─── expand SELECT * (v0.7) ────────────────────────────────────────────────

class ExpandStarRequest(BaseModel):
    datasource_id: str = Field(..., min_length=1)
    sql: str = Field(..., min_length=1)


@router.post("/api/sql-workbench/expand-star")
def expand_star_endpoint(
    payload: ExpandStarRequest = Body(...),
    current: User = Depends(require_role("editor")),
) -> dict[str, Any]:
    """把 SQL 里的 SELECT * / SELECT t.* 展开成显式列名.

    columns 来源:
    - 先查 metadata cache(columns-bulk scope per schema → fallback columns scope per table)
    - 都 miss 时直接拉 DB(list_columns 走 information_schema / all_tab_columns)
    - 失败的表在 response.warnings 列出,*** 保留不动让用户感知 ***
    """
    from app.services.sql_tools import expand_select_star
    from app.sqlide import metadata as _md
    from app.sqlide import metadata_cache as _meta_cache_mod

    ds = require_datasource_access(current, payload.datasource_id)
    cache = _meta_cache_mod.load_cache(payload.datasource_id)
    bulk_scope = cache.get("columns-bulk") or {}
    bulk_items = bulk_scope.get("items") if isinstance(bulk_scope, dict) else None

    def _lookup(schema: str, table: str) -> list[str] | None:
        # 优先 columns-bulk cache(快)
        if isinstance(bulk_items, dict) and schema:
            schema_dict = bulk_items.get(schema) or bulk_items.get(schema.upper()) or bulk_items.get(schema.lower())
            if isinstance(schema_dict, dict):
                cols = schema_dict.get(table) or schema_dict.get(table.upper()) or schema_dict.get(table.lower())
                if isinstance(cols, list) and cols:
                    return [str(c.get("name") or c) for c in cols if c]
        # cache miss → 真去 DB 拉
        try:
            cols = _md.list_columns(ds, table=table, schema=schema)
            if cols:
                return [str(c.get("name", "")) for c in cols if c.get("name")]
        except Exception:
            pass
        return None

    dialect_name = getattr(ds.db_type, "value", str(ds.db_type or "")).lower()
    sqlglot_dialect = {"dm": "oracle", "ob_oracle": "oracle", "ob_mysql": "mysql"}.get(dialect_name, dialect_name)

    rewritten, warnings = expand_select_star(
        payload.sql,
        dialect=sqlglot_dialect or None,
        columns_lookup=_lookup,
    )
    return {
        "sql": rewritten,
        "changed": rewritten != payload.sql,
        "warnings": warnings,
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
    # v0.5:无论 success / unsupported / error,都附 4 条静态文本 hints —— 即使
    # plan 跑不通(Oracle/DM unsupported),用户也能看到"SELECT * 提醒"等。
    hints = _sql_hints.lint_sql(payload.sql)
    return {
        "success": result.success,
        "dialect": result.dialect,
        "columns": result.columns,
        "rows": result.rows,
        "explain_sql": result.explain_sql,
        "elapsed_ms": result.elapsed_ms,
        "unsupported": result.unsupported,
        "error": result.error,
        "hints": hints,
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


# ─── SQL 统计 (P2-2) —— admin 看慢查询 / 失败排行榜 ───────────────────────────

@router.get("/api/sql-workbench/stats/slow")
def list_slow_sql(
    limit: int = Query(default=20, ge=1, le=200),
    metric: str = Query(default="avg_ms", description="avg_ms / max_ms / total_ms / count"),
    min_count: int = Query(default=1, ge=1, le=10_000),
    current: User = Depends(require_role("admin")),
) -> dict[str, Any]:
    """Top N 慢 SQL —— admin only。

    metric:
    - avg_ms: 平均耗时(默认,反映 SQL 模板本身)
    - max_ms: 单次最长耗时
    - total_ms: 累计耗时(高频 × 中等耗时也会上榜)
    - count: 执行次数(高频 SQL)
    """
    from app.services import sql_stats
    return {
        "items": sql_stats.top_slow(limit=limit, metric=metric, min_count=min_count),
        "summary": sql_stats.get_summary(),
    }


@router.get("/api/sql-workbench/stats/concurrency")
def list_concurrency_snapshot(
    current: User = Depends(require_role("admin")),
) -> dict[str, Any]:
    """当前 in-flight 查询计数(按 user × datasource)—— admin 监控。"""
    from app.services import query_concurrency
    return {"in_flight": query_concurrency.get_in_flight_snapshot()}


# ─── metadata (v0.3 加 cache + refresh + indexes/views/DDL) ──────────────

from app.services import sql_export as _sql_export
from app.sqlide import metadata as _metadata
from app.sqlide import metadata_cache as _meta_cache
from app.sqlide import search as _meta_search


def _cached_or_live(
    ds_id: str,
    scope: str,
    key: str | None,
    live_fn,
    refresh: bool = False,
) -> dict[str, Any]:
    """三段逻辑:
      1. 不强刷 + cache 有命中 → 返 cache,source='cache'
      2. 强刷 / cache miss → 跑 live_fn,成功写回 cache,source='live'
      3. live_fn 失败 + cache 有旧值 → 返旧值,source='stale' + error 字段(stale-while-error)
      4. live_fn 失败 + cache 也空 → source='live' + items=[] + error

    `key=None` 表示该 scope 的 items 是 list(schemas / views_当前schema);
    `key='public'` 表示 items 是 dict,只 pluck 该 key(tables / columns / indexes / views_多schema)。
    """
    cache = _meta_cache.load_cache(ds_id)
    items, fetched_at = _meta_cache.get_scope(cache, scope)  # type: ignore[arg-type]

    if key is None:
        existing = items if isinstance(items, list) else None
    else:
        existing = items.get(key) if isinstance(items, dict) else None

    if not refresh and existing is not None:
        return {"items": existing, "cached_at": fetched_at, "source": "cache"}

    try:
        live_items = live_fn()
    except Exception as exc:
        logger.warning("metadata live fetch ds=%s scope=%s key=%s failed: %s",
                       ds_id, scope, key, exc)
        if existing is not None:
            return {
                "items": existing,
                "cached_at": fetched_at,
                "source": "stale",
                "error": str(exc),
            }
        return {"items": [], "cached_at": None, "source": "live", "error": str(exc)}

    # 成功:写回 cache 时,multi-key 维度要保留同维度其他 key 的旧值
    if key is None:
        new_cache = _meta_cache.save_scope(ds_id, scope, live_items)  # type: ignore[arg-type]
    else:
        merged = dict(items) if isinstance(items, dict) else {}
        merged[key] = live_items
        new_cache = _meta_cache.save_scope(ds_id, scope, merged)  # type: ignore[arg-type]
    fresh_at = new_cache[scope]["fetched_at"] if isinstance(new_cache.get(scope), dict) else None
    return {"items": live_items, "cached_at": fresh_at, "source": "live"}


@router.get("/api/sql-workbench/metadata/schemas")
def list_schemas(
    datasource_id: str = Query(..., min_length=1),
    refresh: bool = Query(default=False),
    current: User = Depends(require_role("editor")),
) -> dict[str, Any]:
    ds = require_datasource_access(current, datasource_id)
    return _cached_or_live(
        datasource_id, "schemas", None,
        lambda: _metadata.list_schemas(ds),
        refresh=refresh,
    )


@router.get("/api/sql-workbench/metadata/tables")
def list_tables(
    datasource_id: str = Query(..., min_length=1),
    schema: str = Query(default=""),
    refresh: bool = Query(default=False),
    # P1-1: 大 schema(几万张表)分页 + 搜索 —— 默认 200 条,客户端要更多自己 offset
    q: str = Query(default="", description="名字模糊过滤(大小写不敏感子串)"),
    offset: int = Query(default=0, ge=0, le=1_000_000),
    limit: int = Query(default=200, ge=1, le=2000),
    current: User = Depends(require_role("editor")),
) -> dict[str, Any]:
    ds = require_datasource_access(current, datasource_id)
    cached = _cached_or_live(
        datasource_id, "tables", schema or "__default__",
        lambda: _metadata.list_tables(ds, schema=schema),
        refresh=refresh,
    )
    items = cached.get("items") or []
    total = len(items)

    # 搜索:对 name / schema 子串匹配(从全集筛,再分页)
    if q:
        needle = q.strip().lower()
        items = [
            t for t in items
            if needle in str(t.get("name", "")).lower()
            or needle in str(t.get("schema", "")).lower()
        ]
    filtered_total = len(items)
    items = items[offset:offset + limit]

    return {
        **cached,
        "items": items,
        "total": total,
        "filtered_total": filtered_total,
        "offset": offset,
        "limit": limit,
    }


@router.get("/api/sql-workbench/metadata/columns")
def list_columns(
    datasource_id: str = Query(..., min_length=1),
    table: str = Query(..., min_length=1),
    schema: str = Query(default=""),
    refresh: bool = Query(default=False),
    current: User = Depends(require_role("editor")),
) -> dict[str, Any]:
    ds = require_datasource_access(current, datasource_id)
    # key 含 schema + table,确保不同 schema 同名表互不污染
    key = f"{schema}.{table}" if schema else table
    return _cached_or_live(
        datasource_id, "columns", key,
        lambda: _metadata.list_columns(ds, table=table, schema=schema),
        refresh=refresh,
    )


@router.get("/api/sql-workbench/metadata/columns-bulk")
def list_columns_bulk(
    datasource_id: str = Query(..., min_length=1),
    schema: str = Query(..., min_length=1, description="schema 必填(无默认 schema 概念)"),
    refresh: bool = Query(default=False),
    current: User = Depends(require_role("editor")),
) -> dict[str, Any]:
    """一次拉一个 schema 全部表的字段(N→1 减压) —— 给前端字段补全做预热。

    response.items 是 `{ "<table_name>": [col,...], ... }` dict 形态(跟单表 columns
    的 list 形态不同),前端按 table 写进各 schemas[i].tables[j].columns。
    """
    ds = require_datasource_access(current, datasource_id)
    return _cached_or_live(
        datasource_id, "columns-bulk", schema,
        lambda: _metadata.list_columns_bulk(ds, schema=schema),
        refresh=refresh,
    )


@router.get("/api/sql-workbench/metadata/indexes")
def list_indexes(
    datasource_id: str = Query(..., min_length=1),
    table: str = Query(..., min_length=1),
    schema: str = Query(default=""),
    refresh: bool = Query(default=False),
    current: User = Depends(require_role("editor")),
) -> dict[str, Any]:
    ds = require_datasource_access(current, datasource_id)
    key = f"{schema}.{table}" if schema else table
    return _cached_or_live(
        datasource_id, "indexes", key,
        lambda: _metadata.list_indexes(ds, table=table, schema=schema),
        refresh=refresh,
    )


@router.get("/api/sql-workbench/metadata/views")
def list_views(
    datasource_id: str = Query(..., min_length=1),
    schema: str = Query(default=""),
    refresh: bool = Query(default=False),
    current: User = Depends(require_role("editor")),
) -> dict[str, Any]:
    ds = require_datasource_access(current, datasource_id)
    return _cached_or_live(
        datasource_id, "views", schema or "__default__",
        lambda: _metadata.list_views(ds, schema=schema),
        refresh=refresh,
    )


@router.get("/api/sql-workbench/metadata/table-ddl")
def get_table_ddl(
    datasource_id: str = Query(..., min_length=1),
    table: str = Query(..., min_length=1),
    schema: str = Query(default=""),
    current: User = Depends(require_role("editor")),
) -> dict[str, Any]:
    """表的 CREATE TABLE DDL。Oracle/DM/DB2 暂不支持,返 supported=false。
    不缓存(DDL 内容相对少访问,且文本较长不值得 cache 一份单独 scope)。"""
    ds = require_datasource_access(current, datasource_id)
    try:
        ddl = _metadata.get_table_ddl(ds, table=table, schema=schema)
    except Exception as exc:
        logger.warning("get_table_ddl failed: %s", exc)
        return {"supported": False, "ddl": None, "error": str(exc)}
    if ddl is None:
        return {"supported": False, "ddl": None, "error": "该方言暂不支持 DDL 抽取"}
    return {"supported": True, "ddl": ddl}


@router.get("/api/sql-workbench/metadata/cache-summary")
def cache_summary(
    datasource_id: str = Query(..., min_length=1),
    current: User = Depends(require_role("editor")),
) -> dict[str, Any]:
    """返每 scope 的 fetched_at。前端用来在 UI 标"缓存于 HH:MM"。"""
    require_datasource_access(current, datasource_id)
    return {"datasource_id": datasource_id, "scopes": _meta_cache.cache_summary(datasource_id)}


class _RefreshRequest(BaseModel):
    datasource_id: str = Field(..., min_length=1)
    scope: str = Field(default="all", description="all / schemas / tables / columns / indexes / views")


@router.get("/api/sql-workbench/metadata/search")
def search_objects(
    datasource_id: str = Query(..., min_length=1),
    q: str = Query(..., min_length=1),
    kinds: str = Query(default="", description="逗号分隔:table,column,view;空=全部"),
    limit: int = Query(default=50, ge=1, le=200),
    current: User = Depends(require_role("editor")),
) -> dict[str, Any]:
    """跨 metadata cache 搜 table / column / view。完全 cache-only,搜不出来时
    前端应该提示用户"先展开 schema 触发缓存"。"""
    require_datasource_access(current, datasource_id)
    kind_list = [k.strip() for k in kinds.split(",") if k.strip()] if kinds else None
    items = _meta_search.search_metadata(
        datasource_id, q=q, kinds=kind_list, limit=limit,
    )
    return {"items": items, "query": q, "count": len(items)}


@router.post("/api/sql-workbench/metadata/refresh")
def refresh_metadata(
    payload: _RefreshRequest = Body(...),
    current: User = Depends(require_role("editor")),
) -> dict[str, Any]:
    """手动失效 cache。前端在用户点"刷新"按钮时调,然后用普通 GET ?refresh=true
    重新拉。这里**只清不拉**,避免大数据源刷新阻塞 HTTP 长连接。"""
    require_datasource_access(current, payload.datasource_id)
    if payload.scope == "all":
        _meta_cache.clear_cache(payload.datasource_id, None)
    elif payload.scope in _meta_cache.SCOPES:
        _meta_cache.clear_cache(payload.datasource_id, payload.scope)  # type: ignore[arg-type]
    else:
        raise HTTPException(400, f"unknown scope: {payload.scope}")
    return {"ok": True, "datasource_id": payload.datasource_id, "scope": payload.scope}
