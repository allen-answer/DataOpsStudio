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


def test_fill_provider_off_falls_back_to_faker(ai_provider_off):
    """Phase 14:provider=off + fill=column_values → 走 Faker locale fallback,
    跟之前直接 skip 的行为变了 —— 仍生成业务样本而不是返 unchanged"""
    s = _scenario(
        ai={"provider": "${default}", "fill": ["column_values"]},
        tables=[{
            "name": "t", "role": "source", "rows": 10,
            "columns": [{"name": "x", "type": "DECIMAL", "gen": "realistic"}],
        }],
    )
    filled, report = fill_scenario(s)
    assert report.ok is True
    assert "Faker fallback" in report.skipped_reason
    # Faker 给 unmapped 列(DECIMAL)走类型嗅探,填 int 样本
    col = filled.tables[0].columns[0]
    assert col.values, "Faker fallback 应给该列填了样本"


def test_fill_provider_off_only_descriptions_still_skips(ai_provider_off):
    """provider=off + 只配 table_descriptions 不含 column_values → 仍 skip
    (Faker 没 description / distribution 等价物)"""
    s = _scenario(
        ai={"provider": "${default}", "fill": ["table_descriptions"]},
        tables=[{
            "name": "t", "role": "source", "rows": 10,
            "columns": [{"name": "x", "type": "DECIMAL", "gen": "realistic"}],
        }],
    )
    _, report = fill_scenario(s)
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
    """sandbox path patching + admin token(scenarios router 全 admin only)。

    Phase 14 #3:也注一个 "ds-1" 的 sandbox MySQL ds(测试用字面 id)。
    """
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

    # 注 "ds-1" 字面 id 的 sandbox ds
    from app.models.datasource import (
        DataSource, DatabaseType, make_sandbox_datasource_kwargs,
    )
    from app.services.repositories import datasource_store
    ds = DataSource(
        id="ds-1", name="ds-1-sandbox", db_type=DatabaseType.MYSQL,
        host="localhost", port=3306,
        **make_sandbox_datasource_kwargs(),
    )
    current = [d.model_dump(mode="json") for d in datasource_store.list()]
    current.append(ds.model_dump(mode="json"))
    datasource_store._write_raw(current)  # noqa: SLF001
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
    """Phase 14:provider=off 时 endpoint 仍返 200,走 Faker fallback。orders-recon-mvp
    scenario 配了 column_values → 应被 Faker 填(不是之前的 skip)"""
    r = client_with_scenarios.post("/api/scenarios/orders-recon-mvp/ai-fill")
    assert r.status_code == 200
    body = r.json()
    # column_values 在 fill 配置里 → Faker fallback 跑通
    assert body["report"]["ok"] is True
    assert "Faker fallback" in body["report"]["skipped_reason"]


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


# ─── Phase 14: Faker locale fallback ────────────────────────────────────────


def _faker_scenario(domain_vertical: str = "电商") -> Scenario:
    """构造带 ai.fill=column_values + Faker 友好列名的 scenario"""
    return Scenario.model_validate({
        "id": "faker-test",
        "name": "faker test",
        "domain": {"vertical": domain_vertical},
        "ai": {"fill": ["column_values"]},
        "dialect": "mysql",
        "tables": [{
            "name": "customers",
            "role": "source",
            "rows": 100,
            "columns": [
                {"name": "id", "type": "BIGINT", "gen": "sequence"},
                {"name": "user_name", "type": "VARCHAR(100)", "gen": "realistic"},
                {"name": "email", "type": "VARCHAR(255)", "gen": "realistic"},
                {"name": "city", "type": "VARCHAR(50)", "gen": "realistic"},
            ],
        }],
    })


def test_faker_fallback_when_provider_off(monkeypatch):
    """provider=off + fill=column_values → 走 Faker fallback,填业务样本不是 LLM"""
    from app.scenarios.ai_filler import fill_scenario
    from app.services import lineage_ai as ai_module

    monkeypatch.setattr(ai_module, "_config",
                        lambda: type("c", (), {"provider": "off"})())

    scenario = _faker_scenario()
    filled, report = fill_scenario(scenario)

    assert report.ok is True
    assert "Faker fallback" in report.skipped_reason
    # 3 个 realistic 列(user_name / email / city)应都被填
    assert len(report.filled_columns) == 3
    # 拿到的 values 都是 list[str] 非空
    cust = filled.tables[0]
    for col in cust.columns:
        if col.gen == "realistic":
            assert col.values, f"{col.name} 应被 Faker 填了"
            assert all(isinstance(v, str) for v in col.values)


def test_faker_fallback_locale_zh_cn():
    """domain.vertical=电商 → 推断 zh_CN locale → 拿到中文姓名"""
    from app.scenarios.faker_fallback import (
        detect_locale_from_scenario,
        generate_faker_values,
    )
    scenario = _faker_scenario(domain_vertical="电商")
    assert detect_locale_from_scenario(scenario) == "zh_CN"
    values = generate_faker_values("user_name", n=10, locale="zh_CN", seed=42)
    assert values is not None
    assert len(values) >= 1
    # 中文姓名应含至少一个 CJK 字符
    sample = str(values[0])
    assert any("一" <= c <= "鿿" for c in sample)


def test_faker_fallback_locale_default_en():
    """无中文 vertical → 默认 en_US"""
    from app.scenarios.faker_fallback import detect_locale_from_scenario
    scenario = _faker_scenario(domain_vertical="general saas")
    assert detect_locale_from_scenario(scenario) == "en_US"


def test_faker_fallback_unmapped_column_uses_type():
    """列名匹配不上 Faker mapping → 走类型嗅探兜底"""
    from app.scenarios.faker_fallback import generate_faker_values
    values = generate_faker_values(
        "obscure_metric_xyz", col_type="DECIMAL(10,2)", n=5, locale="en_US", seed=1,
    )
    assert values is not None
    # 类型嗅探给数值
    assert all(isinstance(v, int) for v in values)


def test_faker_fallback_seed_reproducible():
    """同 seed + 同输入 → 同输出"""
    from app.scenarios.faker_fallback import generate_faker_values
    a = generate_faker_values("user_name", n=5, locale="zh_CN", seed=42)
    b = generate_faker_values("user_name", n=5, locale="zh_CN", seed=42)
    assert a == b


def test_faker_fallback_provider_off_table_descriptions_warns(monkeypatch):
    """provider=off + 只配 table_descriptions(没 column_values)→ 仍 skip,
    因为 Faker 没 description 等价物"""
    from app.scenarios.ai_filler import fill_scenario
    from app.services import lineage_ai as ai_module

    monkeypatch.setattr(ai_module, "_config",
                        lambda: type("c", (), {"provider": "off"})())

    scenario = Scenario.model_validate({
        "id": "fck-test",
        "name": "test",
        "domain": {"vertical": "电商"},
        "ai": {"fill": ["table_descriptions"]},  # 没 column_values
        "dialect": "mysql",
        "tables": [{
            "name": "t1", "role": "source", "rows": 1,
            "columns": [{"name": "id", "type": "INT", "gen": "sequence"}],
        }],
    })
    _, report = fill_scenario(scenario)
    assert report.ok is False
    assert "Faker fallback 只支持 column_values" in report.skipped_reason
