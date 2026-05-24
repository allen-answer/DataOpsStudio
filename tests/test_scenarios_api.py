"""Scenario sandbox API tests（Phase 12 切片 4）。

scope: `/api/scenarios` 4 个端点
- GET list / GET detail
- POST materialize（mock materialize_to_datasource —— 端到端 DB 留集成）
- POST record（用 isolated_storage，task_store 真写）
"""
from __future__ import annotations

import pytest
import yaml
from fastapi.testclient import TestClient

from app.utils.paths import BASE_DIR


SCENARIO_EXAMPLE = BASE_DIR / "config" / "scenarios" / "orders-recon.example.yml"


# `client` fixture 来自 conftest.py（admin-authed）。scenarios router 全 admin only。


@pytest.fixture
def populated_scenarios(isolated_storage, monkeypatch):
    """把 example yml 复制进 isolated_storage 的 scenarios 目录，让 endpoint 能找到。

    SCENARIOS_DIR 在 paths 模块；endpoint 和 loader 都 import from paths。
    """
    sdir = isolated_storage["cfg"] / "scenarios"
    sdir.mkdir()
    sdir.joinpath("orders-recon.example.yml").write_text(
        SCENARIO_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    from app.utils import paths as paths_module
    from app.scenarios import loader as loader_module
    from app.api import scenarios as api_module
    monkeypatch.setattr(paths_module, "SCENARIOS_DIR", sdir)
    monkeypatch.setattr(loader_module, "SCENARIOS_DIR", sdir)
    monkeypatch.setattr(api_module, "SCENARIOS_DIR", sdir)
    return sdir


# ─── GET /api/scenarios ─────────────────────────────────────────────────────


def test_list_returns_example(client, populated_scenarios):
    r = client.get("/api/scenarios")
    assert r.status_code == 200
    body = r.json()
    assert "items" in body
    ids = [it.get("id") for it in body["items"]]
    assert "orders-recon-mvp" in ids


def test_list_empty_dir_returns_empty(client, populated_scenarios):
    for p in populated_scenarios.glob("*.yml"):
        p.unlink()
    r = client.get("/api/scenarios")
    assert r.status_code == 200
    assert r.json() == {"items": []}


def test_list_shows_error_for_broken_yml(client, populated_scenarios):
    populated_scenarios.joinpath("broken.yml").write_text(
        "id: bad\nname: B\n# 缺 tables\n", encoding="utf-8"
    )
    r = client.get("/api/scenarios")
    items = r.json()["items"]
    broken = [it for it in items if "error" in it]
    assert len(broken) == 1


# ─── GET /api/scenarios/{id} ────────────────────────────────────────────────


def test_get_scenario_detail(client, populated_scenarios):
    r = client.get("/api/scenarios/orders-recon-mvp")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scenario"]["id"] == "orders-recon-mvp"
    assert len(body["scenario"]["tables"]) == 2
    assert body["path"] == "orders-recon.example.yml"


def test_get_scenario_not_found(client, populated_scenarios):
    r = client.get("/api/scenarios/no-such-id")
    assert r.status_code == 404


# ─── POST /api/scenarios/{id}/materialize ───────────────────────────────────


def test_materialize_calls_runtime(client, populated_scenarios, monkeypatch):
    captured: dict = {}

    def fake_materialize(scenario, data, datasource_id, *, drop_first, batch_size):
        captured["scenario_id"] = scenario.id
        captured["datasource_id"] = datasource_id
        captured["drop_first"] = drop_first
        captured["batch_size"] = batch_size
        captured["table_count"] = len(data)
        return {
            "dialect": "mysql",
            "schemas_created": ["CREATE DATABASE IF NOT EXISTS `ods`"],
            "tables": [{"name": "ods.orders", "rows_inserted": 1000}],
            "warnings": [],
        }

    from app.api import scenarios as api_module
    monkeypatch.setattr(api_module, "materialize_to_datasource", fake_materialize)

    r = client.post(
        "/api/scenarios/orders-recon-mvp/materialize",
        json={"datasource_id": "demo-mysql", "drop_first": True, "batch_size": 200},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert captured == {
        "scenario_id": "orders-recon-mvp",
        "datasource_id": "demo-mysql",
        "drop_first": True,
        "batch_size": 200,
        "table_count": 2,
    }
    assert body["dialect"] == "mysql"
    assert "rows_generated" in body
    assert body["rows_generated"]["ods.orders"] == 1000


def test_materialize_runtime_error_returns_400(client, populated_scenarios, monkeypatch):
    from app.api import scenarios as api_module
    from app.scenarios.runtime import ScenarioRuntimeError

    def fake_raise(*a, **kw):
        raise ScenarioRuntimeError("datasource not found: nope")

    monkeypatch.setattr(api_module, "materialize_to_datasource", fake_raise)
    r = client.post(
        "/api/scenarios/orders-recon-mvp/materialize",
        json={"datasource_id": "nope"},
    )
    assert r.status_code == 400
    # error envelope (Phase 9 Day 6) wraps detail
    body = r.json()
    assert "datasource not found" in str(body)


def test_materialize_missing_datasource_id_rejected(client, populated_scenarios):
    r = client.post(
        "/api/scenarios/orders-recon-mvp/materialize",
        json={"datasource_id": ""},
    )
    assert r.status_code in (400, 422)  # pydantic min_length 拦


def test_materialize_scenario_not_found_404(client, populated_scenarios):
    r = client.post(
        "/api/scenarios/no-such/materialize",
        json={"datasource_id": "demo"},
    )
    assert r.status_code == 404


# ─── Phase 14 #1 合规防御:sandbox-only guard ──────────────────────────────


def _create_ds_in_store(name: str, environment: str = "sandbox") -> str:
    """在 datasource_store 里建一个真 datasource 用于测 guard,返回 id。"""
    from app.models.datasource import DataSourceCreate
    from app.models.common import DatabaseType
    from app.services.repositories import datasource_store
    payload = DataSourceCreate(
        name=name, db_type=DatabaseType.MYSQL, host="localhost", port=3306,
        database="x", username="x", password="x",
        environment=environment,  # type: ignore[arg-type]
    )
    ds = datasource_store.create(payload)
    return ds.id


def test_materialize_rejects_prod_datasource(client, populated_scenarios, monkeypatch):
    """合规防御:prod 环境 ds → materialize 403 + 中文错误"""
    ds_id = _create_ds_in_store("prod-mysql", environment="prod")
    r = client.post(
        "/api/scenarios/orders-recon-mvp/materialize",
        json={"datasource_id": ds_id, "drop_first": True},
    )
    assert r.status_code == 403, r.text
    body = r.json()
    detail = str(body)
    assert "prod" in detail
    assert "sandbox" in detail or "造数据" in detail


def test_run_all_rejects_staging_datasource(client, populated_scenarios):
    """staging 也被拦(只放 sandbox)"""
    ds_id = _create_ds_in_store("staging-mysql", environment="staging")
    r = client.post(
        "/api/scenarios/orders-recon-mvp/run-all",
        json={"datasource_id": ds_id},
    )
    assert r.status_code == 403


def test_record_rejects_prod_datasource(client, populated_scenarios):
    """record 也被拦"""
    ds_id = _create_ds_in_store("prod-rec", environment="prod")
    r = client.post(
        "/api/scenarios/orders-recon-mvp/record",
        json={"datasource_id": ds_id, "project_id": "demo"},
    )
    assert r.status_code == 403


def test_materialize_passes_sandbox_datasource(client, populated_scenarios, monkeypatch):
    """sandbox 环境 ds → guard 通过,继续走 materialize 主流程"""
    ds_id = _create_ds_in_store("sandbox-ok", environment="sandbox")

    def fake_materialize(scenario, data, datasource_id, *, drop_first, batch_size):
        return {"dialect": "mysql", "schemas_created": [], "tables": [], "warnings": []}

    from app.api import scenarios as api_module
    monkeypatch.setattr(api_module, "materialize_to_datasource", fake_materialize)
    r = client.post(
        "/api/scenarios/orders-recon-mvp/materialize",
        json={"datasource_id": ds_id},
    )
    assert r.status_code == 200, r.text


def test_verify_endpoint_does_not_guard_env(client, populated_scenarios):
    """只读路径(verify / analyze)不受 sandbox-only 限制 — 用 prod ds 也能跑"""
    ds_id = _create_ds_in_store("prod-readonly", environment="prod")
    # verify 不直接收 datasource_id,但跟环境无关 — 此处主要验证 verify endpoint
    # 不调用 _enforce_sandbox_only(同 scenario 即可)
    r = client.get("/api/scenarios/orders-recon-mvp/verify")
    # verify 默认走 task_store,不依赖 ds_id 直接, 但不应 403
    assert r.status_code != 403
    # 用 ds_id 防 unused 警告
    assert ds_id


# ─── POST /api/scenarios/{id}/record ────────────────────────────────────────


def test_record_creates_compare_tasks(client, populated_scenarios):
    r = client.post(
        "/api/scenarios/orders-recon-mvp/record",
        json={"datasource_id": "demo-mysql", "project_id": "demo"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["tasks"]) == 1
    t = body["tasks"][0]
    assert t["source_id"] == "demo-mysql"
    assert t["target_id"] == "demo-mysql"
    assert t["project_id"] == "demo"
    assert t["key_columns"] == ["order_id"]


def test_record_scenario_not_found_404(client, populated_scenarios):
    r = client.post(
        "/api/scenarios/no-such/record",
        json={"datasource_id": "demo"},
    )
    assert r.status_code == 404


def test_record_missing_datasource_id_rejected(client, populated_scenarios):
    r = client.post(
        "/api/scenarios/orders-recon-mvp/record",
        json={"datasource_id": ""},
    )
    assert r.status_code in (400, 422)
