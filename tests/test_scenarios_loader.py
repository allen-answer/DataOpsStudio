"""Scenario DSL loader + 校验测试（Phase 12 骨架阶段）。

只覆盖 loader + Pydantic 校验：YAML → Scenario 实例的解析正确性、`extra='forbid'`
拦未知字段、`extends`/derives_from 等结构语义。

generator / materializer / ai_filler 留给下个切片，这里不测。
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from app.scenarios import loader as scenarios_loader
from app.scenarios.loader import list_scenarios, load_scenario
from app.scenarios.models import (
    AnomalyDef,
    Scenario,
    WorkloadDef,
)
from app.utils.paths import BASE_DIR


EXAMPLE_PATH = BASE_DIR / "config" / "scenarios" / "orders-recon.example.yml"


# ─── 加载真实 example ────────────────────────────────────────────────────────


def test_example_scenario_loads_cleanly():
    s = load_scenario(EXAMPLE_PATH)
    assert isinstance(s, Scenario)
    assert s.id == "orders-recon-mvp"
    assert s.dialect == "mysql"
    assert s.seed == 42
    assert len(s.tables) == 2
    assert {t.name for t in s.tables} == {"ods.orders", "dwd.orders_clean"}


def test_example_target_derives_from_source():
    s = load_scenario(EXAMPLE_PATH)
    dwd = next(t for t in s.tables if t.name == "dwd.orders_clean")
    assert dwd.derives_from == "ods.orders"
    assert len(dwd.column_overrides) == 1
    ov = dwd.column_overrides[0]
    # alias='from' 应正确映射到 from_ 属性
    assert ov.from_ == "created_at"
    assert ov.rename == "order_date"
    assert ov.transform == "DATE($)"


def test_example_indexes_include_skip_flag():
    """slow-sql demo 故意缺索引那条要正确解析。"""
    s = load_scenario(EXAMPLE_PATH)
    dwd = next(t for t in s.tables if t.name == "dwd.orders_clean")
    skipped = [i for i in dwd.indexes if i.skip]
    assert len(skipped) == 1
    assert skipped[0].columns == ["order_date"]
    assert "慢查询" in skipped[0].reason


def test_example_anomaly_kinds():
    s = load_scenario(EXAMPLE_PATH)
    kinds = {a.kind for a in s.anomalies}
    assert {"missing_rows", "extra_rows", "value_drift", "null_drift"} <= kinds


def test_example_workload_kinds_all_three():
    s = load_scenario(EXAMPLE_PATH)
    kinds = {w.kind for w in s.workloads}
    assert kinds == {"compare_task", "lineage_script", "slow_query"}


def test_anomaly_extra_fields_pass_through():
    """anomaly model_config=extra='allow' —— kind-specific 字段（perturbation
    / store_as / ...）要透传到 model_extra，给 generator 用。"""
    s = load_scenario(EXAMPLE_PATH)
    drift = next(a for a in s.anomalies if a.kind == "value_drift")
    extra = drift.model_extra or {}
    assert "perturbation" in extra
    assert extra["perturbation"] == "±1%"


def test_workload_expected_field_pass_through():
    """compare_task workload 的 expected: 块（ground truth）应透传。"""
    s = load_scenario(EXAMPLE_PATH)
    comp = next(w for w in s.workloads if w.kind == "compare_task")
    extra = comp.model_extra or {}
    assert extra.get("source") == "ods.orders"
    assert extra.get("target") == "dwd.orders_clean"
    assert extra.get("keys") == ["order_id"]
    expected = extra.get("expected") or {}
    assert expected.get("only_source") == 20


# ─── 校验失败路径 ────────────────────────────────────────────────────────────


def test_scenario_rejects_unknown_top_level_field():
    """`extra='forbid'` 拦 YAML 笔误（如 dialec 写成 dialcet）。"""
    with pytest.raises(ValidationError):
        Scenario.model_validate({
            "id": "x", "name": "X", "tables": [],
            "dialcet": "mysql",  # 拼错
        })


def test_scenario_rejects_unknown_anomaly_kind():
    with pytest.raises(ValidationError):
        Scenario.model_validate({
            "id": "x", "name": "X", "tables": [],
            "anomalies": [{"kind": "bogus_kind", "table": "t1"}],
        })


def test_scenario_rejects_unknown_workload_kind():
    with pytest.raises(ValidationError):
        Scenario.model_validate({
            "id": "x", "name": "X", "tables": [],
            "workloads": [{"kind": "bogus_workload"}],
        })


def test_scenario_id_pattern_enforced():
    """id 必须 lowercase + alphanum + - / _，不允许大写 / 空格。"""
    for bad in ("Upper", "has space", "Has-Cap", "中文"):
        with pytest.raises(ValidationError):
            Scenario.model_validate({"id": bad, "name": "X", "tables": []})


def test_load_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        load_scenario("/nonexistent/path.yml")


def test_load_top_level_non_dict_raises(tmp_path):
    bad = tmp_path / "bad.yml"
    bad.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mapping at top level"):
        load_scenario(bad)


def test_rows_upper_bound_clamps_million(tmp_path):
    """rows 上限 1M（防 LLM / 用户写飞 → OOM）。"""
    with pytest.raises(ValidationError):
        Scenario.model_validate({
            "id": "x", "name": "X",
            "tables": [{"name": "t", "role": "source", "rows": 10_000_000}],
        })


# ─── list_scenarios ─────────────────────────────────────────────────────────


def test_list_scenarios_empty_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(scenarios_loader, "SCENARIOS_DIR", tmp_path)
    assert list_scenarios() == []


def test_list_scenarios_returns_user_files(tmp_path, monkeypatch):
    monkeypatch.setattr(scenarios_loader, "SCENARIOS_DIR", tmp_path)
    (tmp_path / "mine.yml").write_text(
        yaml.safe_dump({
            "id": "my-test",
            "name": "Mine",
            "tables": [{"name": "t1", "role": "source"}],
        }), encoding="utf-8",
    )
    out = list_scenarios()
    assert len(out) == 1
    assert out[0]["id"] == "my-test"
    assert "error" not in out[0]


def test_list_scenarios_falls_back_to_example(tmp_path, monkeypatch):
    """没有用户文件时，example 当 fallback 列出来 —— 新部署也能看到 demo。"""
    monkeypatch.setattr(scenarios_loader, "SCENARIOS_DIR", tmp_path)
    (tmp_path / "demo.example.yml").write_text(
        yaml.safe_dump({
            "id": "demo",
            "name": "Demo",
            "tables": [{"name": "t1", "role": "source"}],
        }), encoding="utf-8",
    )
    out = list_scenarios()
    assert len(out) == 1
    assert out[0]["id"] == "demo"


def test_list_scenarios_user_files_shadow_example(tmp_path, monkeypatch):
    """有用户文件时，example 默认不列（避免 demo 抢真实场景的位置）。"""
    monkeypatch.setattr(scenarios_loader, "SCENARIOS_DIR", tmp_path)
    (tmp_path / "demo.example.yml").write_text(
        yaml.safe_dump({
            "id": "demo", "name": "Demo",
            "tables": [{"name": "t1", "role": "source"}],
        }), encoding="utf-8",
    )
    (tmp_path / "real.yml").write_text(
        yaml.safe_dump({
            "id": "real", "name": "Real",
            "tables": [{"name": "t1", "role": "source"}],
        }), encoding="utf-8",
    )
    out = list_scenarios()
    ids = {e["id"] for e in out}
    assert ids == {"real"}


def test_list_scenarios_invalid_file_keeps_listing(tmp_path, monkeypatch):
    """坏文件也要列出来（带 error 字段），UI 可显示"打不开 + 原因"。"""
    monkeypatch.setattr(scenarios_loader, "SCENARIOS_DIR", tmp_path)
    (tmp_path / "broken.yml").write_text("id: bad\nname: B\n# 缺 tables 字段\n", encoding="utf-8")
    out = list_scenarios()
    assert len(out) == 1
    assert "error" in out[0]


# ─── 类型导入 sanity ────────────────────────────────────────────────────────


def test_models_export_intended_symbols():
    """防回归：模型符号别误删 / 误改名。"""
    assert AnomalyDef.__name__ == "AnomalyDef"
    assert WorkloadDef.__name__ == "WorkloadDef"
    assert Scenario.__name__ == "Scenario"
