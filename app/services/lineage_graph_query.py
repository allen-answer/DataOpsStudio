"""Phase 10 第 3 项：服务端血缘子图查询。

给一组 graph_edges（lineage analyze 出来的）+ 锚点 asset_id + 方向 + 深度
+ 过滤条件，server 端 BFS 切出子图返回。让前端不再背 BFS 逻辑（当前
useLineageGraphData composable 在客户端 BFS，1000+ 节点时主线程卡顿），
也为后续接全局索引 / 增量加载 / 持久化 graph store 打底。

MVP（v0）：stateless —— caller 把 graph_edges 提交进来，server 切片返回。
v1 路线（未排期）：接 workflow_run 或全局索引，URL 形如
`?source=workflow_run:<run_id>&asset_id=ods.t_users&depth=2`。

Filters 支持（都可省）：
- role：仅保留与某 role 相关的节点（前提是 caller 提供 table_roles）
- edge_type：保留指定 edge_type 的边（默认全部）
- confidence：保留 confidence ∈ allowed_set 的边
"""
from __future__ import annotations

from collections import deque
from typing import Any, Iterable, Literal


Direction = Literal["upstream", "downstream", "both"]


def _normalize_edge(edge: Any) -> dict[str, Any] | None:
    if not isinstance(edge, dict):
        return None
    src = str(edge.get("source_table") or "").strip()
    tgt = str(edge.get("target_table") or "").strip()
    if not src or not tgt:
        return None
    return edge


def _build_adjacency(edges: list[dict[str, Any]]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    """从 edges 构造正向 / 反向邻接表。"""
    forward: dict[str, list[dict[str, Any]]] = {}
    backward: dict[str, list[dict[str, Any]]] = {}
    for edge in edges:
        src = edge["source_table"]
        tgt = edge["target_table"]
        forward.setdefault(src, []).append(edge)
        backward.setdefault(tgt, []).append(edge)
    return forward, backward


def _passes_edge_filter(
    edge: dict[str, Any],
    *,
    edge_types: set[str] | None,
    confidences: set[str] | None,
) -> bool:
    if edge_types:
        et = str(edge.get("edge_type") or edge.get("type") or "").lower()
        if et and et not in edge_types:
            return False
    if confidences:
        c = str(edge.get("confidence") or "").lower()
        if c and c not in confidences:
            return False
    return True


def bfs_subgraph(
    *,
    edges: list[dict[str, Any]],
    asset_id: str,
    direction: Direction = "both",
    depth: int = 1,
    edge_types: Iterable[str] | None = None,
    confidences: Iterable[str] | None = None,
    table_roles: list[dict[str, Any]] | None = None,
    role_filter: str | None = None,
    max_nodes: int = 5000,
) -> dict[str, Any]:
    """BFS 切子图。

    Args:
        edges: graph_edges 列表（每条 dict 至少有 source_table / target_table）
        asset_id: BFS 锚点表名
        direction: upstream（向上游） / downstream（向下游） / both
        depth: BFS 跳数。0 = 仅返回 asset 节点本身
        edge_types: 仅保留指定 edge_type 的边
        confidences: 仅保留 confidence ∈ allowed_set 的边
        table_roles: 节点 role 元数据（用于 role_filter）
        role_filter: 仅保留 primary_role == role_filter 的节点
        max_nodes: 安全上限，BFS 命中超过此数会截断（truncated=True）

    Returns:
        {
            "asset_id": str,
            "direction": str,
            "depth": int,
            "nodes": list[{"table": str, "primary_role": str | None, ...}],
            "edges": list[edge dict],
            "stats": {
                "total_nodes": int,
                "total_edges": int,
                "depth_reached": int,
                "truncated": bool,
                "filtered_edges": int,
            },
        }
    """
    if depth < 0:
        raise ValueError("depth must be >= 0")
    if max_nodes < 1:
        raise ValueError("max_nodes must be >= 1")

    edge_types_set = {e.lower() for e in edge_types} if edge_types else None
    confidences_set = {c.lower() for c in confidences} if confidences else None
    role_set = {str(r["table"]): r for r in (table_roles or []) if isinstance(r, dict) and r.get("table")}

    # 归一化 + 过滤 edge
    normalized: list[dict[str, Any]] = []
    filtered_count = 0
    for raw in edges or []:
        norm = _normalize_edge(raw)
        if norm is None:
            continue
        if not _passes_edge_filter(norm, edge_types=edge_types_set, confidences=confidences_set):
            filtered_count += 1
            continue
        normalized.append(norm)

    forward, backward = _build_adjacency(normalized)

    # BFS
    visited: set[str] = {asset_id}
    out_edges: list[dict[str, Any]] = []
    frontier: deque[tuple[str, int]] = deque([(asset_id, 0)])
    depth_reached = 0
    truncated = False

    while frontier:
        node, d = frontier.popleft()
        depth_reached = max(depth_reached, d)
        if d >= depth:
            continue

        # 收集本层 outgoing edges（按 direction 决定走 forward / backward）
        if direction in {"downstream", "both"}:
            for edge in forward.get(node, []):
                if edge in out_edges:
                    continue
                neighbor = edge["target_table"]
                out_edges.append(edge)
                if neighbor not in visited:
                    if len(visited) >= max_nodes:
                        truncated = True
                        break
                    visited.add(neighbor)
                    frontier.append((neighbor, d + 1))
            if truncated:
                break

        if direction in {"upstream", "both"}:
            for edge in backward.get(node, []):
                if edge in out_edges:
                    continue
                neighbor = edge["source_table"]
                out_edges.append(edge)
                if neighbor not in visited:
                    if len(visited) >= max_nodes:
                        truncated = True
                        break
                    visited.add(neighbor)
                    frontier.append((neighbor, d + 1))
            if truncated:
                break

    # 节点元数据：从 table_roles 拿 role；缺省按 schema 划层
    nodes: list[dict[str, Any]] = []
    for table in visited:
        role_entry = role_set.get(table)
        node = {"table": table}
        if role_entry:
            node["primary_role"] = role_entry.get("primary_role") or ""
            node["roles"] = role_entry.get("roles") or []
        nodes.append(node)

    # role_filter 后置过滤：节点不命中时，相关 edge 也丢
    if role_filter:
        kept_tables = {n["table"] for n in nodes if n.get("primary_role") == role_filter}
        # 锚点不论 role 都保留（否则查询变空）
        kept_tables.add(asset_id)
        nodes = [n for n in nodes if n["table"] in kept_tables]
        out_edges = [e for e in out_edges if e["source_table"] in kept_tables and e["target_table"] in kept_tables]

    return {
        "asset_id": asset_id,
        "direction": direction,
        "depth": depth,
        "nodes": nodes,
        "edges": out_edges,
        "stats": {
            "total_nodes": len(nodes),
            "total_edges": len(out_edges),
            "depth_reached": depth_reached,
            "truncated": truncated,
            "filtered_edges": filtered_count,
        },
    }


__all__ = ["bfs_subgraph", "Direction"]
