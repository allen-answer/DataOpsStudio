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
