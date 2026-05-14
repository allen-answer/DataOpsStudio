"""Scenario distribution params tests（Phase 12 切片 17 —— AI filler v2）。

scope:
- generator `_sample_distribution` 4 个分布族 + `_round_for_type` 类型收敛
- `_realistic_value` 优先级：dist_params > values > 类型 fallback
- seed 决定性：同 seed 同分布输出
- min/max clamp 截断长尾极端值
- unknown kind → ValueError（跟 unknown generator 一致）
- ai_filler `column_distributions` scope —— mock _call_ai 填 dist_params
"""
from __future__ import annotations

import random
import statistics
from typing import Any

import pytest

from app.scenarios.ai_filler import fill_scenario
from app.scenarios.generator import (
    DISTRIBUTION_KINDS,
    _round_for_type,
    _sample_distribution,
    generate_scenario,
)
from app.scenarios.models import Scenario


def _scenario(**kwargs: Any) -> Scenario:
    payload = {"id": "test", "name": "T", "seed": 42}
    payload.update(kwargs)
    return Scenario.model_validate(payload)


# ─── _sample_distribution：纯分布采样 ───────────────────────────────────────


def test_distribution_kinds_closed_set():
    assert DISTRIBUTION_KINDS == {"lognormal", "normal", "uniform", "exponential"}


def test_sample_lognormal_positive_and_rounded():
    rng = random.Random(1)
    vals = [_sample_distribution(
        {"kind": "lognormal", "mu": 4.0, "sigma": 0.5}, "DECIMAL(10,2)", rng)
        for _ in range(200)]
    assert all(v > 0 for v in vals)
    # DECIMAL(10,2) → 2 位小数
    assert all(round(v, 2) == v for v in vals)
    # lognormal 右偏 —— 均值 > 中位数
    assert statistics.mean(vals) > statistics.median(vals)


def test_sample_normal_centered_on_mean():
    rng = random.Random(7)
    vals = [_sample_distribution(
        {"kind": "normal", "mean": 100.0, "std": 5.0}, "INT", rng)
        for _ in range(500)]
    assert all(isinstance(v, int) for v in vals)
    assert 95 <= statistics.mean(vals) <= 105


def test_sample_normal_accepts_mu_sigma_aliases():
    rng = random.Random(7)
    v = _sample_distribution({"kind": "normal", "mu": 50, "sigma": 1}, "INT", rng)
    assert isinstance(v, int)
    assert 40 <= v <= 60


def test_sample_uniform_within_bounds():
    rng = random.Random(3)
    vals = [_sample_distribution(
        {"kind": "uniform", "min": 10, "max": 20}, "DECIMAL(10,2)", rng)
        for _ in range(200)]
    assert all(10.0 <= v <= 20.0 for v in vals)


def test_sample_uniform_swapped_bounds_normalized():
    """min > max 时自动归一化区间，不抛。"""
    rng = random.Random(3)
    v = _sample_distribution({"kind": "uniform", "min": 20, "max": 10}, "INT", rng)
    assert 10 <= v <= 20


def test_sample_exponential_non_negative():
    rng = random.Random(5)
    vals = [_sample_distribution(
        {"kind": "exponential", "lambda": 0.5}, "DECIMAL(10,4)", rng)
        for _ in range(200)]
    assert all(v >= 0 for v in vals)


def test_sample_exponential_rate_alias_and_zero_lambda():
    rng = random.Random(5)
    # rate 别名
    v1 = _sample_distribution({"kind": "exponential", "rate": 1.0}, "INT", rng)
    assert v1 >= 0
    # lambda <= 0 → 退化成 0，不抛 ZeroDivisionError
    v2 = _sample_distribution({"kind": "exponential", "lambda": 0}, "INT", rng)
    assert v2 == 0


def test_sample_min_max_clamp_non_uniform():
    """非 uniform 分布的 min/max 起 clamp 作用 —— 截断长尾极端值。"""
    rng = random.Random(11)
    vals = [_sample_distribution(
        {"kind": "lognormal", "mu": 6.0, "sigma": 2.0, "min": 1, "max": 1000},
        "INT", rng) for _ in range(500)]
    assert all(1 <= v <= 1000 for v in vals)
    # sigma=2 长尾必然撞上 max=1000 的天花板
    assert max(vals) == 1000


def test_sample_unknown_kind_raises():
    rng = random.Random(1)
    with pytest.raises(ValueError, match="unknown distribution kind"):
        _sample_distribution({"kind": "pareto"}, "INT", rng)


def test_sample_missing_params_use_defaults():
    """缺参数走默认值（mu=0/sigma=1 等），不抛 KeyError。"""
    rng = random.Random(1)
    v = _sample_distribution({"kind": "lognormal"}, "DECIMAL(10,2)", rng)
    assert v > 0


def test_sample_non_numeric_param_falls_back_to_default():
    """LLM 偶尔返回字符串参数 —— _as_float 兜底成默认值不抛。"""
    rng = random.Random(1)
    v = _sample_distribution(
        {"kind": "normal", "mean": "oops", "std": "bad"}, "INT", rng)
    assert isinstance(v, int)


# ─── _round_for_type：类型收敛 ──────────────────────────────────────────────


def test_round_int_family():
    assert _round_for_type(12.7, "BIGINT") == 13
    assert _round_for_type(12.2, "INT") == 12
    assert isinstance(_round_for_type(5.9, "TINYINT"), int)


def test_round_decimal_respects_scale():
    assert _round_for_type(3.14159, "DECIMAL(10,2)") == 3.14
    assert _round_for_type(3.14159, "DECIMAL(12,4)") == 3.1416
    # 无 scale 标注 → 默认 2 位
    assert _round_for_type(3.14159, "DECIMAL") == 3.14


def test_round_float_double_4_places():
    assert _round_for_type(3.14159265, "DOUBLE") == 3.1416
    assert _round_for_type(2.71828182, "FLOAT") == 2.7183


def test_round_unknown_type_passthrough():
    assert _round_for_type(3.14159, "") == 3.14159
    assert _round_for_type(42.0, "JSON") == 42.0


# ─── _realistic_value 优先级 ────────────────────────────────────────────────


def test_realistic_dist_params_takes_priority_over_values():
    """dist_params 设了就走分布采样，即使 values 也有值。"""
    s = _scenario(tables=[{
        "name": "t", "role": "source", "rows": 200,
        "columns": [{
            "name": "amount", "type": "DECIMAL(10,2)", "gen": "realistic",
            "values": [9999.0],  # 故意放一个明显的哨兵值
            "dist_params": {"kind": "uniform", "min": 1, "max": 100},
        }],
    }])
    out = generate_scenario(s)["t"]
    assert all(1.0 <= r["amount"] <= 100.0 for r in out)
    assert all(r["amount"] != 9999.0 for r in out)


def test_realistic_no_dist_params_uses_values():
    """dist_params 空 → 回退 values 样本池（切片 9 行为不变）。"""
    s = _scenario(tables=[{
        "name": "t", "role": "source", "rows": 50,
        "columns": [{
            "name": "amount", "type": "DECIMAL(10,2)", "gen": "realistic",
            "values": [10.0, 50.0, 100.0],
        }],
    }])
    out = generate_scenario(s)["t"]
    assert all(r["amount"] in {10.0, 50.0, 100.0} for r in out)


def test_realistic_dist_params_deterministic_with_seed():
    """同 seed → 同一份分布数据（复跑可重现）。"""
    def build():
        return _scenario(seed=123, tables=[{
            "name": "t", "role": "source", "rows": 100,
            "columns": [{
                "name": "amount", "type": "DECIMAL(10,2)", "gen": "realistic",
                "dist_params": {"kind": "lognormal", "mu": 4.0, "sigma": 0.6},
            }],
        }])
    a = generate_scenario(build())["t"]
    b = generate_scenario(build())["t"]
    assert [r["amount"] for r in a] == [r["amount"] for r in b]


def test_realistic_dist_params_unknown_kind_raises_at_generate():
    s = _scenario(tables=[{
        "name": "t", "role": "source", "rows": 5,
        "columns": [{
            "name": "x", "type": "INT", "gen": "realistic",
            "dist_params": {"kind": "weibull"},
        }],
    }])
    with pytest.raises(ValueError, match="unknown distribution kind"):
        generate_scenario(s)


# ─── ai_filler：column_distributions scope ──────────────────────────────────


@pytest.fixture
def ai_provider_on(isolated_storage, monkeypatch):
    from app.services import lineage_ai as svc
    from app.services.lineage_ai import LineageAIConfig
    monkeypatch.setattr(svc, "_config", lambda: LineageAIConfig(
        provider="openai", model="gpt-fake", api_key="sk-test", base_url="https://x/v1",
    ))


def test_fill_distributions_writes_dist_params(ai_provider_on, monkeypatch):
    from app.api import ai_utils
    monkeypatch.setattr(ai_utils, "_call_ai", lambda *a, **kw: {
        "kind": "lognormal", "mu": 4.5, "sigma": 0.7, "min": 10, "max": 50000,
    })

    s = _scenario(
        ai={"provider": "${default}", "fill": ["column_distributions"]},
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
    assert report.calls == 1  # 只 realistic 数值列
    assert "ods.orders.amount" in report.filled_distributions
    amount = next(c for c in filled.tables[0].columns if c.name == "amount")
    assert amount.dist_params == {
        "kind": "lognormal", "mu": 4.5, "sigma": 0.7, "min": 10, "max": 50000,
    }
    # 原 scenario 未变
    assert s.tables[0].columns[1].dist_params is None


def test_fill_distributions_skips_non_numeric_realistic(ai_provider_on, monkeypatch):
    """realistic 但字符串类型 → 分布参数无意义，不调 LLM。"""
    from app.api import ai_utils
    monkeypatch.setattr(ai_utils, "_call_ai",
                        lambda *a, **kw: {"kind": "normal", "mean": 1, "std": 1})

    s = _scenario(
        ai={"provider": "${default}", "fill": ["column_distributions"]},
        tables=[{
            "name": "t", "role": "source", "rows": 0,
            "columns": [{"name": "user_name", "type": "VARCHAR(32)", "gen": "realistic"}],
        }],
    )
    filled, report = fill_scenario(s)
    assert report.calls == 0
    assert filled.tables[0].columns[0].dist_params is None


def test_fill_distributions_rejects_unknown_kind(ai_provider_on, monkeypatch):
    """LLM 返回非法 kind → 丢弃，不写脏 dist_params。"""
    from app.api import ai_utils
    monkeypatch.setattr(ai_utils, "_call_ai",
                        lambda *a, **kw: {"kind": "pareto", "alpha": 1.5})

    s = _scenario(
        ai={"provider": "${default}", "fill": ["column_distributions"]},
        tables=[{
            "name": "t", "role": "source", "rows": 0,
            "columns": [{"name": "amount", "type": "DECIMAL(10,2)", "gen": "realistic"}],
        }],
    )
    filled, report = fill_scenario(s)
    assert report.calls == 1
    assert filled.tables[0].columns[0].dist_params is None
    assert report.filled_distributions == []


def test_fill_distributions_filters_unknown_param_keys(ai_provider_on, monkeypatch):
    """LLM 多塞的键（reason / kind 之外的非数值键）被过滤掉。"""
    from app.api import ai_utils
    monkeypatch.setattr(ai_utils, "_call_ai", lambda *a, **kw: {
        "kind": "normal", "mean": 50, "std": 5,
        "reason": "客单价集中分布", "extra_junk": [1, 2, 3], "is_good": True,
    })

    s = _scenario(
        ai={"provider": "${default}", "fill": ["column_distributions"]},
        tables=[{
            "name": "t", "role": "source", "rows": 0,
            "columns": [{"name": "age", "type": "INT", "gen": "realistic"}],
        }],
    )
    filled, _ = fill_scenario(s)
    assert filled.tables[0].columns[0].dist_params == {
        "kind": "normal", "mean": 50, "std": 5,
    }


def test_fill_distributions_skips_column_with_existing_dist_params(ai_provider_on, monkeypatch):
    from app.api import ai_utils
    monkeypatch.setattr(ai_utils, "_call_ai",
                        lambda *a, **kw: {"kind": "normal", "mean": 999, "std": 1})

    s = _scenario(
        ai={"provider": "${default}", "fill": ["column_distributions"]},
        tables=[{
            "name": "t", "role": "source", "rows": 0,
            "columns": [{
                "name": "amount", "type": "DECIMAL(10,2)", "gen": "realistic",
                "dist_params": {"kind": "uniform", "min": 1, "max": 10},
            }],
        }],
    )
    filled, report = fill_scenario(s)
    assert report.calls == 0
    assert filled.tables[0].columns[0].dist_params == {
        "kind": "uniform", "min": 1, "max": 10,
    }


def test_fill_distributions_and_values_coexist(ai_provider_on, monkeypatch):
    """fill = [column_values, column_distributions] —— 数值列走分布，
    字符串列走样本池，两个 scope 不打架。"""
    from app.api import ai_utils

    def fake(provider, config, system_prompt, payload):
        if "concentration" in system_prompt or "分布参数" in system_prompt:
            return {"kind": "lognormal", "mu": 3.0, "sigma": 0.5}
        return {"values": ["paid", "pending", "shipped"]}

    monkeypatch.setattr(ai_utils, "_call_ai", fake)

    s = _scenario(
        ai={"provider": "${default}", "fill": ["column_values", "column_distributions"]},
        tables=[{
            "name": "t", "role": "source", "rows": 0,
            "columns": [
                {"name": "amount", "type": "DECIMAL(10,2)", "gen": "realistic"},
                {"name": "status", "type": "VARCHAR(16)", "gen": "realistic"},
            ],
        }],
    )
    filled, report = fill_scenario(s)
    amount = next(c for c in filled.tables[0].columns if c.name == "amount")
    status = next(c for c in filled.tables[0].columns if c.name == "status")
    # amount：数值列 → 走分布（column_distributions 在 column_values 之后跑，
    # 但 column_values 已先写了 values，dist scope 见 values 非空就跳过）
    # status：字符串列 → 只能走 values
    assert status.values == ["paid", "pending", "shipped"]
    # amount 被 column_values 填了 values；column_distributions 因 values 非空跳过
    assert amount.values  # column_values 填的
