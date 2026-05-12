"""Scenario materializer tests（Phase 12 切片 3）。

scope: build_materialize_plan + apply_plan + CursorExecutor
- DDL：CREATE DATABASE / CREATE TABLE / PK / NOT NULL / 索引
- INSERT：列顺序 + 占位符数 + 参数 tuple 对齐
- derives_from + column_overrides 在 DDL 里正确出现
- apply_plan 调用顺序 + 批次切分 + 空行表跳过 INSERT
- end-to-end with example.yml
"""
from __future__ import annotations

from typing import Any, Sequence

import pytest

from app.scenarios.generator import generate_scenario
from app.scenarios.loader import load_scenario
from app.scenarios.materializer import (
    CursorExecutor,
    MaterializePlan,
    apply_plan,
    build_materialize_plan,
)
from app.scenarios.models import Scenario
from app.utils.paths import BASE_DIR


EXAMPLE_PATH = BASE_DIR / "config" / "scenarios" / "orders-recon.example.yml"


def _scenario(**kwargs: Any) -> Scenario:
    payload = {"id": "test", "name": "T", "seed": 42}
    payload.update(kwargs)
    return Scenario.model_validate(payload)


# ─── FakeExecutor (records all SQL + params) ───────────────────────────────


class FakeExecutor:
    def __init__(self):
        self.calls: list[tuple[str, str, Any]] = []  # (op, sql, params_or_batch)

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> None:
        self.calls.append(("execute", sql, params))

    def executemany(self, sql: str, params_list: list[Sequence[Any]]) -> None:
        self.calls.append(("executemany", sql, list(params_list)))


# ─── plan: schema / drop / DDL basics ──────────────────────────────────────


def test_plan_schema_qualified_name_emits_create_database():
    s = _scenario(tables=[{
        "name": "ods.orders", "role": "source", "rows": 0,
        "columns": [{"name": "id", "type": "INT", "pk": True, "gen": "sequence"}],
    }])
    plan = build_materialize_plan(s, {})
    assert plan.schemas == ["CREATE DATABASE IF NOT EXISTS `ods`"]


def test_plan_unqualified_name_no_create_database():
    s = _scenario(tables=[{
        "name": "orders", "role": "source", "rows": 0,
        "columns": [{"name": "id", "type": "INT", "pk": True, "gen": "sequence"}],
    }])
    plan = build_materialize_plan(s, {})
    assert plan.schemas == []


def test_plan_multiple_tables_dedup_schemas():
    s = _scenario(tables=[
        {"name": "ods.a", "role": "source", "rows": 0,
         "columns": [{"name": "id", "type": "INT", "gen": "sequence"}]},
        {"name": "ods.b", "role": "source", "rows": 0,
         "columns": [{"name": "id", "type": "INT", "gen": "sequence"}]},
        {"name": "dwd.c", "role": "source", "rows": 0,
         "columns": [{"name": "id", "type": "INT", "gen": "sequence"}]},
    ])
    plan = build_materialize_plan(s, {})
    # 顺序保留：先 ods 再 dwd，ods 不重复
    assert plan.schemas == [
        "CREATE DATABASE IF NOT EXISTS `ods`",
        "CREATE DATABASE IF NOT EXISTS `dwd`",
    ]


def test_plan_drop_first_true_emits_drop():
    s = _scenario(tables=[{
        "name": "t", "role": "source", "rows": 0,
        "columns": [{"name": "id", "type": "INT", "gen": "sequence"}],
    }])
    plan = build_materialize_plan(s, {}, drop_first=True)
    assert plan.tables[0].drop_sql == "DROP TABLE IF EXISTS `t`"


def test_plan_drop_first_false_no_drop():
    s = _scenario(tables=[{
        "name": "t", "role": "source", "rows": 0,
        "columns": [{"name": "id", "type": "INT", "gen": "sequence"}],
    }])
    plan = build_materialize_plan(s, {}, drop_first=False)
    assert plan.tables[0].drop_sql is None


def test_create_sql_includes_primary_key():
    s = _scenario(tables=[{
        "name": "t", "role": "source", "rows": 0,
        "columns": [
            {"name": "id", "type": "VARCHAR(32)", "pk": True, "gen": "uuid_short"},
            {"name": "n", "type": "INT", "gen": "random_int"},
        ],
    }])
    create = build_materialize_plan(s, {}).tables[0].create_sql
    assert "PRIMARY KEY (`id`)" in create
    assert "`id` VARCHAR(32)" in create
    assert "`n` INT" in create


def test_create_sql_not_null_flag():
    s = _scenario(tables=[{
        "name": "t", "role": "source", "rows": 0,
        "columns": [{"name": "id", "type": "INT", "pk": True, "gen": "sequence", "nullable": False}],
    }])
    create = build_materialize_plan(s, {}).tables[0].create_sql
    assert "`id` INT NOT NULL" in create


def test_create_sql_quotes_table_with_schema():
    s = _scenario(tables=[{
        "name": "ods.orders", "role": "source", "rows": 0,
        "columns": [{"name": "id", "type": "INT", "gen": "sequence"}],
    }])
    create = build_materialize_plan(s, {}).tables[0].create_sql
    assert create.startswith("CREATE TABLE `ods`.`orders` (")


def test_create_sql_composite_primary_key():
    s = _scenario(tables=[{
        "name": "t", "role": "source", "rows": 0,
        "columns": [
            {"name": "a", "type": "INT", "pk": True, "gen": "sequence"},
            {"name": "b", "type": "INT", "pk": True, "gen": "sequence"},
            {"name": "c", "type": "INT", "gen": "random_int"},
        ],
    }])
    create = build_materialize_plan(s, {}).tables[0].create_sql
    assert "PRIMARY KEY (`a`, `b`)" in create


def test_identifier_with_backtick_is_escaped():
    s = _scenario(tables=[{
        "name": "weird`name", "role": "source", "rows": 0,
        "columns": [{"name": "co`l", "type": "INT", "gen": "sequence"}],
    }])
    plan = build_materialize_plan(s, {})
    assert plan.tables[0].quoted_full == "`weird``name`"
    assert "`co``l`" in plan.tables[0].create_sql


# ─── indexes ────────────────────────────────────────────────────────────────


def test_index_unique_emits_unique_keyword():
    s = _scenario(tables=[{
        "name": "t", "role": "source", "rows": 0,
        "columns": [{"name": "id", "type": "INT", "pk": True, "gen": "sequence"}],
        "indexes": [{"columns": ["id"], "unique": True}],
    }])
    idx_sqls = build_materialize_plan(s, {}).tables[0].index_sqls
    assert len(idx_sqls) == 1
    assert idx_sqls[0] == "CREATE UNIQUE INDEX `idx_t_0` ON `t` (`id`)"


def test_index_non_unique_no_unique_keyword():
    s = _scenario(tables=[{
        "name": "t", "role": "source", "rows": 0,
        "columns": [{"name": "id", "type": "INT", "gen": "sequence"}],
        "indexes": [{"columns": ["id"]}],
    }])
    idx_sqls = build_materialize_plan(s, {}).tables[0].index_sqls
    assert idx_sqls[0] == "CREATE INDEX `idx_t_0` ON `t` (`id`)"


def test_index_skip_excluded():
    s = _scenario(tables=[{
        "name": "t", "role": "source", "rows": 0,
        "columns": [{"name": "id", "type": "INT", "gen": "sequence"}],
        "indexes": [
            {"columns": ["id"]},
            {"columns": ["id"], "skip": True, "reason": "demo slow query"},
        ],
    }])
    idx_sqls = build_materialize_plan(s, {}).tables[0].index_sqls
    assert len(idx_sqls) == 1
    assert "skip" not in idx_sqls[0].lower()


def test_index_multi_column():
    s = _scenario(tables=[{
        "name": "t", "role": "source", "rows": 0,
        "columns": [
            {"name": "a", "type": "INT", "gen": "sequence"},
            {"name": "b", "type": "INT", "gen": "sequence"},
        ],
        "indexes": [{"columns": ["a", "b"]}],
    }])
    idx_sqls = build_materialize_plan(s, {}).tables[0].index_sqls
    assert idx_sqls[0].endswith("(`a`, `b`)")


# ─── INSERT SQL + param rows ────────────────────────────────────────────────


def test_insert_sql_placeholders_match_column_count():
    s = _scenario(tables=[{
        "name": "t", "role": "source", "rows": 3,
        "columns": [
            {"name": "id", "type": "INT", "pk": True, "gen": "sequence"},
            {"name": "x", "type": "INT", "gen": "constant", "values": [7]},
        ],
    }])
    data = generate_scenario(s)
    plan = build_materialize_plan(s, data)
    assert plan.tables[0].insert_sql == "INSERT INTO `t` (`id`, `x`) VALUES (%s, %s)"


def test_insert_param_rows_align_with_column_order():
    s = _scenario(tables=[{
        "name": "t", "role": "source", "rows": 3,
        "columns": [
            {"name": "id", "type": "INT", "pk": True, "gen": "sequence"},
            {"name": "x", "type": "INT", "gen": "constant", "values": [7]},
        ],
    }])
    plan = build_materialize_plan(s, generate_scenario(s))
    # 行参数应该是 tuple，长度=列数，按列顺序
    for row in plan.tables[0].rows:
        assert isinstance(row, tuple)
        assert len(row) == 2
    # 具体值
    ids = [r[0] for r in plan.tables[0].rows]
    xs = [r[1] for r in plan.tables[0].rows]
    assert ids == [1, 2, 3]
    assert xs == [7, 7, 7]


def test_insert_missing_field_becomes_none():
    """row dict 缺某列 → param 用 None 占位（不抛）。"""
    s = _scenario(tables=[{
        "name": "t", "role": "source", "rows": 0,
        "columns": [
            {"name": "a", "type": "INT", "gen": "constant", "values": [1]},
            {"name": "b", "type": "INT", "gen": "constant", "values": [2]},
        ],
    }])
    plan = build_materialize_plan(s, {"t": [{"a": 1}]})  # 缺 b
    assert plan.tables[0].rows == [(1, None)]


# ─── derives_from in plan ───────────────────────────────────────────────────


def test_derives_from_columns_inherited_in_ddl():
    s = _scenario(tables=[
        {
            "name": "src", "role": "source", "rows": 0,
            "columns": [
                {"name": "id", "type": "INT", "pk": True, "gen": "sequence"},
                {"name": "amt", "type": "DECIMAL(10,2)", "gen": "realistic"},
            ],
        },
        {"name": "tgt", "role": "target", "rows": 0, "derives_from": "src"},
    ])
    plans_by_name = {t.full_name: t for t in build_materialize_plan(s, {}).tables}
    assert "`id` INT" in plans_by_name["tgt"].create_sql
    assert "`amt` DECIMAL(10,2)" in plans_by_name["tgt"].create_sql
    assert "PRIMARY KEY (`id`)" in plans_by_name["tgt"].create_sql


def test_derives_from_column_rename_applied_in_ddl():
    s = _scenario(tables=[
        {
            "name": "src", "role": "source", "rows": 0,
            "columns": [
                {"name": "id", "type": "INT", "pk": True, "gen": "sequence"},
                {"name": "ts", "type": "DATETIME", "gen": "timestamp"},
            ],
        },
        {
            "name": "tgt", "role": "target", "rows": 0,
            "derives_from": "src",
            "column_overrides": [{"from": "ts", "rename": "d", "transform": "DATE($)"}],
        },
    ])
    tgt_create = next(
        t.create_sql for t in build_materialize_plan(s, {}).tables if t.full_name == "tgt"
    )
    assert "`d` DATETIME" in tgt_create  # type 不变（transform 只改值）
    assert "`ts`" not in tgt_create  # 原名不再出现


# ─── dialect guard ──────────────────────────────────────────────────────────


def test_oracle_dialect_now_supported():
    """切片 13：Oracle / DM dialect 支持 —— 不再抛 NotImplementedError。"""
    s = _scenario(dialect="oracle", tables=[{
        "name": "t", "role": "source", "rows": 0,
        "columns": [{"name": "id", "type": "INT", "gen": "sequence"}],
    }])
    plan = build_materialize_plan(s, {})
    assert plan.dialect == "oracle"


def test_get_dialect_rejects_unknown_name():
    """get_dialect 直接调用、传未注册方言名应抛 NotImplementedError。"""
    from app.scenarios.dialects import get_dialect
    with pytest.raises(NotImplementedError, match="postgres"):
        get_dialect("postgres")


# ─── apply_plan execution order + batching ─────────────────────────────────


def test_apply_plan_executes_in_order():
    s = _scenario(tables=[{
        "name": "ods.t", "role": "source", "rows": 2,
        "columns": [
            {"name": "id", "type": "INT", "pk": True, "gen": "sequence"},
        ],
    }])
    plan = build_materialize_plan(s, generate_scenario(s))
    ex = FakeExecutor()
    summary = apply_plan(plan, ex)
    ops = [c[0] for c in ex.calls]
    sqls = [c[1] for c in ex.calls]
    # 1) CREATE DATABASE 2) DROP 3) CREATE TABLE 4) INSERT
    assert ops == ["execute", "execute", "execute", "executemany"]
    assert sqls[0].startswith("CREATE DATABASE")
    assert sqls[1].startswith("DROP TABLE")
    assert sqls[2].startswith("CREATE TABLE")
    assert sqls[3].startswith("INSERT INTO")
    assert summary["tables"][0]["rows_inserted"] == 2


def test_apply_plan_batches_inserts():
    s = _scenario(tables=[{
        "name": "t", "role": "source", "rows": 1200,
        "columns": [{"name": "id", "type": "INT", "pk": True, "gen": "sequence"}],
    }])
    plan = build_materialize_plan(s, generate_scenario(s))
    ex = FakeExecutor()
    summary = apply_plan(plan, ex, batch_size=500)
    insert_calls = [c for c in ex.calls if c[0] == "executemany"]
    assert len(insert_calls) == 3  # 500 + 500 + 200
    assert [len(c[2]) for c in insert_calls] == [500, 500, 200]
    assert summary["tables"][0]["rows_inserted"] == 1200


def test_apply_plan_empty_rows_no_executemany():
    s = _scenario(tables=[{
        "name": "t", "role": "source", "rows": 0,
        "columns": [{"name": "id", "type": "INT", "pk": True, "gen": "sequence"}],
    }])
    plan = build_materialize_plan(s, {})
    ex = FakeExecutor()
    summary = apply_plan(plan, ex)
    assert not any(c[0] == "executemany" for c in ex.calls)
    assert summary["tables"][0]["rows_inserted"] == 0


def test_apply_plan_runs_index_after_create():
    s = _scenario(tables=[{
        "name": "t", "role": "source", "rows": 0,
        "columns": [{"name": "id", "type": "INT", "pk": True, "gen": "sequence"}],
        "indexes": [{"columns": ["id"], "unique": True}],
    }])
    plan = build_materialize_plan(s, {})
    ex = FakeExecutor()
    summary = apply_plan(plan, ex)
    sqls = [c[1] for c in ex.calls]
    # DROP → CREATE TABLE → CREATE INDEX（无 CREATE DATABASE 因无 schema）
    assert sqls[0].startswith("DROP TABLE")
    assert sqls[1].startswith("CREATE TABLE")
    assert sqls[2].startswith("CREATE UNIQUE INDEX")
    assert summary["tables"][0]["indexes_created"] == 1


# ─── CursorExecutor (thin shim) ─────────────────────────────────────────────


class _SpyCursor:
    def __init__(self):
        self.execs: list[tuple[str, Any]] = []
        self.many: list[tuple[str, list]] = []

    def execute(self, sql, params=None):
        self.execs.append((sql, params))

    def executemany(self, sql, params_list):
        self.many.append((sql, list(params_list)))


def test_cursor_executor_passthrough():
    cur = _SpyCursor()
    ex = CursorExecutor(cur)
    ex.execute("CREATE TABLE x (id INT)")
    ex.execute("INSERT INTO x VALUES (%s)", (1,))
    ex.executemany("INSERT INTO x VALUES (%s)", [(2,), (3,)])
    assert cur.execs == [("CREATE TABLE x (id INT)", None), ("INSERT INTO x VALUES (%s)", (1,))]
    # 注意 execute("...", None) 走 if-分支 cur.execute(sql) 无 params；上面已经验
    assert cur.many == [("INSERT INTO x VALUES (%s)", [(2,), (3,)])]


# ─── end-to-end with bundled example.yml ───────────────────────────────────


def test_example_yml_full_plan():
    scenario = load_scenario(EXAMPLE_PATH)
    data = generate_scenario(scenario)
    plan = build_materialize_plan(scenario, data)

    # 两个 schema：ods 和 dwd
    assert plan.schemas == [
        "CREATE DATABASE IF NOT EXISTS `ods`",
        "CREATE DATABASE IF NOT EXISTS `dwd`",
    ]
    # 两张表
    by_name = {t.full_name: t for t in plan.tables}
    assert set(by_name) == {"ods.orders", "dwd.orders_clean"}

    # ods.orders DDL 含 PK + 5 列
    ods = by_name["ods.orders"]
    assert "PRIMARY KEY (`order_id`)" in ods.create_sql
    for col in ("order_id", "user_id", "amount", "created_at", "status"):
        assert _col_in_ddl(col, ods.create_sql)

    # dwd.orders_clean：rename 后字段是 order_date，没有 created_at
    dwd = by_name["dwd.orders_clean"]
    assert "`order_date`" in dwd.create_sql
    assert "`created_at`" not in dwd.create_sql

    # 索引：ods 1 个 unique；dwd 1 个 unique（skip 的那个不算）
    assert len(ods.index_sqls) == 1
    assert "UNIQUE" in ods.index_sqls[0]
    assert len(dwd.index_sqls) == 1

    # rows 长度对得上 generator 输出
    assert len(ods.rows) == 1000
    assert len(dwd.rows) == 985  # missing 20 + extras 5


def _col_in_ddl(col: str, ddl: str) -> bool:
    return f"`{col}`" in ddl


def test_example_yml_apply_through_fake():
    scenario = load_scenario(EXAMPLE_PATH)
    plan = build_materialize_plan(scenario, generate_scenario(scenario))
    ex = FakeExecutor()
    summary = apply_plan(plan, ex, batch_size=500)
    # 总 INSERT 行数
    total = sum(t["rows_inserted"] for t in summary["tables"])
    assert total == 1000 + 985
    # CREATE DATABASE 2 次
    create_db = [c for c in ex.calls if c[1].startswith("CREATE DATABASE")]
    assert len(create_db) == 2
