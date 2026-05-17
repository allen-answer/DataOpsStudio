"""Scenario AI filler tests（Phase 12 切片 9）。

scope:
- `fill_scenario` 单测（mock _call_ai）—— ai.fill 解析 / max_calls / 错误降级 / dedup
- generator realistic + AI filled values 协同（不接 LLM）
- /api/scenarios/{id}/ai-fill endpoint
- /api/scenarios/{id}/materialize 的 ai_fill 参数
"""
from __future__ import annotations

from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from app.scenarios.ai_filler import fill_scenario
from app.scenarios.generator import generate_scenario
from app.scenarios.models import Scenario


def _scenario(**kwargs: Any) -> Scenario:
    payload = {"id": "test", "name": "T", "seed": 42}
    payload.update(kwargs)
    return Scenario.model_validate(payload)


@pytest.fixture
def ai_provider_on(isolated_storage, monkeypatch):
    from app.services import lineage_ai as svc
    from app.services.lineage_ai import LineageAIConfig
    monkeypatch.setattr(svc, "_config", lambda: LineageAIConfig(
        provider="openai", model="gpt-fake", api_key="sk-test", base_url="https://x/v1",
    ))


@pytest.fixture
def ai_provider_off(isolated_storage, monkeypatch):
    from app.services import lineage_ai as svc
    from app.services.lineage_ai import LineageAIConfig
    monkeypatch.setattr(svc, "_config", lambda: LineageAIConfig(provider="off"))


# ─── fill_scenario：核心路径 ────────────────────────────────────────────────


def test_fill_no_ai_fill_scope_skips(isolated_storage):
    s = _scenario(
        ai={"provider": "${default}", "fill": []},
        tables=[{
            "name": "t", "role": "source", "rows": 10,
            "columns": [{"name": "x", "type": "DECIMAL", "gen": "realistic"}],
        }],
    )
    filled, report = fill_scenario(s)
    assert report.ok is False
    assert "ai.fill is empty" in report.skipped_reason
    assert filled is s  # 没改


def test_fill_provider_off_returns_unchanged(ai_provider_off):
    s = _scenario(
        ai={"provider": "${default}", "fill": ["column_values"]},
        tables=[{
            "name": "t", "role": "source", "rows": 10,
            "columns": [{"name": "x", "type": "DECIMAL", "gen": "realistic"}],
        }],
    )
    filled, report = fill_scenario(s)
    assert report.ok is False
    assert "未启用" in report.skipped_reason


def test_fill_column_values_writes_values(ai_provider_on, monkeypatch):
    from app.api import ai_utils

    captured: list[dict] = []

    def fake(provider, config, system_prompt, payload):
        captured.append(payload)
        if "table_name" in payload and "column_name" in payload:
            return {"values": [10.5, 22.0, 88.8, 199.99]}
        return {"description": "订单事实表"}

    monkeypatch.setattr(ai_utils, "_call_ai", fake)

    s = _scenario(
        ai={"provider": "${default}", "fill": ["column_values"]},
        tables=[{
            "name": "ods.orders", "role": "source", "rows": 100,
            "columns": [
                {"name": "id", "type": "INT", "pk": True, "gen": "sequence"},
                {"name": "amount", "type": "DECIMAL(10,2)", "gen": "realistic"},
            ],
        }],
    )
    filled, report = fill_scenario(s)
    assert report.ok is True
    assert report.calls == 1  # 只 realistic 列被填
    assert "ods.orders.amount" in report.filled_columns
    amount_col = next(c for c in filled.tables[0].columns if c.name == "amount")
    assert amount_col.values == [10.5, 22.0, 88.8, 199.99]
    # 原 scenario 未变
    orig_amount = next(c for c in s.tables[0].columns if c.name == "amount")
    assert orig_amount.values == []


def test_fill_description_when_missing(ai_provider_on, monkeypatch):
    from app.api import ai_utils
    monkeypatch.setattr(ai_utils, "_call_ai",
                        lambda *a, **kw: {"description": "订单事实表"})

    s = _scenario(
        ai={"provider": "${default}", "fill": ["table_descriptions"]},
        tables=[{
            "name": "ods.orders", "role": "source", "rows": 100,
            "columns": [{"name": "id", "type": "INT", "pk": True, "gen": "sequence"}],
        }],
    )
    filled, report = fill_scenario(s)
    assert filled.tables[0].description == "订单事实表"
    assert "ods.orders" in report.filled_descriptions


def test_fill_skip_table_with_existing_description(ai_provider_on, monkeypatch):
    from app.api import ai_utils
    monkeypatch.setattr(ai_utils, "_call_ai",
                        lambda *a, **kw: {"description": "AI 不该被调到"})

    s = _scenario(
        ai={"provider": "${default}", "fill": ["table_descriptions"]},
        tables=[{
            "name": "t", "role": "source", "rows": 0,
            "description": "已经写好的描述",
            "columns": [{"name": "id", "type": "INT", "pk": True, "gen": "sequence"}],
        }],
    )
    filled, report = fill_scenario(s)
    assert report.calls == 0  # 没调 LLM
    assert filled.tables[0].description == "已经写好的描述"


def test_fill_skip_column_with_existing_values(ai_provider_on, monkeypatch):
    from app.api import ai_utils
    monkeypatch.setattr(ai_utils, "_call_ai",
                        lambda *a, **kw: {"values": ["AI 不该被调到"]})

    s = _scenario(
        ai={"provider": "${default}", "fill": ["column_values"]},
        tables=[{
            "name": "t", "role": "source", "rows": 0,
            "columns": [{"name": "status", "type": "VARCHAR(8)", "gen": "realistic",
                         "values": ["paid", "pending"]}],
        }],
    )
    filled, report = fill_scenario(s)
    assert report.calls == 0
    status_col = filled.tables[0].columns[0]
    assert status_col.values == ["paid", "pending"]


def test_fill_max_calls_cap(ai_provider_on, monkeypatch):
    from app.api import ai_utils

    calls = []

    def fake(provider, config, system_prompt, payload):
        calls.append(1)
        return {"values": [1, 2, 3]}

    monkeypatch.setattr(ai_utils, "_call_ai", fake)

    # 5 张表 × 1 realistic 列 = 5 个潜在调用，cap=2
    s = _scenario(
        ai={"provider": "${default}", "fill": ["column_values"]},
        tables=[
            {"name": f"t{i}", "role": "source", "rows": 0,
             "columns": [{"name": "x", "type": "INT", "gen": "realistic"}]}
            for i in range(5)
        ],
    )
    filled, report = fill_scenario(s, max_calls=2)
    assert report.calls == 2
    assert any("max_calls" in e for e in report.errors)


def test_fill_handles_llm_error_per_field(ai_provider_on, monkeypatch):
    """单字段 LLM 调用失败不影响其他字段。"""
    from app.api import ai_utils

    counter = {"n": 0}

    def fake(provider, config, system_prompt, payload):
        counter["n"] += 1
        if counter["n"] == 1:
            raise RuntimeError("network glitch")
        return {"values": ["ok1", "ok2"]}

    monkeypatch.setattr(ai_utils, "_call_ai", fake)

    s = _scenario(
        ai={"provider": "${default}", "fill": ["column_values"]},
        tables=[
            {"name": "t1", "role": "source", "rows": 0,
             "columns": [{"name": "a", "type": "VARCHAR(8)", "gen": "realistic"}]},
            {"name": "t2", "role": "source", "rows": 0,
             "columns": [{"name": "b", "type": "VARCHAR(8)", "gen": "realistic"}]},
        ],
    )
    filled, report = fill_scenario(s)
    assert report.ok is True
    # 第一次失败 → errors 记一笔；第二次成功 → t2.b 拿到 values
    assert len(report.errors) == 1
    assert "network glitch" in report.errors[0]
    assert filled.tables[1].columns[0].values == ["ok1", "ok2"]


def test_fill_dedup_and_cap_30(ai_provider_on, monkeypatch):
    from app.api import ai_utils
    # 35 个值含 5 个重复
    monkeypatch.setattr(ai_utils, "_call_ai", lambda *a, **kw: {
        "values": list(range(30)) + [0, 1, 2, 3, 4]
    })

    s = _scenario(
        ai={"provider": "${default}", "fill": ["column_values"]},
        tables=[{"name": "t", "role": "source", "rows": 0,
                 "columns": [{"name": "x", "type": "INT", "gen": "realistic"}]}],
    )
    filled, _ = fill_scenario(s)
    vals = filled.tables[0].columns[0].values
    assert len(vals) == 30  # 截 30 + dedup
    assert len(set(map(str, vals))) == 30


def test_fill_non_realistic_columns_skipped(ai_provider_on, monkeypatch):
    """gen=random_int / uuid_short / sequence 等不该被 ai_filler 碰。"""
    from app.api import ai_utils
    monkeypatch.setattr(ai_utils, "_call_ai",
                        lambda *a, **kw: {"values": ["should not be used"]})

    s = _scenario(
        ai={"provider": "${default}", "fill": ["column_values"]},
        tables=[{
            "name": "t", "role": "source", "rows": 0,
            "columns": [
                {"name": "id", "type": "INT", "pk": True, "gen": "sequence"},
                {"name": "uid", "type": "VARCHAR(32)", "gen": "uuid_short"},
                {"name": "n", "type": "INT", "gen": "random_int", "range": [1, 100]},
            ],
        }],
    )
    filled, report = fill_scenario(s)
    assert report.calls == 0
    for col in filled.tables[0].columns:
        assert col.values == []


# ─── generator + AI values 协同 ─────────────────────────────────────────────


def test_realistic_uses_ai_filled_values_when_present():
    """col.values 非空 → _realistic_value 走 fast path，从 values 里 pick。"""
    s = _scenario(tables=[{
        "name": "t", "role": "source", "rows": 100,
        "columns": [{
            "name": "amount", "type": "DECIMAL(10,2)", "gen": "realistic",
            "values": [10.0, 50.0, 100.0],
        }],
    }])
    out = generate_scenario(s)["t"]
    assert all(r["amount"] in {10.0, 50.0, 100.0} for r in out)


def test_realistic_falls_back_when_no_values():
    """col.values 空 → 走类型嗅探 fallback（原行为）。"""
    s = _scenario(tables=[{
        "name": "t", "role": "source", "rows": 10,
        "columns": [{"name": "amount", "type": "DECIMAL(10,2)", "gen": "realistic"}],
    }])
    out = generate_scenario(s)["t"]
    for r in out:
        assert isinstance(r["amount"], float)
        assert 10.0 <= r["amount"] <= 5000.0


# ─── API endpoints ──────────────────────────────────────────────────────────


@pytest.fixture
def client_with_scenarios(client_admin, isolated_storage, monkeypatch):
    """sandbox path patching + admin token（scenarios router 全 admin only）。"""
    from app.utils.paths import BASE_DIR
    sdir = isolated_storage["cfg"] / "scenarios"
    sdir.mkdir()
    example_src = BASE_DIR / "config" / "scenarios" / "orders-recon.example.yml"
    sdir.joinpath("orders-recon.example.yml").write_text(
        example_src.read_text(encoding="utf-8"), encoding="utf-8"
    )
    from app.utils import paths as paths_module
    from app.scenarios import loader as loader_module
    from app.api import scenarios as api_module
    monkeypatch.setattr(paths_module, "SCENARIOS_DIR", sdir)
    monkeypatch.setattr(loader_module, "SCENARIOS_DIR", sdir)
    monkeypatch.setattr(api_module, "SCENARIOS_DIR", sdir)
    return client_admin


def test_ai_fill_endpoint_returns_filled_scenario(client_with_scenarios, ai_provider_on, monkeypatch):
    from app.api import ai_utils

    def fake(provider, config, system_prompt, payload):
        if "column_name" in payload:
            return {"values": [199.99, 299.99]}
        return {"description": "AI 给的描述"}

    monkeypatch.setattr(ai_utils, "_call_ai", fake)

    r = client_with_scenarios.post("/api/scenarios/orders-recon-mvp/ai-fill")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["report"]["ok"] is True
    assert body["report"]["calls"] >= 1
    # example.yml fill = [column_values, table_descriptions]，ods.orders.amount realistic
    amount_col = next(
        c for c in body["scenario"]["tables"][0]["columns"]
        if c["name"] == "amount"
    )
    assert amount_col["values"] == [199.99, 299.99]


def test_ai_fill_endpoint_provider_off_returns_200(client_with_scenarios, ai_provider_off):
    r = client_with_scenarios.post("/api/scenarios/orders-recon-mvp/ai-fill")
    assert r.status_code == 200
    body = r.json()
    assert body["report"]["ok"] is False
    assert "未启用" in body["report"]["skipped_reason"]


def test_ai_fill_endpoint_scenario_not_found(client_with_scenarios):
    r = client_with_scenarios.post("/api/scenarios/no-such/ai-fill")
    assert r.status_code == 404


def test_materialize_with_ai_fill_flag(client_with_scenarios, ai_provider_on, monkeypatch):
    """ai_fill=true 在 materialize 端点跑通整条 fill → generate → materialize。"""
    from app.api import ai_utils, scenarios as scenarios_api
    monkeypatch.setattr(ai_utils, "_call_ai", lambda *a, **kw: {"values": [10.0, 20.0, 30.0]})

    captured: dict = {}

    def fake_materialize(scenario, data, datasource_id, *, drop_first, batch_size):
        # 拿到 scenario 时 amount 列应该已经被 AI 填了 values
        amount = next(c for c in scenario.tables[0].columns if c.name == "amount")
        captured["amount_values"] = amount.values
        return {"dialect": "mysql", "schemas_created": [], "tables": [], "warnings": []}

    monkeypatch.setattr(scenarios_api, "materialize_to_datasource", fake_materialize)

    r = client_with_scenarios.post(
        "/api/scenarios/orders-recon-mvp/materialize",
        json={"datasource_id": "ds-1", "ai_fill": True},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "ai_fill" in body
    assert body["ai_fill"]["ok"] is True
    assert captured["amount_values"] == [10.0, 20.0, 30.0]


def test_materialize_without_ai_fill_skips_filler(client_with_scenarios, monkeypatch):
    """ai_fill=false（默认）→ 不调 fill_scenario，summary 里也没 ai_fill 字段。"""
    from app.api import scenarios as scenarios_api
    monkeypatch.setattr(scenarios_api, "materialize_to_datasource",
                        lambda *a, **kw: {"dialect": "mysql", "schemas_created": [],
                                          "tables": [], "warnings": []})
    # 不 mock _call_ai —— 如果 ai_filler 被错调，会走真实 provider 路径报错

    r = client_with_scenarios.post(
        "/api/scenarios/orders-recon-mvp/materialize",
        json={"datasource_id": "ds-1"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "ai_fill" not in body
