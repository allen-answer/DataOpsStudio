"""Scenario generator tests（Phase 12 切片 2）。

scope: `app.scenarios.generator.generate_scenario`
- 7 个 column generator（uuid_short / random_int+zipf / realistic / timestamp /
  enum+weights / constant / sequence）
- derives_from + column_overrides（rename / transform DATE($)）
- 6 个 anomaly kind（missing/extra/value_drift/null_drift/duplicate_pk/type_mismatch）
- end-to-end with the bundled example.yml
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from app.scenarios.generator import generate_scenario
from app.scenarios.loader import load_scenario
from app.scenarios.models import Scenario
from app.utils.paths import BASE_DIR


EXAMPLE_PATH = BASE_DIR / "config" / "scenarios" / "orders-recon.example.yml"


def _scenario(**kwargs: Any) -> Scenario:
    payload = {"id": "test", "name": "T", "seed": 42}
    payload.update(kwargs)
    return Scenario.model_validate(payload)


# ─── basics ────────────────────────────────────────────────────────────────


def test_generate_empty_scenario():
    s = _scenario(tables=[])
    assert generate_scenario(s) == {}


def test_single_table_generates_rows():
    s = _scenario(tables=[{
        "name": "t1", "role": "source", "rows": 50,
        "columns": [
            {"name": "id", "type": "INT", "pk": True, "gen": "sequence"},
            {"name": "amt", "type": "DECIMAL(10,2)", "gen": "realistic"},
        ],
    }])
    out = generate_scenario(s)
    assert list(out.keys()) == ["t1"]
    assert len(out["t1"]) == 50
    assert set(out["t1"][0].keys()) == {"id", "amt"}


def test_seed_deterministic_across_runs():
    spec = {
        "tables": [{
            "name": "t1", "role": "source", "rows": 20,
            "columns": [
                {"name": "id", "type": "VARCHAR(32)", "pk": True, "gen": "uuid_short"},
                {"name": "n", "type": "INT", "gen": "random_int", "range": [1, 100]},
            ],
        }],
    }
    assert generate_scenario(_scenario(**spec)) == generate_scenario(_scenario(**spec))


# ─── per generator ─────────────────────────────────────────────────────────


def test_uuid_short_is_12_hex():
    s = _scenario(tables=[{
        "name": "t", "role": "source", "rows": 5,
        "columns": [{"name": "k", "type": "VARCHAR(32)", "pk": True, "gen": "uuid_short"}],
    }])
    for row in generate_scenario(s)["t"]:
        v = row["k"]
        assert isinstance(v, str) and len(v) == 12
        assert all(c in "abcdef0123456789" for c in v)


def test_random_int_within_range():
    s = _scenario(tables=[{
        "name": "t", "role": "source", "rows": 100,
        "columns": [{
            "name": "n", "type": "INT", "gen": "random_int", "range": [5, 15],
        }],
    }])
    assert all(5 <= r["n"] <= 15 for r in generate_scenario(s)["t"])


def test_random_int_zipf_skewed_low():
    s = _scenario(tables=[{
        "name": "t", "role": "source", "rows": 500,
        "columns": [{
            "name": "n", "type": "INT", "gen": "random_int",
            "range": [1, 100], "distribution": "zipf", "zipf_alpha": 1.5,
        }],
    }])
    out = generate_scenario(s)["t"]
    low_count = sum(1 for r in out if r["n"] <= 10)
    assert low_count > 250  # zipf alpha=1.5: 至少 50% 在前 10 桶


def test_timestamp_within_iso_range():
    s = _scenario(tables=[{
        "name": "t", "role": "source", "rows": 20,
        "columns": [{
            "name": "ts", "type": "DATETIME", "gen": "timestamp",
            "range": ["2026-01-01", "2026-01-31"],
        }],
    }])
    start, end = datetime(2026, 1, 1), datetime(2026, 1, 31)
    for r in generate_scenario(s)["t"]:
        assert start <= r["ts"] <= end


def test_enum_uses_only_listed_values():
    s = _scenario(tables=[{
        "name": "t", "role": "source", "rows": 100,
        "columns": [{
            "name": "s", "type": "VARCHAR(8)", "gen": "enum",
            "values": ["a", "b", "c"],
        }],
    }])
    assert {r["s"] for r in generate_scenario(s)["t"]} <= {"a", "b", "c"}


def test_enum_with_weights_respects_distribution():
    s = _scenario(tables=[{
        "name": "t", "role": "source", "rows": 1000,
        "columns": [{
            "name": "s", "type": "VARCHAR(8)", "gen": "enum",
            "values": ["a", "b"],
            "distribution": [0.9, 0.1],
        }],
    }])
    a_count = sum(1 for r in generate_scenario(s)["t"] if r["s"] == "a")
    assert a_count > 800  # ~900 expected，给 100 buffer


def test_constant_returns_first_value():
    s = _scenario(tables=[{
        "name": "t", "role": "source", "rows": 5,
        "columns": [{
            "name": "c", "type": "VARCHAR(8)", "gen": "constant",
            "values": ["X"],
        }],
    }])
    assert all(r["c"] == "X" for r in generate_scenario(s)["t"])


def test_sequence_starts_at_one():
    s = _scenario(tables=[{
        "name": "t", "role": "source", "rows": 5,
        "columns": [{
            "name": "i", "type": "INT", "pk": True, "gen": "sequence",
        }],
    }])
    assert [r["i"] for r in generate_scenario(s)["t"]] == [1, 2, 3, 4, 5]


def test_sequence_with_prefix_returns_string():
    s = _scenario(tables=[{
        "name": "t", "role": "source", "rows": 3,
        "columns": [{
            "name": "id", "type": "VARCHAR(8)", "pk": True, "gen": "sequence",
            "values": ["A"],
        }],
    }])
    assert [r["id"] for r in generate_scenario(s)["t"]] == ["A1", "A2", "A3"]


def test_realistic_decimal_type():
    s = _scenario(tables=[{
        "name": "t", "role": "source", "rows": 10,
        "columns": [{
            "name": "amt", "type": "DECIMAL(10,2)", "gen": "realistic",
        }],
    }])
    for r in generate_scenario(s)["t"]:
        assert isinstance(r["amt"], float)
        assert 10.0 <= r["amt"] <= 5000.0


# ─── derives_from + transform ──────────────────────────────────────────────


def test_derives_from_copies_source_rows():
    s = _scenario(tables=[
        {
            "name": "src", "role": "source", "rows": 10,
            "columns": [
                {"name": "id", "type": "INT", "pk": True, "gen": "sequence"},
            ],
        },
        {
            "name": "tgt", "role": "target", "rows": 10,
            "derives_from": "src",
        },
    ])
    out = generate_scenario(s)
    assert out["tgt"] == out["src"]


def test_column_override_rename_only():
    s = _scenario(tables=[
        {
            "name": "src", "role": "source", "rows": 3,
            "columns": [
                {"name": "id", "type": "INT", "pk": True, "gen": "sequence"},
                {"name": "x", "type": "INT", "gen": "constant", "values": [42]},
            ],
        },
        {
            "name": "tgt", "role": "target", "rows": 3,
            "derives_from": "src",
            "column_overrides": [{"from": "x", "rename": "x_new"}],
        },
    ])
    out = generate_scenario(s)
    assert set(out["tgt"][0].keys()) == {"id", "x_new"}
    assert all(r["x_new"] == 42 for r in out["tgt"])


def test_column_override_transform_DATE_strips_time():
    s = _scenario(tables=[
        {
            "name": "src", "role": "source", "rows": 3,
            "columns": [
                {"name": "id", "type": "INT", "pk": True, "gen": "sequence"},
                {"name": "ts", "type": "DATETIME", "gen": "timestamp",
                 "range": ["2026-04-01", "2026-04-30"]},
            ],
        },
        {
            "name": "tgt", "role": "target", "rows": 3,
            "derives_from": "src",
            "column_overrides": [
                {"from": "ts", "rename": "d", "transform": "DATE($)"},
            ],
        },
    ])
    out = generate_scenario(s)
    for row in out["tgt"]:
        assert isinstance(row["d"], date)
        assert not isinstance(row["d"], datetime)  # date not datetime


# ─── anomalies ─────────────────────────────────────────────────────────────


def _two_table_scenario(anomalies: list[dict[str, Any]]) -> Scenario:
    return _scenario(
        tables=[
            {
                "name": "src", "role": "source", "rows": 100,
                "columns": [
                    {"name": "id", "type": "VARCHAR(32)", "pk": True, "gen": "uuid_short"},
                    {"name": "amt", "type": "DECIMAL(10,2)", "gen": "realistic"},
                    {"name": "ext", "type": "VARCHAR(8)", "gen": "constant",
                     "values": ["x"]},
                ],
            },
            {"name": "tgt", "role": "target", "rows": 100, "derives_from": "src"},
        ],
        anomalies=anomalies,
    )


def test_anomaly_missing_rows_drops_fraction():
    s = _two_table_scenario([
        {"kind": "missing_rows", "table": "tgt", "fraction": 0.1},
    ])
    out = generate_scenario(s)
    assert len(out["tgt"]) == 90


def test_anomaly_count_beats_fraction():
    s = _two_table_scenario([
        {"kind": "missing_rows", "table": "tgt", "count": 5, "fraction": 0.99},
    ])
    out = generate_scenario(s)
    assert len(out["tgt"]) == 95


def test_anomaly_extra_rows_adds_count_with_fresh_pks():
    s = _two_table_scenario([
        {"kind": "extra_rows", "table": "tgt", "count": 7},
    ])
    out = generate_scenario(s)
    assert len(out["tgt"]) == 107
    src_ids = {r["id"] for r in out["src"]}
    extras = [r["id"] for r in out["tgt"] if r["id"] not in src_ids]
    assert len(extras) == 7  # 7 个新 PK 都不与 source 撞


def test_anomaly_value_drift_changes_target_column():
    s = _two_table_scenario([
        {"kind": "value_drift", "table": "tgt", "column": "amt",
         "fraction": 0.2, "perturbation": "5%"},
    ])
    out = generate_scenario(s)
    diff = sum(
        1 for src, tgt in zip(out["src"], out["tgt"])
        if src["amt"] != tgt["amt"]
    )
    assert 15 <= diff <= 25  # 20 期望，留 5 buffer


def test_anomaly_value_drift_default_perturbation():
    s = _two_table_scenario([
        {"kind": "value_drift", "table": "tgt", "column": "amt", "fraction": 0.1},
    ])
    out = generate_scenario(s)
    # 默认 perturb=0.02，drift 后值应在原值 ±2% 内
    for src, tgt in zip(out["src"], out["tgt"]):
        if src["amt"] != tgt["amt"]:
            assert abs(tgt["amt"] - src["amt"]) / src["amt"] <= 0.025


def test_anomaly_null_drift_sets_none():
    s = _two_table_scenario([
        {"kind": "null_drift", "table": "tgt", "column": "amt", "fraction": 0.1},
    ])
    out = generate_scenario(s)
    null_count = sum(1 for r in out["tgt"] if r["amt"] is None)
    assert null_count == 10


def test_anomaly_duplicate_pk_creates_dupes():
    s = _two_table_scenario([
        {"kind": "duplicate_pk", "table": "tgt", "count": 3},
    ])
    out = generate_scenario(s)
    ids = [r["id"] for r in out["tgt"]]
    assert len(ids) == 103  # 原 100 + 3 重复
    assert len(set(ids)) == 100  # 唯一 PK 数仍 100


def test_anomaly_type_mismatch_casts_to_string():
    s = _two_table_scenario([
        {"kind": "type_mismatch", "table": "tgt", "column": "amt", "fraction": 0.05},
    ])
    str_count = sum(1 for r in generate_scenario(s)["tgt"] if isinstance(r["amt"], str))
    assert str_count == 5


def test_unknown_anomaly_table_silently_skipped():
    s = _two_table_scenario([
        {"kind": "missing_rows", "table": "no_such_table", "fraction": 0.5},
    ])
    out = generate_scenario(s)
    assert len(out["tgt"]) == 100  # 未变


# ─── end-to-end with bundled example.yml ───────────────────────────────────


def test_example_yml_end_to_end():
    scenario = load_scenario(EXAMPLE_PATH)
    out = generate_scenario(scenario)

    # 两表都生成
    assert set(out.keys()) == {"ods.orders", "dwd.orders_clean"}

    # 数学：1000 source / 1000 derives - 20 missing(2%) + 5 extras = 985
    assert len(out["ods.orders"]) == 1000
    assert len(out["dwd.orders_clean"]) == 985

    # source 字段齐
    src_sample = out["ods.orders"][0]
    assert set(src_sample.keys()) == {"order_id", "user_id", "amount", "created_at", "status"}
    assert isinstance(src_sample["created_at"], datetime)

    # target column_override：created_at → order_date 且变成 date
    dwd_sample = out["dwd.orders_clean"][0]
    assert "order_date" in dwd_sample and "created_at" not in dwd_sample
    assert isinstance(dwd_sample["order_date"], date)

    # null_drift fraction=0.005，apply 时表已是 985 行（missing-20+extras+5），int(985*0.005)=4
    null_user_ids = sum(1 for r in out["dwd.orders_clean"] if r["user_id"] is None)
    assert 3 <= null_user_ids <= 6

    # value_drift fraction=0.01，int(985*0.01)=9 行被打偏；其中可能命中 extras
    # 而 extras 的 order_id 不在 src 里，无法 join 比对，所以可见 drift 数 ≤ 9
    src_by_id = {r["order_id"]: r for r in out["ods.orders"]}
    drift_count = 0
    for r in out["dwd.orders_clean"]:
        src = src_by_id.get(r["order_id"])
        if src and r["amount"] != src["amount"] and r["amount"] is not None:
            drift_count += 1
    assert 4 <= drift_count <= 12  # 9 期望，留 buffer
