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
