"""Phase 10 第 3 项：/api/lineage/graph/* —— 服务端血缘子图查询。

让前端从"一次性 report 拿全图本地裁剪"演进到"按 asset 切片增量加载"。

v0（stateless POST）：caller 提供 graph_edges（适合"已经在屏幕上的报告，
让我聚焦某个节点"场景）。

v1（GET + 全局索引）：server 维护从最近 N 个 workflow_run 聚合的全局
lineage 索引，前端按 asset_id 直接查（适合"我搜了用户表，给我看它的图"
场景）。索引是 lazy / TTL 5min / workflow_run 数量变化触发失效，纯内存。
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.models import User
from app.services.auth import require_role
from app.services.lineage_graph_query import bfs_subgraph
from app.services.lineage_index import get_lineage_index


router = APIRouter()


Direction = Literal["upstream", "downstream", "both"]


class SubgraphRequest(BaseModel):
    asset_id: str = Field(..., min_length=1, description="BFS 锚点表名")
    direction: Direction = "both"
    depth: int = Field(default=1, ge=0, le=10)
    graph_edges: list[dict[str, Any]] = Field(default_factory=list, description="lineage analyze 出来的 graph_edges")
    table_roles: list[dict[str, Any]] = Field(default_factory=list, description="可选：节点 role 元数据，让 role_filter 生效")
    edge_types: list[str] | None = None
    confidences: list[str] | None = None
    role_filter: str | None = Field(default=None, description="仅保留 primary_role == role_filter 的节点；锚点不受限")
    max_nodes: int = Field(default=2000, ge=1, le=10000)


class SubgraphStats(BaseModel):
    total_nodes: int
    total_edges: int
    depth_reached: int
    truncated: bool
    filtered_edges: int


class SubgraphResponse(BaseModel):
    asset_id: str
    direction: Direction
    depth: int
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    stats: SubgraphStats


@router.get("/api/lineage/graph", response_model=SubgraphResponse)
def lineage_graph_query_api(
    asset_id: str = Query(..., min_length=1, description="BFS 锚点表名"),
    direction: Direction = Query("both"),
    depth: int = Query(1, ge=0, le=10),
    edge_types: list[str] | None = Query(None, description="保留指定 edge_type 的边"),
    confidences: list[str] | None = Query(None, description="保留 confidence ∈ allowed_set 的边"),
    role_filter: str | None = Query(None, description="仅保留 primary_role 命中的节点；锚点不受限"),
    max_nodes: int = Query(2000, ge=1, le=10000),
) -> dict[str, Any]:
    """v1：直接查全局 lineage 索引（最近 50 workflow_run 聚合，TTL 300s）。

    跟 POST /api/lineage/graph/subgraph 的区别：caller 不用提供 graph_edges，
    server 从持久化的 workflow_run 拉数据。适合"我搜了用户表，给我看它的图"
    这类不在意当前 session 状态的查询。
    """
    index = get_lineage_index()
    try:
        return index.query_subgraph(
            asset_id,
            direction=direction,
            depth=depth,
            edge_types=edge_types,
            confidences=confidences,
            role_filter=role_filter,
            max_nodes=max_nodes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/lineage/graph/stats")
def lineage_graph_stats_api() -> dict[str, Any]:
    """全局索引统计：表数 / 边数 / 来源 run 数 / 最后构建时间。"""
    return get_lineage_index().stats()


@router.post("/api/lineage/graph/refresh")
def lineage_graph_refresh_api(_: User = Depends(require_role("admin"))) -> dict[str, Any]:
    """显式失效全局索引 —— admin 用，下次 query 触发 rebuild。"""
    index = get_lineage_index()
    index.invalidate()
    return {"ok": True, "stats": index.stats()}


@router.post("/api/lineage/graph/subgraph", response_model=SubgraphResponse)
def lineage_subgraph_api(payload: SubgraphRequest = Body(...)) -> dict[str, Any]:
    """从 graph_edges 切出以 asset_id 为锚点的子图。

    用法举例（前端把分析结果手里有 + 用户点节点想聚焦）：
    ```
    POST /api/lineage/graph/subgraph
    {
      "asset_id": "ods.t_users",
      "direction": "downstream",
      "depth": 2,
      "graph_edges": [...],   // 当前分析的 result.graph_edges
      "table_roles": [...]    // 可选，让 role_filter 生效
    }
    ```
    """
    try:
        return bfs_subgraph(
            edges=payload.graph_edges,
            asset_id=payload.asset_id,
            direction=payload.direction,
            depth=payload.depth,
            edge_types=payload.edge_types,
            confidences=payload.confidences,
            table_roles=payload.table_roles,
            role_filter=payload.role_filter,
            max_nodes=payload.max_nodes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
