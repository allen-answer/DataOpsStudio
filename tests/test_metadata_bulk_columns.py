"""bulk columns SQL + list_columns_bulk grouping 单测。

只测 SQL 生成 + grouping 逻辑,不连真库(那是集成测,docker compose --profile demo-db)。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.dbclients.dialects.db2 import Db2Dialect
from app.dbclients.dialects.mysql import MysqlDialect
from app.dbclients.dialects.oracle import OracleDialect
from app.dbclients.dialects.dm import DmDialect


# ─── bulk_columns_sql per dialect ────────────────────────────────────────────

def test_mysql_bulk_sql():
    sql = MysqlDialect().bulk_columns_sql("ods")
    assert sql is not None
    assert "information_schema.COLUMNS" in sql
    assert "TABLE_SCHEMA = 'ods'" in sql
    assert "TABLE_NAME AS table_name" in sql
    assert "ORDER BY TABLE_NAME, ORDINAL_POSITION" in sql


def test_mysql_bulk_sql_empty_schema_returns_none():
    """MySQL 没默认 schema,空 schema 应返 None。"""
    assert MysqlDialect().bulk_columns_sql("") is None


def test_oracle_bulk_sql():
    sql = OracleDialect().bulk_columns_sql("hr")
    assert sql is not None
    assert "all_tab_columns" in sql
    assert "UPPER('hr')" in sql
    assert "table_name" in sql.lower()


def test_dm_shares_oracle_bulk_sql():
    """DM 继承 OracleDialect,bulk_columns_sql 应该和 Oracle 一致(数据字典视图同名)。"""
    dm_sql = DmDialect().bulk_columns_sql("ods")
    oracle_sql = OracleDialect().bulk_columns_sql("ods")
    assert dm_sql == oracle_sql


def test_db2_bulk_sql():
    sql = Db2Dialect().bulk_columns_sql("admin")
    assert sql is not None
    assert "SYSIBM.SYSCOLUMNS" in sql
    assert "UPPER('admin')" in sql
    assert "TBNAME AS table_name" in sql


def test_db2_bulk_sql_empty_schema_returns_none():
    assert Db2Dialect().bulk_columns_sql("") is None


# ─── list_columns_bulk grouping ──────────────────────────────────────────────

def test_list_columns_bulk_groups_by_table_name():
    """list_columns_bulk 应该把扁平行 grouped by table_name。"""
    from app.sqlide import metadata

    fake_rows = [
        {"table_name": "users", "name": "id", "data_type": "INT", "nullable": "NO", "comment": "", "ordinal": 1},
        {"table_name": "users", "name": "name", "data_type": "VARCHAR", "nullable": "YES", "comment": "", "ordinal": 2},
        {"table_name": "orders", "name": "id", "data_type": "INT", "nullable": "NO", "comment": "", "ordinal": 1},
        {"table_name": "orders", "name": "user_id", "data_type": "INT", "nullable": "YES", "comment": "", "ordinal": 2},
    ]

    fake_source = MagicMock()
    fake_source.id = "ds-test"
    fake_source.db_type = MagicMock()

    with patch("app.sqlide.metadata.dbclients_factory.fetch_rows", return_value=fake_rows), \
         patch("app.sqlide.metadata.get_dialect") as fake_get_dialect:
        fake_get_dialect.return_value.bulk_columns_sql.return_value = "SELECT ..."
        result = metadata.list_columns_bulk(fake_source, schema="ods")

    assert set(result.keys()) == {"users", "orders"}
    assert len(result["users"]) == 2
    assert result["users"][0]["name"] == "id"
    assert result["users"][1]["name"] == "name"
    assert len(result["orders"]) == 2


def test_list_columns_bulk_empty_schema_returns_empty():
    from app.sqlide import metadata
    fake_source = MagicMock()
    result = metadata.list_columns_bulk(fake_source, schema="")
    assert result == {}


def test_list_columns_bulk_unsupported_dialect_returns_empty():
    """方言不支持 bulk(bulk_columns_sql 返 None)时返 {}。"""
    from app.sqlide import metadata

    fake_source = MagicMock()
    fake_source.id = "ds-test"

    with patch("app.sqlide.metadata.get_dialect") as fake_get_dialect:
        fake_get_dialect.return_value.bulk_columns_sql.return_value = None
        result = metadata.list_columns_bulk(fake_source, schema="ods")

    assert result == {}


def test_list_columns_bulk_skips_rows_without_table_name():
    """坏数据行(table_name 缺失)跳过,不抛。"""
    from app.sqlide import metadata

    fake_rows = [
        {"table_name": "users", "name": "id", "data_type": "INT", "nullable": "NO", "ordinal": 1},
        {"table_name": None, "name": "orphan", "data_type": "?", "nullable": "?", "ordinal": 1},
        {"name": "no_table_field", "data_type": "?", "nullable": "?", "ordinal": 1},
    ]
    fake_source = MagicMock()
    fake_source.id = "ds-test"

    with patch("app.sqlide.metadata.dbclients_factory.fetch_rows", return_value=fake_rows), \
         patch("app.sqlide.metadata.get_dialect") as fake_get_dialect:
        fake_get_dialect.return_value.bulk_columns_sql.return_value = "SELECT ..."
        result = metadata.list_columns_bulk(fake_source, schema="ods")

    assert set(result.keys()) == {"users"}
    assert len(result["users"]) == 1


def test_list_columns_bulk_accepts_uppercase_columns():
    """Oracle / DM 驱动有时返大写列名,grouping 应兼容。"""
    from app.sqlide import metadata

    fake_rows = [
        {"TABLE_NAME": "USERS", "NAME": "ID", "DATA_TYPE": "NUMBER", "NULLABLE": "N", "COMMENT": "", "ORDINAL": 1},
    ]
    fake_source = MagicMock()
    fake_source.id = "ds-test"

    with patch("app.sqlide.metadata.dbclients_factory.fetch_rows", return_value=fake_rows), \
         patch("app.sqlide.metadata.get_dialect") as fake_get_dialect:
        fake_get_dialect.return_value.bulk_columns_sql.return_value = "SELECT ..."
        result = metadata.list_columns_bulk(fake_source, schema="hr")

    assert "USERS" in result
    assert result["USERS"][0]["name"] == "ID"
    assert result["USERS"][0]["data_type"] == "NUMBER"
