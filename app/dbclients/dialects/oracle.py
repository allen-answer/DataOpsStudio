"""Oracle Dialect。

DM 跟 Oracle 共享 `introspect_columns_sql` / `connection_test_sql`，但
**`connect()` 不一样**：DM 用 dmPython 驱动，签名是 positional 或 kwarg，
还要落 schema option + 两次调用兜底。所以 `dm.py` 用独立子类只 override
connect，其他方法继承 OracleDialect。
"""
from __future__ import annotations

import importlib
from typing import Any

from app.dbclients.dialects import register
from app.dbclients.dialects.base import Dialect
from app.models import DataSource, DatabaseType


class OracleDialect(Dialect):
    name = "oracle"

    def connection_test_sql(self) -> str:
        return "select 1 as ok from dual"

    def apply_call_timeout(self, conn: Any, seconds: float) -> bool:
        # oracledb / cx_Oracle Connection.callTimeout —— 单位毫秒，作用于所有
        # 后续 round-trip(execute / fetch)，超过即驱动抛 DPI-1067。dmPython 也
        # 基于 DB-API，多数版本兼容；不兼容会 AttributeError，由 caller 吞掉。
        try:
            conn.callTimeout = int(seconds * 1000)
            return True
        except (AttributeError, TypeError):
            return False

    def connect(self, source: DataSource, module_name: str) -> Any:
        # oracledb / cx_Oracle 都接 user/password/dsn 三参数；dsn 优先用
        # extra.dsn（让用户自己写 TNS / Easy Connect），否则按 host:port/service 拼
        driver = importlib.import_module(module_name)
        dsn = source.extra.get("dsn") or f"{source.host}:{source.port}/{source.database}"
        options: dict[str, Any] = {}
        if "tcp_connect_timeout" in source.extra:
            options["tcp_connect_timeout"] = source.extra["tcp_connect_timeout"]
        return driver.connect(user=source.username, password=source.password, dsn=dsn, **options)

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
