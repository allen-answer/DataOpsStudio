"""DB2 Dialect。"""
from __future__ import annotations

from app.dbclients.dialects import register
from app.dbclients.dialects.base import Dialect
from app.models import DatabaseType


class Db2Dialect(Dialect):
    name = "db2"

    def connection_test_sql(self) -> str:
        return "select 1 as ok from sysibm.sysdummy1"

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
