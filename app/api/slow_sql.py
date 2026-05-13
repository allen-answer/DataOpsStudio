"""Slow SQL analyze endpoint（Phase 12 切片 6 + 8）。

- POST /api/slow-sql/analyze    EXPLAIN + 规则推断（切片 6）
- POST /api/slow-sql/enrich     plan + rule issues → LLM 复核 + 补漏 + 覆盖率（切片 8）
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field

from app.services.slow_sql import SlowSqlError, analyze_sql, enrich_via_ai


router = APIRouter()


class SlowSqlRequest(BaseModel):
    sql: str = Field(..., min_length=1)
    datasource_id: str = Field(..., min_length=1)
    max_plan_rows: int = Field(default=100, ge=1, le=1000)


class SlowSqlEnrichRequest(BaseModel):
    sql: str = Field(..., min_length=1)
    plan: list[dict[str, Any]] = Field(default_factory=list)
    issues: list[dict[str, Any]] = Field(default_factory=list)
    suggestions: list[dict[str, Any]] = Field(default_factory=list)
    expected_optimizations: list[str] = Field(default_factory=list)
    dialect: str = Field(default="mysql")


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
