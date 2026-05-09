"""Phase 11 spike：app.dbclients.dialects registry contract。

只覆盖 registry + Dialect 抽象层。SQL 文本细节由
tests/test_datasource_introspect.py::test_columns_sql_* 已经覆盖，
不重复 assert。
"""
from __future__ import annotations

import pytest

from app.dbclients.dialects import get_dialect
from app.dbclients.dialects.oracle import OracleDialect
from app.models import DatabaseType


def test_get_dialect_returns_singleton_per_db_type():
    a = get_dialect(DatabaseType.MYSQL)
    b = get_dialect(DatabaseType.MYSQL)
    assert a is b  # registry 缓存同一实例


def test_dm_shares_oracle_dialect_instance():
    """DM 的字典视图跟 Oracle 一致 → 直接复用 OracleDialect 实例避免空壳子继承。"""
    assert isinstance(get_dialect(DatabaseType.DM), OracleDialect)
    assert isinstance(get_dialect(DatabaseType.ORACLE), OracleDialect)


def test_all_four_db_types_have_dialect():
    """缺任一方言注册都意味着 introspect 会 ValueError。"""
    for db_type in (DatabaseType.MYSQL, DatabaseType.ORACLE, DatabaseType.DM, DatabaseType.DB2):
        d = get_dialect(db_type)
        # 抽象方法必须有具体实现
        sql = d.introspect_columns_sql("ods", "t1")
        assert isinstance(sql, str) and sql.strip()


def test_introspect_sql_has_required_output_columns():
    """name / data_type / nullable / comment / ordinal 是 introspect_columns 归一化的契约。"""
    for db_type in (DatabaseType.MYSQL, DatabaseType.ORACLE, DatabaseType.DM, DatabaseType.DB2):
        sql = get_dialect(db_type).introspect_columns_sql("ods", "t1")
        for alias in ("name", "data_type", "nullable", "comment", "ordinal"):
            assert f"AS {alias}" in sql, f"{db_type.value} 缺别名 {alias}：{sql}"


def test_introspect_sql_handles_no_schema():
    """schema='' 跨 schema 拉同名表，不应抛错。"""
    for db_type in (DatabaseType.MYSQL, DatabaseType.ORACLE, DatabaseType.DB2):
        sql = get_dialect(db_type).introspect_columns_sql("", "t1")
        assert isinstance(sql, str) and sql.strip()


# ─── connection_test_sql ─────────────────────────────────────────────────────


def test_mysql_connection_test_has_no_from():
    """MySQL 没 dual 表，select 1 直出，不能带 FROM。"""
    sql = get_dialect(DatabaseType.MYSQL).connection_test_sql()
    assert "select 1" in sql.lower()
    assert "from" not in sql.lower()


def test_oracle_and_dm_connection_test_use_dual():
    """Oracle / DM 要求 select 必有 FROM；用 dual 伪表。"""
    for db_type in (DatabaseType.ORACLE, DatabaseType.DM):
        sql = get_dialect(db_type).connection_test_sql()
        assert "from dual" in sql.lower()


def test_db2_connection_test_uses_sysdummy1():
    """DB2 用 sysibm.sysdummy1 当 dual 等价。"""
    sql = get_dialect(DatabaseType.DB2).connection_test_sql()
    assert "sysibm.sysdummy1" in sql.lower()


def test_connection_test_sql_aliases_ok_column():
    """所有方言都把列起名 ok，方便 caller 读 sample。"""
    for db_type in (DatabaseType.MYSQL, DatabaseType.ORACLE, DatabaseType.DM, DatabaseType.DB2):
        sql = get_dialect(db_type).connection_test_sql()
        assert "as ok" in sql.lower()
