"""Phase 11 spike：app.dbclients.dialects registry contract。

只覆盖 registry + Dialect 抽象层。SQL 文本细节由
tests/test_datasource_introspect.py::test_columns_sql_* 已经覆盖，
不重复 assert。
"""
from __future__ import annotations

import pytest

from app.dbclients.dialects import get_dialect
from app.dbclients.dialects.dm import DmDialect
from app.dbclients.dialects.oracle import OracleDialect
from app.models import DatabaseType
from app.models import DataSource


def test_get_dialect_returns_singleton_per_db_type():
    a = get_dialect(DatabaseType.MYSQL)
    b = get_dialect(DatabaseType.MYSQL)
    assert a is b  # registry 缓存同一实例


def test_dm_inherits_oracle_for_connection_test_only():
    """DM 继承 OracleDialect, 共享 connection_test_sql, 但
    introspect_columns_sql / bulk_columns_sql 被 DM override —
    DM 某些版本对 LEFT JOIN all_col_comments 多列 ON 解析失败,
    故 DM 简化不用 JOIN, comment 字段返空."""
    dm = get_dialect(DatabaseType.DM)
    oracle = get_dialect(DatabaseType.ORACLE)
    assert isinstance(dm, OracleDialect)
    assert isinstance(dm, DmDialect)
    assert dm is not oracle
    # 探活共享
    assert dm.connection_test_sql() == oracle.connection_test_sql()
    # 但 introspect / bulk 不共享 — DM 简化版不用 LEFT JOIN
    dm_sql = dm.introspect_columns_sql("ods", "t1")
    assert "LEFT JOIN" not in dm_sql
    assert "all_col_comments" not in dm_sql
    assert "all_tab_columns" in dm_sql
    assert "'' AS comment" in dm_sql  # 注释字段保留 schema 但返空


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


# ─── connect() contract ─────────────────────────────────────────────────────


class _FakeDriver:
    """记录 connect() 调用参数的假驱动 module。"""

    def __init__(self):
        self.kwargs: dict | None = None
        self.args: tuple | None = None

    def connect(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        return object()  # 返回任意 connection-like 对象，pool / cursor 不会调


def _make_source(db_type: DatabaseType, **overrides) -> DataSource:
    base = dict(
        name="t", db_type=db_type.value,
        host="h", port=1234, database="d", username="u", password="p",
        extra={},
    )
    base.update(overrides)
    return DataSource(**{**base, "id": "test-id"})


def test_mysql_connect_pymysql_passes_database_and_charset(monkeypatch):
    """pymysql 用 database= / read_timeout / write_timeout / charset。"""
    fake = _FakeDriver()
    import app.dbclients.dialects.mysql as mod
    monkeypatch.setattr(mod.importlib, "import_module", lambda name: fake)
    src = _make_source(DatabaseType.MYSQL, extra={"charset": "latin1"})
    get_dialect(DatabaseType.MYSQL).connect(src, "pymysql")
    assert fake.kwargs["database"] == "d"
    assert fake.kwargs["password"] == "p"
    assert fake.kwargs["charset"] == "latin1"
    assert "read_timeout" in fake.kwargs
    assert "write_timeout" in fake.kwargs


def test_mysql_connect_mysqldb_uses_legacy_kwargs(monkeypatch):
    """MySQLdb 用 passwd / db，没 read/write timeout。"""
    fake = _FakeDriver()
    import app.dbclients.dialects.mysql as mod
    monkeypatch.setattr(mod.importlib, "import_module", lambda name: fake)
    src = _make_source(DatabaseType.MYSQL)
    get_dialect(DatabaseType.MYSQL).connect(src, "MySQLdb")
    assert fake.kwargs["passwd"] == "p"
    assert fake.kwargs["db"] == "d"
    assert "password" not in fake.kwargs
    assert "read_timeout" not in fake.kwargs


def test_oracle_connect_uses_easy_connect_dsn(monkeypatch):
    """没传 extra.dsn 时 fallback 拼 host:port/database。"""
    fake = _FakeDriver()
    import app.dbclients.dialects.oracle as mod
    monkeypatch.setattr(mod.importlib, "import_module", lambda name: fake)
    src = _make_source(DatabaseType.ORACLE, host="h", port=1521, database="orcl")
    get_dialect(DatabaseType.ORACLE).connect(src, "oracledb")
    assert fake.kwargs["dsn"] == "h:1521/orcl"


def test_oracle_connect_honors_extra_dsn(monkeypatch):
    """extra.dsn 传了 TNS 就直接用，不再拼 host:port。"""
    fake = _FakeDriver()
    import app.dbclients.dialects.oracle as mod
    monkeypatch.setattr(mod.importlib, "import_module", lambda name: fake)
    src = _make_source(DatabaseType.ORACLE, extra={"dsn": "(DESCRIPTION=...)"})
    get_dialect(DatabaseType.ORACLE).connect(src, "oracledb")
    assert fake.kwargs["dsn"] == "(DESCRIPTION=...)"


def test_dm_connect_injects_schema_from_database(monkeypatch):
    """DM 的 source.database 当默认 schema 用。"""
    fake = _FakeDriver()
    import app.dbclients.dialects.dm as mod
    monkeypatch.setattr(mod.importlib, "import_module", lambda name: fake)
    src = _make_source(DatabaseType.DM, database="MYAPP")
    get_dialect(DatabaseType.DM).connect(src, "dmPython")
    assert fake.kwargs["schema"] == "MYAPP"
    assert "login_timeout" in fake.kwargs


def test_dm_connect_falls_back_to_positional_on_kwarg_failure(monkeypatch):
    """老版 dmPython 不接 kwargs，第一次抛异常后用 positional 兜底。"""
    calls = []

    class FlakyDriver:
        def connect(self, *args, **kwargs):
            calls.append((args, kwargs))
            if not args:
                raise TypeError("old dmPython does not accept kwargs")
            return object()

    fake = FlakyDriver()
    import app.dbclients.dialects.dm as mod
    monkeypatch.setattr(mod.importlib, "import_module", lambda name: fake)
    src = _make_source(DatabaseType.DM)
    get_dialect(DatabaseType.DM).connect(src, "dmPython")
    assert len(calls) == 2
    # 第二次必须是 positional：(user, password, "host:port")
    assert calls[1][0][:3] == ("u", "p", "h:1234")


def test_db2_connect_builds_conn_str_when_extra_missing(monkeypatch):
    """没 extra.conn_str 时按 DATABASE / HOSTNAME / PORT / UID / PWD 拼。"""
    fake = _FakeDriver()
    import app.dbclients.dialects.db2 as mod
    monkeypatch.setattr(mod, "add_db2_dll_directories", lambda: None)
    monkeypatch.setattr(mod.importlib, "import_module", lambda name: fake)
    src = _make_source(DatabaseType.DB2)
    get_dialect(DatabaseType.DB2).connect(src, "ibm_db_dbi")
    conn_str = fake.args[0]
    assert "DATABASE=d" in conn_str
    assert "HOSTNAME=h" in conn_str
    assert "PORT=1234" in conn_str
    assert "CONNECTTIMEOUT=" in conn_str  # 自动补


def test_db2_connect_honors_extra_conn_str(monkeypatch):
    """extra.conn_str 传了就直接用，不再按字段拼。"""
    fake = _FakeDriver()
    import app.dbclients.dialects.db2 as mod
    monkeypatch.setattr(mod, "add_db2_dll_directories", lambda: None)
    monkeypatch.setattr(mod.importlib, "import_module", lambda name: fake)
    src = _make_source(DatabaseType.DB2, extra={"conn_str": "DATABASE=X;HOSTNAME=Y;PORT=1;UID=u;PWD=p;"})
    get_dialect(DatabaseType.DB2).connect(src, "ibm_db_dbi")
    conn_str = fake.args[0]
    assert "DATABASE=X" in conn_str
    assert "HOSTNAME=Y" in conn_str
