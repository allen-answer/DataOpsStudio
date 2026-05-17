"""S1.C：datasource introspection 测试。

不依赖真实 DB —— 用 monkeypatch 替换 dbclients_factory.fetch_rows 返回固定 rows。
真实端到端用 docker compose --profile demo-db 起 MySQL 验。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.models import DataSourceCreate
from app.services import datasource_introspect as di
from app.services.repositories import datasource_store


# ─── unit 工具 ─────────────────────────────────────────────────────────────


def test_split_schema_table_dot_form():
    assert di._split_schema_table("ods.t_users") == ("ods", "t_users")


def test_split_schema_table_no_schema():
    assert di._split_schema_table("t_users") == ("", "t_users")


def test_split_schema_table_strips_dblink():
    assert di._split_schema_table("dim.cust@remote_db") == ("dim", "cust")


def test_validate_identifier_accepts_alnum_underscore_dollar():
    di._validate_identifier("t_users")
    di._validate_identifier("ODS_DATA")
    di._validate_identifier("table$1")
    di._validate_identifier("schema.table")


def test_validate_identifier_rejects_sql_injection():
    """防注入：`'`、`;`、空格、引号都拒绝。"""
    for bad in ["t'users", "t;DROP", "t users", 'tab"name', "t/*x*/"]:
        with pytest.raises(ValueError):
            di._validate_identifier(bad)


def test_normalize_nullable_handles_yes_no():
    assert di._normalize_nullable("YES") is True
    assert di._normalize_nullable("Y") is True
    assert di._normalize_nullable("NO") is False
    assert di._normalize_nullable("N") is False
    assert di._normalize_nullable(True) is True
    assert di._normalize_nullable("") is False


# ─── _columns_sql per dialect ──────────────────────────────────────────────


def test_columns_sql_mysql_with_schema():
    from app.models import DatabaseType
    sql, _ = di._columns_sql(DatabaseType.MYSQL, "ods", "t_users")
    assert "information_schema.COLUMNS" in sql
    assert "TABLE_SCHEMA = 'ods'" in sql
    assert "TABLE_NAME = 't_users'" in sql
    assert "ORDER BY ORDINAL_POSITION" in sql


def test_columns_sql_oracle_uppercases():
    """Oracle 标识符默认大写，SQL 用 UPPER() 包 schema/table。"""
    from app.models import DatabaseType
    sql, _ = di._columns_sql(DatabaseType.ORACLE, "ods", "t_users")
    assert "all_tab_columns" in sql
    assert "UPPER('ods')" in sql
    assert "UPPER('t_users')" in sql


def test_columns_sql_dm_uses_oracle_form():
    """DM 跟 Oracle 共享语法。"""
    from app.models import DatabaseType
    sql, _ = di._columns_sql(DatabaseType.DM, "x", "y")
    assert "all_tab_columns" in sql


def test_columns_sql_db2_uses_sysibm():
    from app.models import DatabaseType
    sql, _ = di._columns_sql(DatabaseType.DB2, "DBA", "T")
    assert "SYSIBM.SYSCOLUMNS" in sql


def test_columns_sql_rejects_injection_in_table_name():
    from app.models import DatabaseType
    with pytest.raises(ValueError):
        di._columns_sql(DatabaseType.MYSQL, "ods", "t' OR 1=1 --")


# ─── introspect_columns（monkeypatch fetch_rows）─────────────────────────────


@pytest.fixture
def fake_mysql_ds(isolated_storage):
    """建一个 MySQL datasource，返回 id。"""
    ds = datasource_store.create(DataSourceCreate(
        name="prod-mysql", db_type="MySQL",
        host="db", port=3306, database="orders", username="u", password="p",
    ))
    return ds.id


def test_introspect_columns_normalizes_rows(fake_mysql_ds, monkeypatch):
    """fetch_rows 返回 raw rows → introspect_columns 归一化字段名 / 类型 / nullable。"""
    fake_rows = [
        {"name": "id", "data_type": "BIGINT", "nullable": "NO",
         "comment": "主键", "ordinal": 1},
        {"name": "name", "data_type": "VARCHAR", "nullable": "YES",
         "comment": "", "ordinal": 2},
    ]
    monkeypatch.setattr(
        di.dbclients_factory, "fetch_rows",
        lambda source, sql, max_rows=None: fake_rows,
    )
    di.invalidate_cache()  # 防上一个测试残留
    cols = di.introspect_columns(fake_mysql_ds, "ods.t_users", use_cache=False)
    assert len(cols) == 2
    assert cols[0]["name"] == "id"
    assert cols[0]["nullable"] is False
    assert cols[1]["nullable"] is True


def test_introspect_columns_cache_hit_avoids_db_call(fake_mysql_ds, monkeypatch):
    """同 (datasource, table) 第二次拉走缓存。"""
    call_count = {"n": 0}
    def counting_fetch(source, sql, max_rows=None):
        call_count["n"] += 1
        return [{"name": "id", "data_type": "INT", "nullable": "NO", "comment": "", "ordinal": 1}]
    monkeypatch.setattr(di.dbclients_factory, "fetch_rows", counting_fetch)
    di.invalidate_cache()
    di.introspect_columns(fake_mysql_ds, "t1")
    di.introspect_columns(fake_mysql_ds, "t1")
    di.introspect_columns(fake_mysql_ds, "t1")
    assert call_count["n"] == 1  # 只第一次打 DB


def test_introspect_columns_invalidate_cache(fake_mysql_ds, monkeypatch):
    call_count = {"n": 0}
    def counting_fetch(source, sql, max_rows=None):
        call_count["n"] += 1
        return []
    monkeypatch.setattr(di.dbclients_factory, "fetch_rows", counting_fetch)
    di.invalidate_cache()
    di.introspect_columns(fake_mysql_ds, "t1")
    di.invalidate_cache(datasource_id=fake_mysql_ds, table_name="t1")
    di.introspect_columns(fake_mysql_ds, "t1")
    assert call_count["n"] == 2


def test_introspect_columns_unknown_datasource(isolated_storage):
    di.invalidate_cache()
    with pytest.raises(ValueError, match="not found"):
        di.introspect_columns("ghost-id", "t1")


def test_introspect_columns_empty_inputs_raise(isolated_storage):
    with pytest.raises(ValueError):
        di.introspect_columns("", "t")
    with pytest.raises(ValueError):
        di.introspect_columns("any", "")


# ─── HTTP endpoint ──────────────────────────────────────────────────────────


# `client` fixture 来自 conftest.py（admin-authed）。


def test_introspect_endpoint_requires_datasource_id(client, fake_mysql_ds):
    r = client.get(f"/api/assets/introspect/some.table")
    assert r.status_code == 422  # missing required Query


def test_introspect_endpoint_404_on_unknown_datasource(client):
    r = client.get("/api/assets/introspect/some.table?datasource_id=ghost")
    assert r.status_code == 404


def test_introspect_endpoint_returns_columns_with_meta(client, fake_mysql_ds, monkeypatch):
    monkeypatch.setattr(
        di.dbclients_factory, "fetch_rows",
        lambda source, sql, max_rows=None: [
            {"name": "id", "data_type": "INT", "nullable": "NO", "comment": "", "ordinal": 1},
        ],
    )
    di.invalidate_cache()
    r = client.get(f"/api/assets/introspect/ods.t1?datasource_id={fake_mysql_ds}")
    assert r.status_code == 200
    body = r.json()
    assert body["datasource_name"] == "prod-mysql"
    assert body["db_type"] == "MySQL"
    assert body["column_count"] == 1
    assert body["columns"][0]["name"] == "id"


def test_introspect_endpoint_502_on_db_error(client, fake_mysql_ds, monkeypatch):
    """连接失败 → 502（区别于 4xx 用户错）。"""
    def raise_err(source, sql, max_rows=None):
        raise RuntimeError("connection refused")
    monkeypatch.setattr(di.dbclients_factory, "fetch_rows", raise_err)
    di.invalidate_cache()
    r = client.get(f"/api/assets/introspect/ods.t1?datasource_id={fake_mysql_ds}")
    assert r.status_code == 502
