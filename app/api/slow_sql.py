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
    require_datasource_access(current, payload.datasource_id)
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
    """两种查询模式:
    - 给 datasource_id + sql_hash → 拉同 SQL 的最近 N 次 plan(改写迭代 timeline)
    - 给 scenario_id (+ workload_name) → 拉该 scenario 的最近 N 次 plan(场景维度)

    item 含 plan / issues / suggestions(已 JSON 解析)。
    """
    if datasource_id and sql_hash:
        require_datasource_access(current, datasource_id)
        items = plan_history.list_plans_for_sql(datasource_id, sql_hash, limit=limit)
    elif scenario_id:
        # scenario_id 维度的 history 不绑 datasource(同 scenario 可换 ds 跑),
        # 这里仅 editor 即可,具体 plan 详情已经按 datasource 落 history 时校过
        items = plan_history.list_plans_for_scenario(scenario_id, workload_name, limit=limit)
    else:
        raise HTTPException(status_code=400, detail="需要 datasource_id+sql_hash 或 scenario_id")
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
