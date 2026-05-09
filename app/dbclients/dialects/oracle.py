"""Oracle Dialect。

DM 几乎完全兼容 Oracle 数据字典（all_tab_columns / all_col_comments），
所以 dm.py 直接 register 同一个实例。
"""
from __future__ import annotations

from app.dbclients.dialects import register
from app.dbclients.dialects.base import Dialect
from app.models import DatabaseType


class OracleDialect(Dialect):
    name = "oracle"

    def connection_test_sql(self) -> str:
        return "select 1 as ok from dual"

    def introspect_columns_sql(self, schema: str, table: str) -> str:
        # NULLABLE 是 'Y' / 'N'；comments 在 all_col_comments 里需 join
        # Oracle 标识符默认大写 → 用 UPPER() 包 schema/table
        if schema:
            return (
                "SELECT c.COLUMN_NAME AS name, c.DATA_TYPE AS data_type, "
                "c.NULLABLE AS nullable, cc.COMMENTS AS comment, "
                "c.COLUMN_ID AS ordinal "
                "FROM all_tab_columns c "
                "LEFT JOIN all_col_comments cc ON cc.OWNER = c.OWNER "
                "  AND cc.TABLE_NAME = c.TABLE_NAME "
                "  AND cc.COLUMN_NAME = c.COLUMN_NAME "
                f"WHERE c.OWNER = UPPER('{schema}') AND c.TABLE_NAME = UPPER('{table}') "
                "ORDER BY c.COLUMN_ID"
            )
        return (
            "SELECT c.COLUMN_NAME AS name, c.DATA_TYPE AS data_type, "
            "c.NULLABLE AS nullable, cc.COMMENTS AS comment, "
            "c.COLUMN_ID AS ordinal "
            "FROM all_tab_columns c "
            "LEFT JOIN all_col_comments cc ON cc.OWNER = c.OWNER "
            "  AND cc.TABLE_NAME = c.TABLE_NAME "
            "  AND cc.COLUMN_NAME = c.COLUMN_NAME "
            f"WHERE c.TABLE_NAME = UPPER('{table}') "
            "ORDER BY c.OWNER, c.COLUMN_ID"
        )


register(DatabaseType.ORACLE, OracleDialect())
