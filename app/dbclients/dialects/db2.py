"""DB2 Dialect。"""
from __future__ import annotations

import importlib
from typing import Any

from app.dbclients.dialects import register
from app.dbclients.dialects.base import CONNECT_TIMEOUT_SECONDS, Dialect
from app.dbclients.drivers import add_db2_dll_directories
from app.models import DataSource, DatabaseType


class Db2Dialect(Dialect):
    name = "db2"

    def connection_test_sql(self) -> str:
        return "select 1 as ok from sysibm.sysdummy1"

    def apply_call_timeout(self, conn: Any, seconds: float) -> bool:
        # DB2 走 `ibm_db.set_option(conn_handle, {SQL_ATTR_QUERY_TIMEOUT: sec}, 1)`
        # —— 1 表示连接级选项,影响该连接所有后续 cursor.execute。
        # ibm_db_dbi.Connection 暴露底层 handle 在 `.conn_handler`(老版本)/
        # `.conn_handle`(新版本)—— 两个名都试。SQL_ATTR_QUERY_TIMEOUT 单位
        # 是秒,跟我们 caller 参数一致,不必换算。
        # build 不装 ibm_db 时 import 直接抛 ImportError,返 False 让 factory 知
        # 道没生效(也没 SQL fallback,行为退化为「不超时」与本切片前一致)。
        try:
            import ibm_db  # type: ignore
        except ImportError:
            return False
        handle = getattr(conn, "conn_handler", None) or getattr(conn, "conn_handle", None)
        if handle is None:
            return False
        try:
            ibm_db.set_option(
                handle,
                {ibm_db.SQL_ATTR_QUERY_TIMEOUT: int(seconds)},
                1,  # 1 = connection-level option
            )
            return True
        except Exception:
            return False

    def connect(self, source: DataSource, module_name: str) -> Any:
        # ibm_db_dbi 是 DB-API wrapper（包了 ibm_db），ibm_db 直接走 C 扩展。
        # 我们要的是 cursor()/execute() 兼容 DB-API，所以无论 first_available_module
        # 选了哪个，最终都用 ibm_db_dbi。
        add_db2_dll_directories()
        if module_name == "ibm_db_dbi":
            driver = importlib.import_module(module_name)
        else:
            import ibm_db_dbi as driver  # type: ignore
        conn_str = source.extra.get("conn_str") or (
            f"DATABASE={source.database};HOSTNAME={source.host};PORT={source.port};"
            f"PROTOCOL=TCPIP;UID={source.username};PWD={source.password};"
        )
        if "CONNECTTIMEOUT" not in conn_str.upper():
            conn_str += f"CONNECTTIMEOUT={int(source.extra.get('connect_timeout', CONNECT_TIMEOUT_SECONDS))};"
        return driver.connect(conn_str, "", "")

    def introspect_columns_sql(self, schema: str, table: str) -> str:
        if schema:
            return (
                "SELECT NAME AS name, COLTYPE AS data_type, NULLS AS nullable, "
                "REMARKS AS comment, COLNO AS ordinal "
                "FROM SYSIBM.SYSCOLUMNS "
                f"WHERE TBCREATOR = UPPER('{schema}') AND TBNAME = UPPER('{table}') "
                "ORDER BY COLNO"
            )
        return (
            "SELECT NAME AS name, COLTYPE AS data_type, NULLS AS nullable, "
            "REMARKS AS comment, COLNO AS ordinal "
            "FROM SYSIBM.SYSCOLUMNS "
            f"WHERE TBNAME = UPPER('{table}') "
            "ORDER BY COLNO"
        )


register(DatabaseType.DB2, Db2Dialect())
