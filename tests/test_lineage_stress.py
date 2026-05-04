"""Phase 10 第 1 项：lineage 大图压测 fixture 生成器测试。

覆盖：
- 给定 size，生成的 tables / edges / target_summary 数量合理
- seed 固定 → 多次生成的结果完全一致（保证压测可重复）
- size 边界（min 1, max 10000）
- HTTP 端点 /api/lineage/stress-fixture 的 size 参数校验
- fixture 包含 LineageReport 期望的所有顶层字段
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from main import app
from app.services.lineage_stress import build_stress_fixture


def test_generates_correct_number_of_tables():
    fx = build_stress_fixture(300)
    assert len(fx["tables"]) == 300
    assert fx["stress_fixture"] is True
    assert fx["stress_size"] == 300


def test_seed_makes_output_deterministic():
    a = build_stress_fixture(500, seed=42)
    b = build_stress_fixture(500, seed=42)
    # 表名 / 边数完全一致
    assert [t["table"] for t in a["tables"]] == [t["table"] for t in b["tables"]]
    assert len(a["graph_edges"]) == len(b["graph_edges"])
    assert a["graph_edges"][0] == b["graph_edges"][0]


def test_different_seed_produces_different_output():
    a = build_stress_fixture(500, seed=1)
    b = build_stress_fixture(500, seed=2)
    # 至少边集应该不同
    assert a["graph_edges"] != b["graph_edges"]


def test_edges_only_go_downstream():
    """ods → dim/ref → dwd → dws → fct，不允许 fct 反过来指向 ods。"""
    layer = {"ods": 0, "dim": 1, "ref": 1, "dwd": 2, "dws": 3, "fct": 4}
    fx = build_stress_fixture(1000)
    for edge in fx["graph_edges"]:
        src_schema = edge["source_table"].split(".")[0]
        tgt_schema = edge["target_table"].split(".")[0]
        # source 的 layer 必须 < target 的 layer
        assert layer[src_schema] < layer[tgt_schema], (
            f"违反 layer 约束：{edge['source_table']} → {edge['target_table']}"
        )


def test_target_summary_only_for_writable_schemas():
    """只有 dwd / dws / fct 表才进 target_summary。"""
    fx = build_stress_fixture(1000)
    writable = {"dwd", "dws", "fct"}
    for ts in fx["target_summary"]:
        schema = ts["target_table"].split(".")[0]
        assert schema in writable


def test_table_roles_cover_all_tables():
    fx = build_stress_fixture(500)
    table_set = {t["table"] for t in fx["tables"]}
    role_set = {r["table"] for r in fx["table_roles"]}
    assert table_set == role_set


def test_size_zero_rejected():
    with pytest.raises(ValueError):
        build_stress_fixture(0)


def test_size_too_large_rejected():
    with pytest.raises(ValueError):
        build_stress_fixture(20000)


def test_max_size_5000_works():
    """5000 节点应该能在合理时间生成（< 1s on dev box）。"""
    import time
    started = time.perf_counter()
    fx = build_stress_fixture(5000)
    elapsed = time.perf_counter() - started
    assert len(fx["tables"]) == 5000
    assert elapsed < 5.0  # 留宽，CI 慢也能过


def test_fixture_has_all_expected_top_level_fields():
    """前端 LineageReportView 消费的字段必须都在。"""
    fx = build_stress_fixture(100)
    expected_fields = {
        "tables", "columns", "insert_mappings", "target_summary",
        "table_roles", "graph_edges", "graph_groups", "parse_errors",
        "warnings", "statements", "semantic_lineage", "report",
    }
    assert expected_fields.issubset(set(fx.keys()))


def test_semantic_lineage_observation_includes_stress_summary():
    fx = build_stress_fixture(500)
    obs = fx["semantic_lineage"]["observations"]
    assert obs
    assert any("500" in o for o in obs)


# ─── HTTP 端点 ───────────────────────────────────────────────────────────────


@pytest.fixture
def client(isolated_storage):
    return TestClient(app)


def test_http_stress_fixture_endpoint(client):
    response = client.get("/api/lineage/stress-fixture?size=300")
    assert response.status_code == 200
    body = response.json()
    assert body["stress_fixture"] is True
    assert body["stress_size"] == 300
    assert len(body["tables"]) == 300


def test_http_stress_fixture_size_too_small_400(client):
    response = client.get("/api/lineage/stress-fixture?size=5")
    assert response.status_code == 400


def test_http_stress_fixture_size_too_large_400(client):
    response = client.get("/api/lineage/stress-fixture?size=20000")
    assert response.status_code == 400


def test_http_stress_fixture_default_size_works(client):
    response = client.get("/api/lineage/stress-fixture")
    assert response.status_code == 200
    body = response.json()
    assert body["stress_size"] == 1000
