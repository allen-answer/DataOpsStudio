"""/api/v1/ alias 测试。

覆盖：
- 所有 /api/X 都有对应 /api/v1/X 同义路由
- 同一个 endpoint 走 v1 和走老路径返回相同结果
- v1 路由出现在 OpenAPI schema（带 v1 tag）
- /api/v1/v1/X 等坏路径不会重复被注入
- non-/api/ 路径（/spa / /metrics / /static）不被克隆
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from main import app
from app.api._versioning import _v1_path, install_v1_aliases


# ─── 单元 ────────────────────────────────────────────────────────────────────


def test_v1_path_transforms_api_to_v1():
    assert _v1_path("/api/datasources") == "/api/v1/datasources"
    assert _v1_path("/api/lineage/graph") == "/api/v1/lineage/graph"
    assert _v1_path("/api/assets/table/{name:path}") == "/api/v1/assets/table/{name:path}"


def test_v1_path_skips_already_v1_or_non_api():
    assert _v1_path("/api/v1/datasources") is None
    assert _v1_path("/spa") is None
    assert _v1_path("/static/spa/index.html") is None
    assert _v1_path("/metrics") is None
    assert _v1_path("/") is None


def test_install_v1_aliases_idempotent():
    """重复 install 不会再加新 v1 routes（已经有了的跳过）。"""
    from fastapi import FastAPI
    test_app = FastAPI()

    @test_app.get("/api/foo")
    def foo():
        return {"ok": True}

    @test_app.get("/api/bar")
    def bar():
        return {"ok": True}

    n1 = install_v1_aliases(test_app)
    n2 = install_v1_aliases(test_app)
    assert n1 == 2
    assert n2 == 0


# ─── 集成：实际 app ──────────────────────────────────────────────────────────


@pytest.fixture
def client(isolated_storage):
    return TestClient(app)


def test_known_endpoints_have_v1_alias(client):
    """每个核心 endpoint 应该既能 /api/X 也能 /api/v1/X。"""
    pairs = [
        ("/api/drivers", "/api/v1/drivers"),
        ("/api/lineage/graph/stats", "/api/v1/lineage/graph/stats"),
    ]
    for legacy, v1 in pairs:
        r1 = client.get(legacy)
        r2 = client.get(v1)
        assert r1.status_code == r2.status_code == 200, f"both should work: {legacy} / {v1}"


def test_v1_returns_same_payload_as_legacy(client):
    legacy = client.get("/api/lineage/graph/stats").json()
    v1 = client.get("/api/v1/lineage/graph/stats").json()
    assert legacy == v1


def test_openapi_includes_both_legacy_and_v1_paths(client):
    """OpenAPI 文档里两套路径都注册了。"""
    schema = client.get("/openapi.json").json()
    paths = set(schema.get("paths", {}).keys())
    assert "/api/drivers" in paths
    assert "/api/v1/drivers" in paths
    assert "/api/lineage/graph/stats" in paths
    assert "/api/v1/lineage/graph/stats" in paths


def test_v1_routes_have_v1_tag(client):
    schema = client.get("/openapi.json").json()
    drivers_v1 = schema["paths"]["/api/v1/drivers"]
    method = next(iter(drivers_v1.values()))
    assert "v1" in (method.get("tags") or [])


def test_metrics_and_spa_not_cloned():
    """非 /api/ 路径（/metrics / / / /static / /spa）不被注入 v1 别名。"""
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/api/v1/metrics" not in paths
    assert "/api/v1/spa" not in paths


def test_post_endpoint_with_v1(client, isolated_storage):
    """非 GET 端点（POST）的 v1 别名也要 work + 同样的 request body 处理。"""
    response = client.post(
        "/api/v1/lineage/graph/subgraph",
        json={
            "asset_id": "x",
            "direction": "both",
            "depth": 1,
            "graph_edges": [
                {"source_table": "a", "target_table": "x", "edge_type": "table"},
                {"source_table": "x", "target_table": "b", "edge_type": "table"},
            ],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["asset_id"] == "x"
    tables = {n["table"] for n in body["nodes"]}
    assert tables == {"a", "x", "b"}
