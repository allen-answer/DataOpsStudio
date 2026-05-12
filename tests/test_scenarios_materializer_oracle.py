"""Oracle / DM materialize dialect tests（Phase 12 切片 13）。

scope:
- Oracle dialect 独立单测（quote / placeholder / DROP / schema_create）
- DM = Oracle 实例（registry 共享）
- build_materialize_plan with dialect=oracle / dm：plan 形态验证
- 跟现有 mysql plan 形态对比，确保两套独立、无串台
"""
from __future__ import annotations

from typing import Any

import pytest

from app.scenarios.dialects import get_dialect
from app.scenarios.dialects.oracle import OracleMaterializeDialect
from app.scenarios.materializer import build_materialize_plan
from app.scenarios.models import Scenario


def _scenario(**kwargs: Any) -> Scenario:
    payload = {"id": "ora-test", "name": "T", "seed": 42}
    payload.update(kwargs)
    return Scenario.model_validate(payload)


# ─── Oracle dialect 单元方法 ────────────────────────────────────────────────


def test_oracle_quote_identifier_uses_double_quotes():
    d = OracleMaterializeDialect()
    assert d.quote_identifier("orders") == '"orders"'


def test_oracle_quote_escapes_embedded_double_quote():
    d = OracleMaterializeDialect()
    assert d.quote_identifier('we"ird') == '"we""ird"'


def test_oracle_quote_qualified_schema_table():
    d = OracleMaterializeDialect()
    assert d.quote_qualified("ods.orders") == '"ods"."orders"'


def test_oracle_quote_qualified_unqualified():
    d = OracleMaterializeDialect()
    assert d.quote_qualified("orders") == '"orders"'


def test_oracle_no_schema_create_sql():
    """Oracle schema=user，不能擅自 CREATE → 返回 None。"""
    assert OracleMaterializeDialect().schema_create_sql("ods") is None


def test_oracle_drop_table_wraps_pl_sql_block():
    d = OracleMaterializeDialect()
    drop = d.drop_table_sql('"ods"."orders"')
    assert drop.startswith("BEGIN")
    assert "EXECUTE IMMEDIATE 'DROP TABLE \"ods\".\"orders\"'" in drop
    assert "SQLCODE != -942" in drop  # 吞 ORA-00942 (table not found)
    assert drop.rstrip().endswith("END;")


def test_oracle_placeholder_uses_numbered_binds():
    d = OracleMaterializeDialect()
    assert d.placeholder(0) == ":1"
    assert d.placeholder(3) == ":4"


def test_oracle_insert_sql_uses_numbered_placeholders():
    d = OracleMaterializeDialect()
    sql = d.insert_sql('"t"', ["a", "b", "c"])
    assert sql == 'INSERT INTO "t" ("a", "b", "c") VALUES (:1, :2, :3)'


def test_oracle_create_index_sql_uses_double_quotes():
    d = OracleMaterializeDialect()
    sql = d.create_index_sql('"idx_t_0"', '"ods"."orders"', ["id", "name"], unique=True)
    assert sql == 'CREATE UNIQUE INDEX "idx_t_0" ON "ods"."orders" ("id", "name")'


# ─── Registry：DM 复用 Oracle ───────────────────────────────────────────────


def test_dm_dialect_resolves_to_oracle_instance():
    assert isinstance(get_dialect("dm"), OracleMaterializeDialect)


def test_mysql_and_oracle_are_distinct_instances():
    """同 registry 但不同方言要给不同实例。"""
    from app.scenarios.dialects.mysql import MysqlMaterializeDialect
    assert isinstance(get_dialect("mysql"), MysqlMaterializeDialect)
    assert not isinstance(get_dialect("mysql"), OracleMaterializeDialect)


def test_get_dialect_case_insensitive():
    assert get_dialect("MySQL").name == "mysql"
    assert get_dialect("ORACLE").name == "oracle"


# ─── build_materialize_plan with dialect=oracle ─────────────────────────────


def test_oracle_plan_no_create_database():
    """Oracle schema_create_sql 返回 None → plan.schemas 为空。"""
    s = _scenario(dialect="oracle", tables=[{
        "name": "ods.orders", "role": "source", "rows": 0,
        "columns": [{"name": "id", "type": "INT", "pk": True, "gen": "sequence"}],
    }])
    plan = build_materialize_plan(s, {})
    assert plan.dialect == "oracle"
    assert plan.schemas == []  # 没 CREATE DATABASE


def test_oracle_plan_drop_table_is_plsql_block():
    s = _scenario(dialect="oracle", tables=[{
        "name": "t", "role": "source", "rows": 0,
        "columns": [{"name": "id", "type": "INT", "pk": True, "gen": "sequence"}],
    }])
    plan = build_materialize_plan(s, {}, drop_first=True)
    drop_sql = plan.tables[0].drop_sql
    assert drop_sql is not None
    assert drop_sql.startswith("BEGIN")
    assert "EXECUTE IMMEDIATE 'DROP TABLE \"t\"'" in drop_sql


def test_oracle_plan_create_table_uses_double_quotes():
    s = _scenario(dialect="oracle", tables=[{
        "name": "ods.orders", "role": "source", "rows": 0,
        "columns": [
            {"name": "id", "type": "NUMBER", "pk": True, "gen": "sequence"},
            {"name": "amt", "type": "NUMBER(10,2)", "gen": "realistic"},
        ],
    }])
    create = build_materialize_plan(s, {}).tables[0].create_sql
    assert create.startswith('CREATE TABLE "ods"."orders" (')
    assert '"id" NUMBER' in create
    assert '"amt" NUMBER(10,2)' in create
    assert 'PRIMARY KEY ("id")' in create


def test_oracle_plan_insert_uses_numbered_placeholders():
    s = _scenario(dialect="oracle", tables=[{
        "name": "ods.orders", "role": "source", "rows": 0,
        "columns": [
            {"name": "id", "type": "NUMBER", "pk": True, "gen": "sequence"},
            {"name": "amt", "type": "NUMBER(10,2)", "gen": "realistic"},
        ],
    }])
    insert = build_materialize_plan(s, {}).tables[0].insert_sql
    assert insert == 'INSERT INTO "ods"."orders" ("id", "amt") VALUES (:1, :2)'


def test_oracle_plan_indexes_use_double_quotes():
    s = _scenario(dialect="oracle", tables=[{
        "name": "ods.orders", "role": "source", "rows": 0,
        "columns": [{"name": "id", "type": "NUMBER", "pk": True, "gen": "sequence"}],
        "indexes": [{"columns": ["id"], "unique": True}],
    }])
    idx = build_materialize_plan(s, {}).tables[0].index_sqls
    assert len(idx) == 1
    assert idx[0] == 'CREATE UNIQUE INDEX "idx_orders_0" ON "ods"."orders" ("id")'


def test_dm_plan_identical_to_oracle():
    """DM 和 Oracle 走同实例，plan 应字节一致。"""
    payload = {
        "id": "x", "name": "T", "seed": 1,
        "tables": [{
            "name": "t", "role": "source", "rows": 0,
            "columns": [{"name": "id", "type": "INT", "pk": True, "gen": "sequence"}],
        }],
    }
    s_oracle = Scenario.model_validate({**payload, "dialect": "oracle"})
    s_dm = Scenario.model_validate({**payload, "dialect": "dm"})
    plan_oracle = build_materialize_plan(s_oracle, {})
    plan_dm = build_materialize_plan(s_dm, {})
    # dialect 名字会不同（oracle vs oracle —— DM 实例 .name 仍是 "oracle"），
    # 但 SQL 形态应完全一致
    assert plan_oracle.tables[0].create_sql == plan_dm.tables[0].create_sql
    assert plan_oracle.tables[0].drop_sql == plan_dm.tables[0].drop_sql
    assert plan_oracle.tables[0].insert_sql == plan_dm.tables[0].insert_sql


# ─── recorder 用 Oracle dialect 时 SELECT 用双引号 ──────────────────────────


def test_recorder_oracle_select_uses_double_quotes(isolated_storage):
    from app.scenarios.recorder import build_compare_tasks
    s = _scenario(
        dialect="oracle",
        tables=[
            {"name": "ods.orders", "role": "source", "rows": 0, "columns": [
                {"name": "order_id", "type": "VARCHAR2(32)", "pk": True, "gen": "uuid_short"},
                {"name": "amt", "type": "NUMBER(10,2)", "gen": "realistic"},
            ]},
            {"name": "dwd.orders", "role": "target", "rows": 0, "derives_from": "ods.orders"},
        ],
        workloads=[{
            "kind": "compare_task", "name": "recon",
            "source": "ods.orders", "target": "dwd.orders", "keys": ["order_id"],
        }],
    )
    plan = build_compare_tasks(s, datasource_id="ds-oracle")
    p = plan.payloads[0]
    # 列名 + 表名都用 Oracle "" 引用
    assert p.source_sql == (
        'SELECT "order_id", "amt" FROM "ods"."orders" ORDER BY "order_id"'
    )
    assert p.target_sql == (
        'SELECT "order_id", "amt" FROM "dwd"."orders" ORDER BY "order_id"'
    )
