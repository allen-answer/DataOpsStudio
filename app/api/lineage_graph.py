"""Phase 10 第 3 项：/api/lineage/graph/* —— 服务端血缘子图查询。

让前端从"一次性 report 拿全图本地裁剪"演进到"按 asset 切片增量加载"。
当前是 stateless POST（caller 提供 graph_edges）；下一版接 workflow_run /
全局索引后会加 GET 形式（按 run_id + asset_id 服务器查持久化数据）。
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field

from app.services.lineage_graph_query import bfs_subgraph


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
