"""Slow SQL analyze endpoint(Phase 12 切片 6 + 8, Phase 14 P1-2 plan history + diff)。

- POST /api/slow-sql/analyze        EXPLAIN + 规则推断(切片 6),Phase 14 自动落 plan history
- POST /api/slow-sql/enrich         plan + rule issues → LLM 复核 + 补漏 + 覆盖率(切片 8)
- GET  /api/slow-sql/plan-history   拿同 sql_hash 最近 N 条 plan(Phase 14)
- GET  /api/slow-sql/plan-diff      算两次 plan 的结构化差异(Phase 14)
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api._authz import require_datasource_access
from app.models import User
from app.services.auth import require_role
from app.services import plan_history
from app.services.operation_policy import Operation, assert_operation_allowed
from app.services.slow_sql import SlowSqlError, analyze_sql, enrich_via_ai


# slow-sql 跑 EXPLAIN / LLM 复核，编辑级权限（执行 SQL + 烧 token）。
router = APIRouter(dependencies=[Depends(require_role("editor"))])


class SlowSqlRequest(BaseModel):
    sql: str = Field(..., min_length=1)
    datasource_id: str = Field(..., min_length=1)
    max_plan_rows: int = Field(default=100, ge=1, le=1000)
    # Phase 14 P1-2:打 history 标签便于按 scenario 维度拉历史
    scenario_id: str = ""
    workload_name: str = ""
    save_history: bool = True  # 默认存,改写迭代时给 plan-diff 用


class SlowSqlEnrichRequest(BaseModel):
    sql: str = Field(..., min_length=1)
    plan: list[dict[str, Any]] = Field(default_factory=list)
    issues: list[dict[str, Any]] = Field(default_factory=list)
    suggestions: list[dict[str, Any]] = Field(default_factory=list)
    expected_optimizations: list[str] = Field(default_factory=list)
    dialect: str = Field(default="mysql")


@router.post("/api/slow-sql/analyze")
def analyze(
    payload: SlowSqlRequest = Body(...),
    current: User = Depends(require_role("editor")),
) -> dict[str, Any]:
    # 项目级授权：editor 不能拿别的项目的 datasource_id 跑 EXPLAIN 反查 plan。
    ds = require_datasource_access(current, payload.datasource_id)

    # Phase 14 #3: operation policy 强制 — environment + allow_* flag 矩阵决策
    # 按 db_type 路由到对应 Operation,prod ds 未开 allow_* 立刻 OperationDenied 403
    db_type = (ds.db_type.value or "").lower()
    if db_type == "mysql":
        op = Operation.SQL_EXPLAIN_MYSQL
    elif db_type == "dm":
        op = Operation.SQL_EXPLAIN_DM
    elif db_type == "oracle":
        op = Operation.SQL_EXPLAIN_ORACLE_PLAN_TABLE
    else:
        raise HTTPException(
            status_code=400,
            detail=f"slow-sql analyze 暂支持 mysql / oracle / dm;got {ds.db_type.value}",
        )
    assert_operation_allowed(
        current, ds, op,
        context={"sql_hash": plan_history.sql_hash(payload.sql)},
    )

    try:
        result = analyze_sql(
            payload.datasource_id,
            payload.sql,
            max_plan_rows=payload.max_plan_rows,
        )
    except SlowSqlError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        # sql_guard.validate_readonly_sql 抛 ValueError
        raise HTTPException(status_code=400, detail=f"sql validation failed: {exc}") from exc
    # Phase 14 P1-2:自动落 history 给 plan-diff 用。失败 best-effort 吞,
    # 不阻塞 analyze 主结果返回。
    history_id = None
    if payload.save_history:
        try:
            history_id = plan_history.save_plan(
                datasource_id=payload.datasource_id,
                dialect=str(result.get("dialect") or ""),
                sql_text=payload.sql,
                plan=result.get("plan") or [],
                issues=result.get("issues") or [],
                suggestions=result.get("suggestions") or [],
                scenario_id=payload.scenario_id,
                workload_name=payload.workload_name,
            )
        except Exception:  # noqa: BLE001
            pass
    result["history_id"] = history_id
    result["sql_hash"] = plan_history.sql_hash(payload.sql)
    return result


@router.get("/api/slow-sql/plan-history")
def plan_history_list(
    datasource_id: str = Query(""),
    sql_hash: str = Query(""),
    scenario_id: str = Query(""),
    workload_name: str = Query(""),
    limit: int = Query(10, ge=1, le=100),
    current: User = Depends(require_role("editor")),
) -> dict[str, Any]:
    """Phase 14 #3 收口:plan-history **只**支持 datasource_id + sql_hash 模式。

    旧 scenario_id-only 模式(不绑 datasource)已禁用 — 它不带项目级授权检查,
    editor 可能拉到别项目的 plan(跨项目泄露)。如需按 scenario 维度查询,可
    指定 scenario_id 当筛选 + 必须同时给 datasource_id + sql_hash 走授权路径。

    item 含 plan / issues / suggestions(已 JSON 解析)。
    """
    if not (datasource_id and sql_hash):
        raise HTTPException(
            status_code=400,
            detail=(
                "plan-history 现要求同时提供 datasource_id + sql_hash;"
                "scenario_id-only 模式已禁用以防跨项目 plan 泄露。"
                "如需按 scenario 维度浏览,先在 /sql-diagnosis 或 /scenario-lab 跑一次 analyze "
                "拿 sql_hash,再用 (datasource_id, sql_hash) 查 history。"
            ),
        )
    require_datasource_access(current, datasource_id)
    items = plan_history.list_plans_for_sql(datasource_id, sql_hash, limit=limit)
    # scenario_id + workload_name 仍可作为 client-side 过滤的筛选条件(后端
    # list_plans_for_sql 返结果里 item 自带 scenario_id / workload_name)
    if scenario_id:
        items = [it for it in items if str(it.get("scenario_id") or "") == scenario_id]
    if workload_name:
        items = [it for it in items if str(it.get("workload_name") or "") == workload_name]
    return {"items": items}


@router.get("/api/slow-sql/plan-diff")
def plan_diff(
    plan_a_id: int = Query(..., description="较旧 plan history id"),
    plan_b_id: int = Query(..., description="较新 plan history id"),
    current: User = Depends(require_role("editor")),
) -> dict[str, Any]:
    """对两条历史 plan 算结构化 diff(b 是新的 / a 是老的)。

    返:max-rows 改善 / type 变化 / Extra 变化 / issues 修复 / 新引入 issues。
    """
    a = plan_history.get_plan(plan_a_id)
    b = plan_history.get_plan(plan_b_id)
    if a is None or b is None:
        raise HTTPException(status_code=404, detail="plan_a_id 或 plan_b_id 不存在")
    # 权限:两条 plan 涉及的 datasource 都要 editor 有权
    require_datasource_access(current, a.get("datasource_id") or "")
    if b.get("datasource_id") and b.get("datasource_id") != a.get("datasource_id"):
        require_datasource_access(current, b.get("datasource_id") or "")
    return {
        "plan_a": {"id": a["id"], "ts": a["ts"], "sql_text": a.get("sql_text", "")},
        "plan_b": {"id": b["id"], "ts": b["ts"], "sql_text": b.get("sql_text", "")},
        "diff": plan_history.diff_plans(a, b),
    }


@router.post("/api/sql-diagnosis/preflight")
def sql_diagnosis_preflight(
    payload: dict[str, Any] = Body(...),
    current: User = Depends(require_role("editor")),
) -> dict[str, Any]:
    """Phase 14 #3 alias — /api/sql/preflight 同语义,挂在 sql-diagnosis namespace。

    body: {sql, dialect?, key_columns?, max_rows?, stream_compare?, datasource_id?}
    返回:assess_sql 标准 SQLPreflightDecision 形状。

    用于新 /sql-diagnosis 前端在 analyze 前先调,blocking=true 时阻止用户继续。
    不连数据库,prod 也安全。
    """
    from app.services.sql_preflight import assess_sql
    sql = str(payload.get("sql") or "")
    if not sql.strip():
        raise HTTPException(status_code=400, detail="sql is required")
    raw_keys = payload.get("key_columns") or []
    if isinstance(raw_keys, str):
        key_columns = [c.strip() for c in raw_keys.split(",") if c.strip()]
    else:
        key_columns = [str(c) for c in raw_keys if str(c).strip()]
    dialect_field = str(payload.get("dialect") or "")
    # 如果传了 datasource_id,验权 + 用 ds.db_type 校正 dialect
    datasource_id = str(payload.get("datasource_id") or "").strip()
    if datasource_id:
        ds = require_datasource_access(current, datasource_id, detail="无权对此数据源做 preflight")
        if not dialect_field:
            dialect_field = ds.db_type.value
    try:
        max_rows = int(payload.get("max_rows") or 100_000)
    except (TypeError, ValueError):
        max_rows = 100_000
    decision = assess_sql(
        sql=sql,
        dialect=dialect_field,
        key_columns=key_columns,
        mode="compare",
        max_rows=max_rows,
        stream_compare=bool(payload.get("stream_compare")),
    )
    return decision.model_dump()


@router.post("/api/slow-sql/enrich")
def enrich(payload: SlowSqlEnrichRequest = Body(...)) -> dict[str, Any]:
    """AI 增强：LLM 复核规则 issues + 补漏 + 给出 DDL + 对比 expected 覆盖率。

    provider 关闭时返回 200 + ok=False + error 文案（不抛 4xx，避免普通用户
    在没配 AI 时误以为接口坏了）。
    """
    result = enrich_via_ai(
        sql=payload.sql,
        plan=payload.plan,
        issues=payload.issues,
        suggestions=payload.suggestions,
        expected_optimizations=payload.expected_optimizations,
        dialect=payload.dialect,
    )
    return asdict(result)
