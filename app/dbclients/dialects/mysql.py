"""MySQL Dialect。"""
from __future__ import annotations

from app.dbclients.dialects import register
from app.dbclients.dialects.base import Dialect
from app.models import DatabaseType


class MysqlDialect(Dialect):
    name = "mysql"

    def introspect_columns_sql(self, schema: str, table: str) -> str:
        if schema:
            return (
                "SELECT COLUMN_NAME AS name, DATA_TYPE AS data_type, "
                "IS_NULLABLE AS nullable, COLUMN_COMMENT AS comment, "
                "ORDINAL_POSITION AS ordinal "
                "FROM information_schema.COLUMNS "
                f"WHERE TABLE_SCHEMA = '{schema}' AND TABLE_NAME = '{table}' "
                "ORDER BY ORDINAL_POSITION"
            )
        return (
            "SELECT COLUMN_NAME AS name, DATA_TYPE AS data_type, "
            "IS_NULLABLE AS nullable, COLUMN_COMMENT AS comment, "
            "ORDINAL_POSITION AS ordinal "
            "FROM information_schema.COLUMNS "
            f"WHERE TABLE_NAME = '{table}' "
            "ORDER BY TABLE_SCHEMA, ORDINAL_POSITION"
        )


register(DatabaseType.MYSQL, MysqlDialect())
