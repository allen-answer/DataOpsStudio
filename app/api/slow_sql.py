"""Slow SQL analyze endpoint（Phase 12 切片 6）。

POST /api/slow-sql/analyze body={sql, datasource_id, max_plan_rows?}
返回 {dialect, explain_sql, plan, issues, suggestions}。

Phase 12 切片 6 MVP：只规则推断，无 LLM。未来加 enrichment 把 plan +
heuristic issues 喂给 provider 生成更准的优化方案 + 对比 scenario
workloads[slow_query].expected_optimizations 做评分。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field

from app.services.slow_sql import SlowSqlError, analyze_sql


router = APIRouter()


class SlowSqlRequest(BaseModel):
    sql: str = Field(..., min_length=1)
    datasource_id: str = Field(..., min_length=1)
    max_plan_rows: int = Field(default=100, ge=1, le=1000)


@router.post("/api/slow-sql/analyze")
def analyze(payload: SlowSqlRequest = Body(...)) -> dict[str, Any]:
    try:
        return analyze_sql(
            payload.datasource_id,
            payload.sql,
            max_plan_rows=payload.max_plan_rows,
        )
    except SlowSqlError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        # sql_guard.validate_readonly_sql 抛 ValueError
        raise HTTPException(status_code=400, detail=f"sql validation failed: {exc}") from exc
