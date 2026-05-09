"""MySQL Dialect。"""
from __future__ import annotations

import importlib
from typing import Any

from app.dbclients.dialects import register
from app.dbclients.dialects.base import (
    CONNECT_TIMEOUT_SECONDS,
    QUERY_TIMEOUT_SECONDS,
    Dialect,
)
from app.models import DataSource, DatabaseType


class MysqlDialect(Dialect):
    name = "mysql"

    def connection_test_sql(self) -> str:
        return "select 1 as ok"

    def connect(self, source: DataSource, module_name: str) -> Any:
        # pymysql 和 MySQLdb 的 connect() 签名不一样：
        # - pymysql：user/password/database + read_timeout/write_timeout
        # - MySQLdb：user/passwd/db，没 read/write timeout
        driver = importlib.import_module(module_name)
        if module_name == "pymysql":
            return driver.connect(
                host=source.host,
                port=source.port,
                user=source.username,
                password=source.password,
                database=source.database or None,
                charset=source.extra.get("charset", "utf8mb4"),
                connect_timeout=int(source.extra.get("connect_timeout", CONNECT_TIMEOUT_SECONDS)),
                read_timeout=int(source.extra.get("read_timeout", QUERY_TIMEOUT_SECONDS)),
                write_timeout=int(source.extra.get("write_timeout", QUERY_TIMEOUT_SECONDS)),
            )
        return driver.connect(
            host=source.host,
            port=source.port,
            user=source.username,
            passwd=source.password,
            db=source.database or None,
            connect_timeout=int(source.extra.get("connect_timeout", CONNECT_TIMEOUT_SECONDS)),
        )

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
