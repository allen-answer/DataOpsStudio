"""Phase 10 第 2 项：/api/search 端点 —— 跨资产全局搜索。

替代当前"图内 Ctrl+F"的孤立查找，把 datasource / task / workflow / history /
lineage script 当一类资产统一搜索。这是 DataHub / Atlan 平台的核心能力之一
（"搜用户表 → 所有引用的 ETL / 报表一击命中"）。
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.services.search import search as run_search


router = APIRouter()


SearchKind = Literal["datasource", "task", "workflow", "history", "lineage_script"]


class SearchHit(BaseModel):
    kind: SearchKind
    id: str
    name: str
    snippet: str = ""
    match_path: str = ""
    score: int = Field(default=0, ge=0)
    project_id: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchResponse(BaseModel):
    query: str
    total: int = Field(..., ge=0)
    hits: list[SearchHit] = Field(default_factory=list)
    by_kind: dict[str, int] = Field(default_factory=dict)


@router.get("/api/search", response_model=SearchResponse)
def search_api(
    q: str = Query(..., min_length=1, description="搜索关键词，多 token 用空格分隔（AND 语义）"),
    kinds: list[SearchKind] | None = Query(
        None,
        description="限定类型；不传 = 全部 5 类（datasource / task / workflow / history / lineage_script）",
    ),
    limit: int = Query(50, ge=1, le=500),
    project_id: str = Query("", description="项目空间过滤；空 = 不过滤"),
) -> dict[str, Any]:
    return run_search(q, kinds=kinds, limit=limit, project_id=project_id)
