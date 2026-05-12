"""Scenario recorder tests（Phase 12 切片 4）。

scope:
- `build_compare_tasks`：workload → CompareTaskCreate 翻译（纯函数）
- `record_scenario`：build + task_store.create 持久化（用 isolated_storage）
- warning 路径：缺 source/target/keys / 表名不在 scenario / 持久化失败
"""
from __future__ import annotations

from typing import Any

import pytest

from app.models.common import SourceKind, SqlMode
from app.scenarios.loader import load_scenario
from app.scenarios.models import Scenario
from app.scenarios.recorder import build_compare_tasks, record_scenario
from app.utils.paths import BASE_DIR


EXAMPLE_PATH = BASE_DIR / "config" / "scenarios" / "orders-recon.example.yml"


def _scenario(**kwargs: Any) -> Scenario:
    payload = {"id": "test", "name": "T", "seed": 42}
    payload.update(kwargs)
    return Scenario.model_validate(payload)


def _basic_scenario(workloads: list[dict[str, Any]]) -> Scenario:
    return _scenario(
        tables=[
            {"name": "ods.t", "role": "source", "rows": 0,
             "columns": [
                 {"name": "id", "type": "INT", "pk": True, "gen": "sequence"},
                 {"name": "x", "type": "INT", "gen": "constant", "values": [1]},
             ]},
            {"name": "dwd.t", "role": "target", "rows": 0, "derives_from": "ods.t"},
        ],
        workloads=workloads,
    )


# ─── build_compare_tasks: 翻译正确性 ─────────────────────────────────────────


def test_build_compare_task_basic_shape():
    s = _basic_scenario([{
        "kind": "compare_task", "name": "daily-recon",
        "source": "ods.t", "target": "dwd.t", "keys": ["id"],
    }])
    out = build_compare_tasks(s, datasource_id="ds-mysql-1")
    assert len(out.payloads) == 1
    p = out.payloads[0]
    assert p.name == "test · daily-recon"
    assert p.source_kind == SourceKind.SQL
    assert p.target_kind == SourceKind.SQL
    assert p.source_id == "ds-mysql-1"
    assert p.target_id == "ds-mysql-1"
    assert p.sql_mode == SqlMode.DOUBLE
    assert p.key_columns == ["id"]


def test_build_compare_task_select_lists_columns_with_pk_order():
    s = _basic_scenario([{
        "kind": "compare_task", "name": "x",
        "source": "ods.t", "target": "dwd.t", "keys": ["id"],
    }])
    p = build_compare_tasks(s, datasource_id="ds").payloads[0]
    # source 显式列名 + ORDER BY pk
    assert p.source_sql == "SELECT `id`, `x` FROM `ods`.`t` ORDER BY `id`"
    # target derives_from 继承同样的列，schema 不同
    assert p.target_sql == "SELECT `id`, `x` FROM `dwd`.`t` ORDER BY `id`"


def test_build_compare_task_composite_keys_in_order_by():
    s = _scenario(
        tables=[
            {"name": "src", "role": "source", "rows": 0, "columns": [
                {"name": "a", "type": "INT", "pk": True, "gen": "sequence"},
                {"name": "b", "type": "INT", "pk": True, "gen": "sequence"},
            ]},
            {"name": "tgt", "role": "target", "rows": 0, "derives_from": "src"},
        ],
        workloads=[{
            "kind": "compare_task", "name": "w",
            "source": "src", "target": "tgt", "keys": ["a", "b"],
        }],
    )
    p = build_compare_tasks(s, datasource_id="ds").payloads[0]
    assert p.source_sql.endswith("ORDER BY `a`, `b`")
    assert p.key_columns == ["a", "b"]


def test_build_compare_task_uses_renamed_target_columns():
    s = _scenario(
        tables=[
            {"name": "src", "role": "source", "rows": 0, "columns": [
                {"name": "id", "type": "INT", "pk": True, "gen": "sequence"},
                {"name": "ts", "type": "DATETIME", "gen": "timestamp"},
            ]},
            {
                "name": "tgt", "role": "target", "rows": 0,
                "derives_from": "src",
                "column_overrides": [{"from": "ts", "rename": "d", "transform": "DATE($)"}],
            },
        ],
        workloads=[{
            "kind": "compare_task", "name": "w",
            "source": "src", "target": "tgt", "keys": ["id"],
        }],
    )
    p = build_compare_tasks(s, datasource_id="ds").payloads[0]
    assert "`ts`" in p.source_sql
    assert "`d`" in p.target_sql
    assert "`ts`" not in p.target_sql  # renamed away


def test_build_compare_task_project_id_passthrough():
    s = _basic_scenario([{
        "kind": "compare_task", "name": "w",
        "source": "ods.t", "target": "dwd.t", "keys": ["id"],
    }])
    p = build_compare_tasks(s, datasource_id="ds", project_id="proj-1").payloads[0]
    assert p.project_id == "proj-1"


def test_build_compare_task_only_compare_kind_picked_up():
    s = _basic_scenario([
        {"kind": "compare_task", "name": "c", "source": "ods.t", "target": "dwd.t", "keys": ["id"]},
        {"kind": "lineage_script", "name": "l", "sql": "SELECT 1"},
        {"kind": "slow_query", "name": "s", "sql": "SELECT 1"},
    ])
    out = build_compare_tasks(s, datasource_id="ds")
    assert len(out.payloads) == 1
    assert out.payloads[0].name == "test · c"


# ─── warnings 路径 ───────────────────────────────────────────────────────────


def test_warns_when_source_missing():
    s = _basic_scenario([{
        "kind": "compare_task", "name": "w",
        "target": "dwd.t", "keys": ["id"],
    }])
    out = build_compare_tasks(s, datasource_id="ds")
    assert out.payloads == []
    assert len(out.warnings) == 1
    assert "missing source/target" in out.warnings[0].reason


def test_warns_when_keys_missing():
    s = _basic_scenario([{
        "kind": "compare_task", "name": "w",
        "source": "ods.t", "target": "dwd.t",
    }])
    out = build_compare_tasks(s, datasource_id="ds")
    assert out.payloads == []
    assert "missing keys" in out.warnings[0].reason


def test_warns_when_source_table_not_in_scenario():
    s = _basic_scenario([{
        "kind": "compare_task", "name": "w",
        "source": "no_such", "target": "dwd.t", "keys": ["id"],
    }])
    out = build_compare_tasks(s, datasource_id="ds")
    assert out.payloads == []
    assert "source table 'no_such' not in scenario" in out.warnings[0].reason


def test_anonymous_workload_uses_placeholder_in_warning():
    s = _basic_scenario([{
        "kind": "compare_task",  # 无 name
        "target": "dwd.t", "keys": ["id"],
    }])
    out = build_compare_tasks(s, datasource_id="ds")
    assert out.warnings[0].workload_name == "<anonymous>"


def test_anonymous_workload_default_compare_name():
    s = _basic_scenario([{
        "kind": "compare_task",  # 无 name → "source vs target"
        "source": "ods.t", "target": "dwd.t", "keys": ["id"],
    }])
    p = build_compare_tasks(s, datasource_id="ds").payloads[0]
    assert p.name == "test · ods.t vs dwd.t"


def test_empty_datasource_id_raises():
    s = _basic_scenario([])
    with pytest.raises(ValueError, match="datasource_id"):
        build_compare_tasks(s, datasource_id="")


# ─── record_scenario: 持久化 ─────────────────────────────────────────────────


def test_record_scenario_creates_tasks(isolated_storage):
    s = _basic_scenario([
        {"kind": "compare_task", "name": "w1", "source": "ods.t", "target": "dwd.t", "keys": ["id"]},
        {"kind": "compare_task", "name": "w2", "source": "ods.t", "target": "dwd.t", "keys": ["id"]},
    ])
    res = record_scenario(s, datasource_id="ds-1")
    assert len(res["tasks"]) == 2
    assert res["warnings"] == []
    # 真的写进了 task_store
    from app.services.repositories import task_store
    stored = task_store.list()
    assert {t.name for t in stored} >= {"test · w1", "test · w2"}


def test_record_scenario_propagates_warnings(isolated_storage):
    s = _basic_scenario([
        {"kind": "compare_task", "name": "ok", "source": "ods.t", "target": "dwd.t", "keys": ["id"]},
        {"kind": "compare_task", "name": "bad", "source": "ods.t", "target": "dwd.t"},  # 缺 keys
    ])
    res = record_scenario(s, datasource_id="ds-1")
    assert len(res["tasks"]) == 1
    assert len(res["warnings"]) == 1
    assert res["warnings"][0]["workload_name"] == "bad"


# ─── end-to-end with example yml ────────────────────────────────────────────


def test_example_yml_records_one_compare_task(isolated_storage):
    s = load_scenario(EXAMPLE_PATH)
    res = record_scenario(s, datasource_id="demo-mysql", project_id="demo")
    # example 里有 1 个 compare_task workload（slow_query 跳过；lineage_script 走切片 12）
    assert len(res["tasks"]) == 1
    t = res["tasks"][0]
    assert t.project_id == "demo"
    assert t.source_id == "demo-mysql"
    assert t.target_id == "demo-mysql"
    assert t.key_columns == ["order_id"]
    assert "`ods`.`orders`" in t.source_sql
    assert "`dwd`.`orders_clean`" in t.target_sql
    assert t.sql_mode == SqlMode.DOUBLE


# ─── lineage_script workload（切片 12） ─────────────────────────────────────


def test_lineage_script_workload_produces_history_entry(isolated_storage):
    s = _basic_scenario([{
        "kind": "lineage_script", "name": "orders-etl",
        "sql": "INSERT INTO dwd.t SELECT id, x FROM ods.t WHERE x > 0;",
    }])
    res = record_scenario(s, datasource_id="ds-1")
    assert len(res["lineage_runs"]) == 1
    run = res["lineage_runs"][0]
    assert run["ok"] is True
    assert run["workload_name"] == "orders-etl"
    assert run["run_id"].startswith("lineage_script_")

    # history JSON 真的落到 results/
    json_path = isolated_storage["results"] / f"{run['run_id']}.json"
    assert json_path.exists()
    import json as _json
    data = _json.loads(json_path.read_text(encoding="utf-8"))
    assert data["type"] == "lineage"
    assert data["task_name"] == "test · orders-etl"
    assert data["sql"].startswith("INSERT INTO dwd.t")
    assert "table_edges" in data  # classifier 靠它落 type=lineage


def test_lineage_script_missing_sql_warns(isolated_storage):
    s = _basic_scenario([{
        "kind": "lineage_script", "name": "empty",
        # 缺 sql
    }])
    res = record_scenario(s, datasource_id="ds-1")
    assert len(res["lineage_runs"]) == 1
    assert res["lineage_runs"][0]["ok"] is False
    assert "missing sql" in res["lineage_runs"][0]["error"]


def test_lineage_script_analyzer_error_captured(isolated_storage, monkeypatch):
    """analyzer 抛错应进 lineage_runs[*].error，不中断 record_scenario。"""
    from app.lineage import analyzer as analyzer_mod

    def boom(*a, **kw):
        raise RuntimeError("sqlglot exploded")

    monkeypatch.setattr(analyzer_mod, "analyze_sql_lineage", boom)

    s = _basic_scenario([
        {"kind": "compare_task", "name": "w1", "source": "ods.t",
         "target": "dwd.t", "keys": ["id"]},
        {"kind": "lineage_script", "name": "broken-sql",
         "sql": "INSERT INTO dwd.t SELECT * FROM ods.t;"},
    ])
    res = record_scenario(s, datasource_id="ds-1")
    # compare task 仍然成功创建
    assert len(res["tasks"]) == 1
    # lineage_run 标失败
    assert res["lineage_runs"][0]["ok"] is False
    assert "sqlglot exploded" in res["lineage_runs"][0]["error"]


def test_lineage_runs_empty_when_no_lineage_workload(isolated_storage):
    """没 lineage_script workload 时 lineage_runs=[]，不调 analyzer。"""
    s = _basic_scenario([
        {"kind": "compare_task", "name": "w", "source": "ods.t",
         "target": "dwd.t", "keys": ["id"]},
    ])
    res = record_scenario(s, datasource_id="ds-1")
    assert res["lineage_runs"] == []


def test_lineage_script_multiple_workloads_all_recorded(isolated_storage):
    s = _basic_scenario([
        {"kind": "lineage_script", "name": "etl-a",
         "sql": "INSERT INTO dwd.a SELECT * FROM ods.a;"},
        {"kind": "lineage_script", "name": "etl-b",
         "sql": "INSERT INTO dwd.b SELECT * FROM ods.b;"},
    ])
    res = record_scenario(s, datasource_id="ds-1")
    assert len(res["lineage_runs"]) == 2
    assert all(r["ok"] for r in res["lineage_runs"])
    # 两个独立 run_id（确保 generator 给 unique id）
    ids = {r["run_id"] for r in res["lineage_runs"]}
    assert len(ids) == 2
