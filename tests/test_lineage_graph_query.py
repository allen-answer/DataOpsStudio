"""Phase 10 #3：服务端 BFS 子图查询测试。

覆盖：
- BFS 正向 / 反向 / 双向
- depth 0 = 仅锚点；depth=N 切对的层数
- edge_types / confidences / role_filter 过滤
- 锚点不在 graph 时的行为
- max_nodes 截断
- 异常路径（depth < 0 / max_nodes < 1）
- HTTP 端点 /api/lineage/graph/subgraph
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from main import app
from app.services.lineage_graph_query import bfs_subgraph


# ─── fixture：一张小图，3 层 6 节点 ──────────────────────────────────────────
#
#   ods.a ──┐
#           ├──> dwd.x ──> dws.m ──> fct.r
#   ods.b ──┘                ↑
#                            │
#   dim.c ───────────────────┘  (低 confidence)


def _sample_edges():
    return [
        {"source_table": "ods.a", "target_table": "dwd.x", "edge_type": "table", "confidence": "high"},
        {"source_table": "ods.b", "target_table": "dwd.x", "edge_type": "table", "confidence": "high"},
        {"source_table": "dwd.x", "target_table": "dws.m", "edge_type": "table", "confidence": "high"},
        {"source_table": "dim.c", "target_table": "dws.m", "edge_type": "table", "confidence": "low"},
        {"source_table": "dws.m", "target_table": "fct.r", "edge_type": "table", "confidence": "medium"},
    ]


def _sample_roles():
    return [
        {"table": "ods.a", "primary_role": "source_fact", "roles": ["source_fact"]},
        {"table": "ods.b", "primary_role": "source_fact", "roles": ["source_fact"]},
        {"table": "dim.c", "primary_role": "dimension", "roles": ["dimension"]},
        {"table": "dwd.x", "primary_role": "intermediate", "roles": ["intermediate"]},
        {"table": "dws.m", "primary_role": "intermediate", "roles": ["intermediate"]},
        {"table": "fct.r", "primary_role": "target", "roles": ["target"]},
    ]


# ─── 单元 ────────────────────────────────────────────────────────────────────


def test_depth_zero_returns_only_anchor():
    result = bfs_subgraph(edges=_sample_edges(), asset_id="dwd.x", direction="both", depth=0)
    assert result["stats"]["total_nodes"] == 1
    assert result["stats"]["total_edges"] == 0
    assert result["nodes"][0]["table"] == "dwd.x"


def test_downstream_depth_one():
    result = bfs_subgraph(edges=_sample_edges(), asset_id="dwd.x", direction="downstream", depth=1)
    tables = {n["table"] for n in result["nodes"]}
    assert tables == {"dwd.x", "dws.m"}
    assert result["stats"]["total_edges"] == 1


def test_upstream_depth_one():
    result = bfs_subgraph(edges=_sample_edges(), asset_id="dwd.x", direction="upstream", depth=1)
    tables = {n["table"] for n in result["nodes"]}
    assert tables == {"dwd.x", "ods.a", "ods.b"}
    assert result["stats"]["total_edges"] == 2


def test_both_depth_two_full_reach():
    """从 dwd.x 双向 depth=2 应触达整张图。"""
    result = bfs_subgraph(edges=_sample_edges(), asset_id="dwd.x", direction="both", depth=2)
    tables = {n["table"] for n in result["nodes"]}
    assert tables == {"ods.a", "ods.b", "dwd.x", "dim.c", "dws.m", "fct.r"}
    assert result["stats"]["total_edges"] == 5


def test_anchor_not_in_graph_returns_just_anchor():
    """锚点不在 edges 里：返回单点节点，BFS 找不到边。"""
    result = bfs_subgraph(edges=_sample_edges(), asset_id="missing.table", direction="both", depth=2)
    assert result["stats"]["total_nodes"] == 1
    assert result["stats"]["total_edges"] == 0


def test_confidence_filter_keeps_only_allowed():
    """只保留 high confidence 边 → dim.c 的 low 边被滤掉，dws.m → fct.r 的 medium 也被滤掉。"""
    result = bfs_subgraph(
        edges=_sample_edges(), asset_id="dwd.x", direction="both", depth=3,
        confidences=["high"],
    )
    # 仅高置信路径：ods.a/b → dwd.x → dws.m（high）
    # dws.m → fct.r 是 medium 被滤；dim.c → dws.m 是 low 被滤
    tables = {n["table"] for n in result["nodes"]}
    assert tables == {"ods.a", "ods.b", "dwd.x", "dws.m"}
    # 5 条边里 3 条 high → 保留 3
    assert result["stats"]["filtered_edges"] == 2


def test_edge_type_filter():
    edges = _sample_edges() + [
        {"source_table": "dwd.x", "target_table": "fct.alt", "edge_type": "column", "confidence": "high"},
    ]
    result = bfs_subgraph(
        edges=edges, asset_id="dwd.x", direction="downstream", depth=2,
        edge_types=["table"],
    )
    tables = {n["table"] for n in result["nodes"]}
    assert "fct.alt" not in tables  # 被 edge_type 过滤掉
    assert "dws.m" in tables


def test_role_filter_preserves_anchor():
    """role_filter=intermediate：仅保留 intermediate 节点，但锚点（即使不是
    intermediate）不丢。"""
    result = bfs_subgraph(
        edges=_sample_edges(), asset_id="ods.a", direction="downstream", depth=3,
        table_roles=_sample_roles(),
        role_filter="intermediate",
    )
    tables = {n["table"] for n in result["nodes"]}
    # 锚点 ods.a 强保留 + 路径上的 intermediate（dwd.x / dws.m）保留；
    # source_fact ods.b / target fct.r / dimension dim.c 被滤
    assert "ods.a" in tables
    assert "dwd.x" in tables
    assert "dws.m" in tables
    assert "fct.r" not in tables
    assert "dim.c" not in tables


def test_max_nodes_truncation():
    edges = [{"source_table": f"t{i}", "target_table": f"t{i+1}", "edge_type": "table"} for i in range(20)]
    result = bfs_subgraph(edges=edges, asset_id="t0", direction="downstream", depth=20, max_nodes=5)
    assert result["stats"]["truncated"] is True
    assert result["stats"]["total_nodes"] <= 5


def test_invalid_depth_raises():
    with pytest.raises(ValueError):
        bfs_subgraph(edges=[], asset_id="x", depth=-1)


def test_invalid_max_nodes_raises():
    with pytest.raises(ValueError):
        bfs_subgraph(edges=[], asset_id="x", max_nodes=0)


def test_normalized_edge_skips_malformed():
    edges = [
        "not a dict",
        {"source_table": "", "target_table": "x"},
        {"source_table": "a", "target_table": "b", "edge_type": "table"},
    ]
    result = bfs_subgraph(edges=edges, asset_id="a", direction="downstream", depth=1)
    assert result["stats"]["total_edges"] == 1


# ─── HTTP 端点 ───────────────────────────────────────────────────────────────


# `client` fixture 来自 conftest.py（admin-authed）。


def test_http_subgraph_returns_envelope(client):
    response = client.post(
        "/api/lineage/graph/subgraph",
        json={
            "asset_id": "dwd.x",
            "direction": "both",
            "depth": 2,
            "graph_edges": _sample_edges(),
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["asset_id"] == "dwd.x"
    assert body["direction"] == "both"
    assert body["depth"] == 2
    assert "stats" in body
    assert body["stats"]["total_nodes"] >= 1


def test_http_subgraph_validates_depth_range(client):
    response = client.post(
        "/api/lineage/graph/subgraph",
        json={"asset_id": "x", "depth": 100},
    )
    assert response.status_code == 422  # > 10


def test_http_subgraph_role_filter(client):
    response = client.post(
        "/api/lineage/graph/subgraph",
        json={
            "asset_id": "ods.a",
            "direction": "downstream",
            "depth": 3,
            "graph_edges": _sample_edges(),
            "table_roles": _sample_roles(),
            "role_filter": "intermediate",
        },
    )
    assert response.status_code == 200
    body = response.json()
    tables = {n["table"] for n in body["nodes"]}
    assert "fct.r" not in tables
    assert "dwd.x" in tables
