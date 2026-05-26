"""SQL Workbench 元数据 helper(Phase 3) —— schemas / tables / columns。

columns 复用既有 `services.datasource_introspect.introspect_columns`;
schemas / tables 这两层 v0.1 直接 dispatch SQL(简单 + 不改 dialect 抽象)。

支持矩阵:
- MySQL:  information_schema.SCHEMATA / TABLES(常用,默认开)
- Oracle: ALL_USERS / ALL_TABLES(查 schema = USERNAME)
- DM:     同 Oracle 协议
- DB2:    SYSCAT.SCHEMATA / SYSCAT.TABLES
- 其它:   返空 + reason 标 v0.1 未支持
"""
from __future__ import annotations

import logging
from typing import Any

from app.dbclients import factory as dbclients_factory
from app.dbclients.dialects import get_dialect
from app.services.datasource_introspect import _validate_identifier, introspect_columns

logger = logging.getLogger(__name__)


# 内置忽略的系统 schema —— MySQL / Oracle / DM 都有大量内置 schema 噪音
_MYSQL_SYSTEM_SCHEMAS = {"information_schema", "mysql", "performance_schema", "sys"}
_ORACLE_SYSTEM_USERS = {
    "SYS", "SYSTEM", "OUTLN", "DBSNMP", "APPQOSSYS", "XDB", "ANONYMOUS",
    "CTXSYS", "MDSYS", "ORDDATA", "ORDPLUGINS", "ORDSYS", "OLAPSYS", "WMSYS",
    "EXFSYS", "FLOWS_FILES", "MDDATA", "ORACLE_OCM", "SI_INFORMTN_SCHEMA",
    "SPATIAL_CSW_ADMIN_USR", "SPATIAL_WFS_ADMIN_USR", "GSMADMIN_INTERNAL",
    "PUBLIC", "DIP", "AUDSYS", "GGSYS", "DBSFWUSER", "REMOTE_SCHEDULER_AGENT",
    "LBACSYS", "SYSBACKUP", "SYSDG", "SYSKM", "SYSRAC",
    # DM 自带的系统 schema 比 oracle 多但子集对齐
    "SYSDBA", "SYSSSO", "SYSAUDITOR",
}
_DB2_SYSTEM_SCHEMAS_PREFIX = ("SYS", "DB2", "ERR", "NULLID", "SQLJ", "ST_")


def _db_type(source: Any) -> str:
    raw = getattr(source.db_type, "value", source.db_type)
    return str(raw).lower()


def list_schemas(source: Any) -> list[dict[str, str]]:
    """返 [{name: '...'}],按字典序;系统 schema 过滤掉。"""
    db_type = _db_type(source)
    if db_type == "mysql":
        sql = ("SELECT SCHEMA_NAME AS name FROM information_schema.SCHEMATA "
               "ORDER BY SCHEMA_NAME")
    elif db_type in ("oracle", "dm"):
        sql = "SELECT USERNAME AS name FROM ALL_USERS ORDER BY USERNAME"
    elif db_type == "db2":
        sql = "SELECT SCHEMANAME AS name FROM SYSCAT.SCHEMATA ORDER BY SCHEMANAME"
    else:
        return []

    rows = dbclients_factory.fetch_rows(source, sql, max_rows=2000)
    items: list[dict[str, str]] = []
    for r in rows:
        name = _pick(r, ["name", "NAME", "Name"]) or ""
        name_str = str(name).strip()
        if not name_str:
            continue
        if _is_system_schema(db_type, name_str):
            continue
        items.append({"name": name_str})
    return items


def list_tables(source: Any, schema: str = "") -> list[dict[str, str]]:
    """返 [{name: '...', schema: '...'}],按表名升序。schema 空时:
    - MySQL  → 当前 database(source.database)
    - Oracle/DM → 当前 user 的 schema(`USER`)
    - DB2    → 当前 CURRENT_SCHEMA
    """
    db_type = _db_type(source)
    if schema:
        _validate_identifier(schema)

    if db_type == "mysql":
        sch = schema or (source.database or "")
        if not sch:
            return []
        _validate_identifier(sch)
        # %s 占位符在 fetch_rows 走 cursor.execute(sql, params) 路径才安全;
        # 这里直接拼字符串但 schema 已经过 _validate_identifier alphanum/_ 白名单
        sql = ("SELECT TABLE_NAME AS name FROM information_schema.TABLES "
               f"WHERE TABLE_SCHEMA = '{sch}' AND TABLE_TYPE = 'BASE TABLE' "
               "ORDER BY TABLE_NAME")
    elif db_type in ("oracle", "dm"):
        sch = schema.upper() if schema else ""
        if not sch:
            sql = "SELECT TABLE_NAME AS name FROM USER_TABLES ORDER BY TABLE_NAME"
        else:
            sql = (f"SELECT TABLE_NAME AS name FROM ALL_TABLES "
                   f"WHERE OWNER = '{sch}' ORDER BY TABLE_NAME")
    elif db_type == "db2":
        sch = schema.upper() if schema else ""
        if not sch:
            sql = ("SELECT TABNAME AS name FROM SYSCAT.TABLES "
                   "WHERE TABSCHEMA = CURRENT_SCHEMA ORDER BY TABNAME")
        else:
            sql = (f"SELECT TABNAME AS name FROM SYSCAT.TABLES "
                   f"WHERE TABSCHEMA = '{sch}' AND TYPE = 'T' ORDER BY TABNAME")
    else:
        return []

    rows = dbclients_factory.fetch_rows(source, sql, max_rows=5000)
    items: list[dict[str, str]] = []
    for r in rows:
        name = _pick(r, ["name", "NAME"]) or ""
        name_str = str(name).strip()
        if not name_str:
            continue
        items.append({"name": name_str, "schema": schema})
    return items


def list_columns(source: Any, table: str, schema: str = "") -> list[dict[str, Any]]:
    """复用既有 introspect_columns;表名 'schema.table' 也接受。"""
    full = f"{schema}.{table}" if schema and "." not in table else table
    return introspect_columns(source.id, full)


def list_indexes(source: Any, table: str, schema: str = "") -> list[dict[str, Any]]:
    """返 [{index_name, column_name, non_unique, seq_in_index, index_type}],
    按 (index_name, seq_in_index) 升序。同一索引多列展开为多行。

    不支持的方言(目前 DB2)返 [],不抛。
    """
    _validate_identifier(table)
    if schema:
        _validate_identifier(schema)
    dialect = get_dialect(source.db_type)
    sql = dialect.list_indexes_sql(schema, table)
    if sql is None:
        return []
    rows = dbclients_factory.fetch_rows(source, sql, max_rows=1000)
    return [_normalize_index_row(r) for r in rows]


def list_views(source: Any, schema: str = "") -> list[dict[str, str]]:
    """返 [{name, schema}]。schema 空时按方言默认(MySQL: DATABASE(),Oracle: USER)。
    不支持的方言返 []。"""
    if schema:
        _validate_identifier(schema)
    dialect = get_dialect(source.db_type)
    sql = dialect.list_views_sql(schema)
    if sql is None:
        return []
    rows = dbclients_factory.fetch_rows(source, sql, max_rows=2000)
    items: list[dict[str, str]] = []
    for r in rows:
        name = _pick(r, ["name", "NAME"]) or ""
        name_str = str(name).strip()
        if not name_str:
            continue
        items.append({"name": name_str, "schema": schema})
    return items


def get_table_ddl(source: Any, table: str, schema: str = "") -> str | None:
    """返表的 CREATE TABLE 文本;不支持的方言或失败返 None。

    约定:取结果第一行最后一列(MySQL SHOW CREATE TABLE 第 2 列就是 DDL)。
    Oracle/DM 暂不支持(留 None),前端提示「该方言不支持 DDL 抽取」。
    """
    _validate_identifier(table)
    if schema:
        _validate_identifier(schema)
    dialect = get_dialect(source.db_type)
    sql = dialect.show_create_table_sql(schema, table)
    if sql is None:
        return None
    try:
        rows = dbclients_factory.fetch_rows(source, sql, max_rows=1)
    except Exception as exc:
        logger.warning("get_table_ddl failed: %s", exc)
        return None
    if not rows:
        return None
    row = rows[0]
    # MySQL `SHOW CREATE TABLE` 返列名是 "Create Table"(含空格),取倒数最后一个
    # 字符串值最稳健 —— 多方言通用。
    values = list(row.values())
    if not values:
        return None
    ddl = values[-1]
    if ddl is None:
        return None
    return str(ddl)


def _normalize_index_row(row: dict) -> dict[str, Any]:
    """统一索引行字段名,小写化。"""
    return {
        "index_name": str(_pick(row, ["index_name", "INDEX_NAME", "Index_name", "Key_name"]) or "").strip(),
        "column_name": str(_pick(row, ["column_name", "COLUMN_NAME", "Column_name"]) or "").strip(),
        "non_unique": int(_pick(row, ["non_unique", "NON_UNIQUE", "Non_unique"]) or 0),
        "seq_in_index": int(_pick(row, ["seq_in_index", "SEQ_IN_INDEX", "Seq_in_index"]) or 0),
        "index_type": str(_pick(row, ["index_type", "INDEX_TYPE", "Index_type"]) or "").strip(),
    }


def _pick(row: dict, candidates: list[str]) -> Any:
    for k in candidates:
        if k in row:
            return row[k]
    return None


def _is_system_schema(db_type: str, name: str) -> bool:
    if db_type == "mysql":
        return name.lower() in _MYSQL_SYSTEM_SCHEMAS
    if db_type in ("oracle", "dm"):
        return name.upper() in _ORACLE_SYSTEM_USERS
    if db_type == "db2":
        return name.upper().startswith(_DB2_SYSTEM_SCHEMAS_PREFIX)
    return False
