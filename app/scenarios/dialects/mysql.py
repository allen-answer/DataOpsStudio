"""MySQL materialize dialect。"""
from __future__ import annotations

from app.scenarios.dialects.base import MaterializeDialect


class MysqlMaterializeDialect(MaterializeDialect):
    name = "mysql"

    def quote_identifier(self, ident: str) -> str:
        # 反引号包裹，反引号自身 double 转义
        return "`" + ident.replace("`", "``") + "`"

    def schema_create_sql(self, schema: str) -> str | None:
        return f"CREATE DATABASE IF NOT EXISTS {self.quote_identifier(schema)}"

    def drop_table_sql(self, qfull: str) -> str:
        return f"DROP TABLE IF EXISTS {qfull}"

    def placeholder(self, index: int) -> str:
        return "%s"
