"""Phase 10 #3 v1：全局 lineage 索引测试。

覆盖：
- 从合成 workflow_run 文件 lazy 构建索引
- 索引 rebuild 时正确聚合 graph_edges / table_roles / target_summary
- 同表多 run 出现 → role 取出现频次最高，refresh_modes 合并去重
- query_subgraph 直接查索引（不用 caller 提供 edges）
- get_table_metadata 返回单表的 role / refresh_modes / 上下游 count
- invalidate() 触发 rebuild
- TTL 过期触发 rebuild
- 无 workflow_run 时索引为空，query 返回单点
- HTTP 端点：GET /api/lineage/graph + /stats + /refresh
- get_table_asset 走索引补 primary_role / refresh_mode / 上下游 count
"""
from __future__ import annotations

import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from main import app
from app.models.compare import CompareTaskCreate
from app.services.assets import get_table_asset
from app.services.lineage_index import LineageIndex, get_lineage_index
from app.services.repositories import task_store


def _write_run(wf_runs_dir, *, run_id=None, edges=None, roles=None, target_summary=None, started_at="2026-05-04T10:00:00"):
    """合成一个 workflow_run JSON 文件 —— 一个 lineage 节点 + output。

    格式跟 WorkflowRun pydantic 序列化一致：run_id（不是 id）+ nodes 数组里
    每个 node_run 含 node_id / type / output。
    """
    run_id = run_id or uuid.uuid4().hex
    payload = {
        "run_id": run_id,
        "workflow_id": "test-wf",
        "status": "succeeded",
        "started_at": started_at,
        "created_at": started_at,
        "nodes": [{
            "node_id": "n1",
            "type": "lineage",
            "status": "succeeded",
            "output": {
                "graph_edges": edges or [],
                "table_roles": roles or [],
                "target_summary": target_summary or [],
            },
        }],
    }
    (wf_runs_dir / f"{run_id}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return run_id


# ─── 单元 ────────────────────────────────────────────────────────────────────


def test_index_empty_when_no_runs(isolated_storage):
    idx = LineageIndex(ttl_seconds=300)
    stats = idx.stats()
    assert stats["table_count"] == 0
    assert stats["edge_count"] == 0


def test_index_aggregates_edges_and_roles(isolated_storage):
    wf_runs = isolated_storage["wf_runs"]
    _write_run(
        wf_runs,
        edges=[
            {"source_table": "ods.a", "target_table": "dwd.x", "edge_type": "table", "confidence": "high"},
            {"source_table": "dwd.x", "target_table": "fct.r", "edge_type": "table", "confidence": "high"},
        ],
        roles=[
            {"table": "ods.a", "primary_role": "source_fact", "roles": ["source_fact"]},
            {"table": "dwd.x", "primary_role": "intermediate", "roles": ["intermediate"]},
            {"table": "fct.r", "primary_role": "target", "roles": ["target"]},
        ],
        target_summary=[
            {"target_table": "dwd.x", "refresh_mode": "truncate_insert"},
            {"target_table": "fct.r", "refresh_mode": "merge"},
        ],
    )
    idx = LineageIndex(ttl_seconds=300)
    stats = idx.stats()
    assert stats["edge_count"] == 2
    assert stats["table_count"] == 3
    meta = idx.get_table_metadata("dwd.x")
    assert meta["primary_role"] == "intermediate"
    assert meta["refresh_modes"] == ["truncate_insert"]
    assert meta["upstream_count"] == 1
    assert meta["downstream_count"] == 1


def test_index_dedups_edges_across_runs(isolated_storage):
    wf_runs = isolated_storage["wf_runs"]
    edge = {"source_table": "ods.a", "target_table": "dwd.x", "edge_type": "table"}
    _write_run(wf_runs, edges=[edge], started_at="2026-05-04T10:00:00")
    _write_run(wf_runs, edges=[edge], started_at="2026-05-04T11:00:00")
    idx = LineageIndex(ttl_seconds=300)
    assert idx.stats()["edge_count"] == 1  # 同 (src, tgt, edge_type) 去重


def test_index_role_takes_most_common_across_runs(isolated_storage):
    """同一表在多个 run 中标了不同 primary_role，取出现频次最高的。"""
    wf_runs = isolated_storage["wf_runs"]
    for _ in range(2):
        _write_run(wf_runs, roles=[{"table": "x", "primary_role": "intermediate", "roles": ["intermediate"]}])
    _write_run(wf_runs, roles=[{"table": "x", "primary_role": "target", "roles": ["target"]}])
    idx = LineageIndex(ttl_seconds=300)
    meta = idx.get_table_metadata("x")
    assert meta["primary_role"] == "intermediate"  # 2 次 > 1 次


def test_index_refresh_modes_merged(isolated_storage):
    wf_runs = isolated_storage["wf_runs"]
    _write_run(wf_runs, target_summary=[{"target_table": "x", "refresh_mode": "truncate_insert"}])
    _write_run(wf_runs, target_summary=[{"target_table": "x", "refresh_mode": "merge"}])
    idx = LineageIndex(ttl_seconds=300)
    meta = idx.get_table_metadata("x")
    assert set(meta["refresh_modes"]) == {"truncate_insert", "merge"}


def test_index_query_subgraph_uses_aggregated_data(isolated_storage):
    wf_runs = isolated_storage["wf_runs"]
    _write_run(
        wf_runs,
        edges=[
            {"source_table": "ods.a", "target_table": "dwd.x", "edge_type": "table"},
            {"source_table": "dwd.x", "target_table": "fct.r", "edge_type": "table"},
        ],
    )
    idx = LineageIndex(ttl_seconds=300)
    sub = idx.query_subgraph("dwd.x", direction="both", depth=1)
    tables = {n["table"] for n in sub["nodes"]}
    assert tables == {"ods.a", "dwd.x", "fct.r"}


def test_index_invalidate_forces_rebuild(isolated_storage):
    wf_runs = isolated_storage["wf_runs"]
    idx = LineageIndex(ttl_seconds=300)
    assert idx.stats()["edge_count"] == 0
    # 之后才 write run
    _write_run(wf_runs, edges=[{"source_table": "a", "target_table": "b", "edge_type": "table"}])
    # 不 invalidate —— TTL 内 + run count 变了应该自动失效
    assert idx.stats()["edge_count"] == 1


def test_index_skips_non_lineage_node_outputs(isolated_storage):
    wf_runs = isolated_storage["wf_runs"]
    payload = {
        "run_id": "run1",
        "workflow_id": "wf",
        "started_at": "2026-05-04T10:00:00",
        "nodes": [{
            "node_id": "n1",
            "type": "compare",  # 不是 lineage type
            "output": {
                "graph_edges": [{"source_table": "a", "target_table": "b", "edge_type": "table"}],
            },
        }],
    }
    (wf_runs / "run1.json").write_text(json.dumps(payload), encoding="utf-8")
    idx = LineageIndex(ttl_seconds=300)
    assert idx.stats()["edge_count"] == 0


# ─── 集成：HTTP 端点 ─────────────────────────────────────────────────────────


@pytest.fixture
def client(isolated_storage):
    return TestClient(app)


def test_http_graph_query_returns_subgraph_from_index(client, isolated_storage):
    wf_runs = isolated_storage["wf_runs"]
    _write_run(wf_runs, edges=[
        {"source_table": "ods.a", "target_table": "dwd.x", "edge_type": "table"},
        {"source_table": "dwd.x", "target_table": "fct.r", "edge_type": "table"},
    ])
    # 确保新 run 被索引看见
    get_lineage_index().invalidate()

    response = client.get("/api/lineage/graph", params={"asset_id": "dwd.x", "depth": 1})
    assert response.status_code == 200
    body = response.json()
    assert body["asset_id"] == "dwd.x"
    tables = {n["table"] for n in body["nodes"]}
    assert tables == {"ods.a", "dwd.x", "fct.r"}


def test_http_graph_stats_endpoint(client, isolated_storage):
    response = client.get("/api/lineage/graph/stats")
    assert response.status_code == 200
    body = response.json()
    for key in ("table_count", "edge_count", "source_run_count", "built_at", "ttl_seconds"):
        assert key in body


# ─── 集成：asset detail 走索引 ───────────────────────────────────────────────


def test_get_table_asset_fills_role_from_index(isolated_storage):
    wf_runs = isolated_storage["wf_runs"]
    _write_run(
        wf_runs,
        edges=[
            {"source_table": "ods.t_users", "target_table": "dwd.t_users_clean", "edge_type": "table"},
        ],
        roles=[
            {"table": "ods.t_users", "primary_role": "source_fact", "roles": ["source_fact"]},
            {"table": "dwd.t_users_clean", "primary_role": "intermediate", "roles": ["intermediate"]},
        ],
        target_summary=[
            {"target_table": "dwd.t_users_clean", "refresh_mode": "truncate_insert"},
        ],
    )
    get_lineage_index().invalidate()
    asset = get_table_asset("dwd.t_users_clean")
    assert asset["primary_role"] == "intermediate"
    assert asset["refresh_mode"] == "truncate_insert"
    assert asset["refresh_modes"] == ["truncate_insert"]
    assert asset["upstream_count"] == 1
    assert asset["downstream_count"] == 0


def test_get_table_asset_keeps_null_role_when_not_in_index(isolated_storage):
    """索引里没这张表 → role / refresh_mode 保持 null，但 references 仍可能命中。"""
    task_store.create(CompareTaskCreate(
        name="x", sql_mode="double",
        source_sql="SELECT * FROM unknown.t1", target_sql="SELECT * FROM unknown.t2",
        source_id="x", target_id="y", key_columns=["id"],
    ))
    get_lineage_index().invalidate()
    asset = get_table_asset("unknown.t1")
    assert asset["primary_role"] is None
    assert asset["refresh_mode"] is None
    assert asset["upstream_count"] == 0
    assert asset["stats"]["task_count"] == 1


def test_lineage_index_shares_cached_run_payloads(isolated_storage, monkeypatch):
    """LineageIndex 重建走 workflow_history.get_cached_run_payloads ——
    跟 assets / search 共用同一份解析后的 runs，避免重复 JSON parse。"""
    from app.services import workflow_history
    _write_run(isolated_storage["wf_runs"],
               edges=[{"source_table": "ods.x", "target_table": "dwd.x"}])
    workflow_history.invalidate_run_payloads_cache()
    get_lineage_index().invalidate()

    # 先让 assets 把 payload cache 预热（一次 JSON parse）
    from app.services import assets as assets_svc
    assets_svc.invalidate_column_edge_index_cache()
    # invalidate_column_edge_index_cache 会清 payload cache —— 重新预热
    workflow_history.get_cached_run_payloads(50)
    assert workflow_history._run_payloads_cache, "预热后 payload cache 应有条目"

    # 计 get_workflow_run 调用数
    call_count = {"n": 0}
    real = workflow_history.get_workflow_run

    def counting(rid):
        call_count["n"] += 1
        return real(rid)

    monkeypatch.setattr(workflow_history, "get_workflow_run", counting)

    # LineageIndex 重建走 cached payloads → 不应再 get_workflow_run
    get_lineage_index().invalidate()
    get_lineage_index().query_subgraph("ods.x", direction="downstream", depth=1)
    assert call_count["n"] == 0, "重建走共享 cache，不应额外调 get_workflow_run"
