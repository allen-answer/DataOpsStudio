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


# ─── Phase 14 P1-1 完成版:introspect_indexes 多方言 ───────────────────────


def test_introspect_indexes_mysql_groups_by_index(fake_mysql_ds, monkeypatch):
    """MySQL SHOW INDEX 输出按 index 名 group + 按 seq 排 columns + PK 标记"""
    fake_rows = [
        {"Key_name": "PRIMARY", "Column_name": "id", "Seq_in_index": 1, "Non_unique": 0},
        {"Key_name": "idx_status_dt", "Column_name": "status", "Seq_in_index": 1, "Non_unique": 1},
        {"Key_name": "idx_status_dt", "Column_name": "created_at", "Seq_in_index": 2, "Non_unique": 1},
    ]
    monkeypatch.setattr(di.dbclients_factory, "fetch_rows",
                        lambda *a, **k: fake_rows)
    indexes = di.introspect_indexes(fake_mysql_ds, "orders")
    assert len(indexes) == 2
    # PK 在前
    assert indexes[0]["name"] == "PRIMARY"
    assert indexes[0]["is_pk"] is True
    assert indexes[0]["unique"] is True
    # 复合索引按 seq 排
    assert indexes[1]["name"] == "idx_status_dt"
    assert indexes[1]["columns"] == ["status", "created_at"]
    assert indexes[1]["is_pk"] is False
    assert indexes[1]["unique"] is False


@pytest.fixture
def fake_oracle_ds(isolated_storage):
    ds = datasource_store.create(DataSourceCreate(
        name="prod-oracle", db_type="Oracle",
        host="db", port=1521, database="ORCL", username="u", password="p",
    ))
    return ds.id


@pytest.fixture
def fake_dm_ds(isolated_storage):
    ds = datasource_store.create(DataSourceCreate(
        name="prod-dm", db_type="DM",
        host="db", port=5236, database="DMSERVER", username="u", password="p",
    ))
    return ds.id


@pytest.fixture
def fake_db2_ds(isolated_storage):
    ds = datasource_store.create(DataSourceCreate(
        name="prod-db2", db_type="DB2",
        host="db", port=50000, database="SAMPLE", username="u", password="p",
    ))
    return ds.id


def test_introspect_indexes_oracle_uses_user_indexes(fake_oracle_ds, monkeypatch):
    """Oracle 走 ALL_INDEXES + ALL_IND_COLUMNS,正确解析 unique + 多列顺序"""
    calls: list[str] = []
    def fake_fetch(source, sql, max_rows=None):
        calls.append(sql)
        if "ALL_INDEXES" in sql and "ALL_IND_COLUMNS" in sql:
            return [
                {"IDX_NAME": "PK_ORDERS", "UNIQUENESS": "UNIQUE",
                 "COL_NAME": "ID", "COL_POS": 1},
                {"IDX_NAME": "IDX_STATUS", "UNIQUENESS": "NONUNIQUE",
                 "COL_NAME": "STATUS", "COL_POS": 1},
                {"IDX_NAME": "IDX_STATUS", "UNIQUENESS": "NONUNIQUE",
                 "COL_NAME": "CREATED_AT", "COL_POS": 2},
            ]
        if "ALL_CONSTRAINTS" in sql:
            return [{"INDEX_NAME": "PK_ORDERS"}]
        return []

    monkeypatch.setattr(di.dbclients_factory, "fetch_rows", fake_fetch)
    indexes = di.introspect_indexes(fake_oracle_ds, "ORDERS")
    assert len(indexes) == 2
    pk = next(i for i in indexes if i["is_pk"])
    assert pk["name"] == "PK_ORDERS"
    assert pk["unique"] is True
    composite = next(i for i in indexes if i["name"] == "IDX_STATUS")
    assert composite["columns"] == ["STATUS", "CREATED_AT"]
    assert composite["unique"] is False
    # 验证主查询拼了 ALL_INDEXES join + 二次 PK 查询
    assert any("ALL_INDEXES" in s and "JOIN ALL_IND_COLUMNS" in s for s in calls)
    assert any("ALL_CONSTRAINTS" in s for s in calls)


def test_introspect_indexes_dm_uses_same_path_as_oracle(fake_dm_ds, monkeypatch):
    """DM 兼容 Oracle 数据字典视图,走同一条 SQL"""
    calls: list[str] = []
    def fake_fetch(source, sql, max_rows=None):
        calls.append(sql)
        if "ALL_INDEXES" in sql:
            return [{"IDX_NAME": "IDX_T1", "UNIQUENESS": "UNIQUE",
                     "COL_NAME": "C1", "COL_POS": 1}]
        return []
    monkeypatch.setattr(di.dbclients_factory, "fetch_rows", fake_fetch)
    indexes = di.introspect_indexes(fake_dm_ds, "T1")
    assert len(indexes) == 1
    assert indexes[0]["unique"] is True
    # DM 跟 Oracle 同一条 SQL
    assert any("ALL_INDEXES" in s for s in calls)


def test_introspect_indexes_db2_uses_syscat(fake_db2_ds, monkeypatch):
    """DB2 走 SYSCAT.INDEXES + SYSCAT.INDEXCOLUSE,uniquerule 'P' = PK"""
    def fake_fetch(source, sql, max_rows=None):
        if "SYSCAT.INDEXES" in sql:
            return [
                {"IDX_NAME": "PK_T1", "UNIQUERULE": "P",
                 "COL_NAME": "ID", "COL_POS": 1},
                {"IDX_NAME": "IDX_T1_NAME", "UNIQUERULE": "D",
                 "COL_NAME": "NAME", "COL_POS": 1},
                {"IDX_NAME": "UQ_T1_EMAIL", "UNIQUERULE": "U",
                 "COL_NAME": "EMAIL", "COL_POS": 1},
            ]
        return []
    monkeypatch.setattr(di.dbclients_factory, "fetch_rows", fake_fetch)
    indexes = di.introspect_indexes(fake_db2_ds, "T1")
    assert len(indexes) == 3
    pk = next(i for i in indexes if i["is_pk"])
    assert pk["name"] == "PK_T1"
    assert pk["unique"] is True
    uq = next(i for i in indexes if i["name"] == "UQ_T1_EMAIL")
    assert uq["unique"] is True
    assert uq["is_pk"] is False
    normal = next(i for i in indexes if i["name"] == "IDX_T1_NAME")
    assert normal["unique"] is False


def test_introspect_indexes_oracle_query_fails_safe_degrade(fake_oracle_ds, monkeypatch):
    """Oracle 主查询失败 → 返 [],不阻塞 yml_importer"""
    def fake_fetch(*a, **k):
        raise RuntimeError("ORA-00942: table or view does not exist")
    monkeypatch.setattr(di.dbclients_factory, "fetch_rows", fake_fetch)
    assert di.introspect_indexes(fake_oracle_ds, "MISSING_TABLE") == []


def test_introspect_indexes_oracle_pk_query_fails_keeps_other_indexes(fake_oracle_ds, monkeypatch):
    """Oracle PK 二次 query 失败 → 索引列表还在,只是没标 is_pk"""
    def fake_fetch(source, sql, max_rows=None):
        if "ALL_INDEXES" in sql:
            return [{"IDX_NAME": "IDX_T1", "UNIQUENESS": "NONUNIQUE",
                     "COL_NAME": "C1", "COL_POS": 1}]
        if "ALL_CONSTRAINTS" in sql:
            raise RuntimeError("permission denied on ALL_CONSTRAINTS")
        return []
    monkeypatch.setattr(di.dbclients_factory, "fetch_rows", fake_fetch)
    indexes = di.introspect_indexes(fake_oracle_ds, "T1")
    assert len(indexes) == 1
    assert indexes[0]["is_pk"] is False  # PK 没标但索引在


def test_introspect_indexes_table_validates_identifier(fake_oracle_ds):
    """表名走 _validate_identifier,防 SQL 注入"""
    with pytest.raises(ValueError):
        di.introspect_indexes(fake_oracle_ds, "T1; DROP TABLE foo--")
