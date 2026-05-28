"""达梦 Dialect。

DM 跟 Oracle 在数据字典视图上 **高度兼容但不 100%**:
- `ALL_TAB_COLUMNS` ✓ 通用
- `ALL_COL_COMMENTS` ⚠ 部分版本对 LEFT JOIN ... ON 多列 AND 条件解析失败,
  报 `[CODE: -2007] 语法分析出错` 第 178 列附近(就是 LEFT JOIN 那行)
- `connection_test_sql` / `connect timeout` 等沿用 Oracle 没问题

所以 DM 单独 override `introspect_columns_sql` / `bulk_columns_sql` —— 拿掉
LEFT JOIN, comment 字段返空字符串. 列字段补全 / 对比都不用 comment, 牺牲
这个换"DM 真能用"是合理 trade.

驱动层差异: dmPython 的 connect() 接 server/port 而非 dsn,还有 schema 选项和
老接口 positional 兜底,所以 `connect()` 也 override.
"""
from __future__ import annotations

import importlib
from typing import Any

from app.dbclients.dialects import register
from app.dbclients.dialects.base import CONNECT_TIMEOUT_SECONDS
from app.dbclients.dialects.oracle import OracleDialect
from app.models import DataSource, DatabaseType


class DmDialect(OracleDialect):
    name = "dm"

    def introspect_columns_sql(self, schema: str, table: str) -> str:
        """DM 单表字段查询 — 不用 LEFT JOIN all_col_comments(部分 DM 版本不接),
        comment 字段返空字符串(列名 / 类型 / 是否可空才是字段补全必须的).
        """
        if schema:
            return (
                "SELECT COLUMN_NAME AS name, DATA_TYPE AS data_type, "
                "NULLABLE AS nullable, '' AS comment, COLUMN_ID AS ordinal "
                "FROM all_tab_columns "
                f"WHERE OWNER = UPPER('{schema}') AND TABLE_NAME = UPPER('{table}') "
                "ORDER BY COLUMN_ID"
            )
        return (
            "SELECT COLUMN_NAME AS name, DATA_TYPE AS data_type, "
            "NULLABLE AS nullable, '' AS comment, COLUMN_ID AS ordinal "
            "FROM all_tab_columns "
            f"WHERE TABLE_NAME = UPPER('{table}') "
            "ORDER BY OWNER, COLUMN_ID"
        )

    def bulk_columns_sql(self, schema: str) -> str | None:
        """DM bulk 字段拉取 — 一个 schema 一条 SQL 拉所有表字段,comment 返空."""
        if not schema:
            return None
        return (
            "SELECT TABLE_NAME AS table_name, COLUMN_NAME AS name, "
            "DATA_TYPE AS data_type, NULLABLE AS nullable, "
            "'' AS comment, COLUMN_ID AS ordinal "
            "FROM all_tab_columns "
            f"WHERE OWNER = UPPER('{schema}') "
            "ORDER BY TABLE_NAME, COLUMN_ID"
        )

    def connect(self, source: DataSource, module_name: str) -> Any:
        driver = importlib.import_module(module_name)
        options = dict(source.extra)
        # database 字段在 DM 语义里是默认 schema
        if source.database and "schema" not in options:
            options["schema"] = source.database
        options.setdefault("login_timeout", CONNECT_TIMEOUT_SECONDS)
        # 新版 dmPython 接 kwargs;老版只接 positional + 拼 host:port —— 用
        # try/except 兜底。两次 connect 都会从 _login_timeout option 拿超时。
        try:
            return driver.connect(
                user=source.username,
                password=source.password,
                server=source.host,
                port=source.port,
                **options,
            )
        except Exception:
            return driver.connect(source.username, source.password, f"{source.host}:{source.port}", **options)


register(DatabaseType.DM, DmDialect())
