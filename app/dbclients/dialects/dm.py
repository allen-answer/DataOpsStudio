"""达梦 Dialect。

DM 在 SQL 层面（数据字典 / 探活语法）跟 Oracle 一致，所以 `introspect_columns_sql`
和 `connection_test_sql` 直接继承 OracleDialect。但 **驱动层不一样** ——
dmPython 的 connect() 接 server/port 而非 dsn，还有 schema 选项和老接口
positional 兜底，所以 `connect()` 必须 override。
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

    def connect(self, source: DataSource, module_name: str) -> Any:
        driver = importlib.import_module(module_name)
        options = dict(source.extra)
        # database 字段在 DM 语义里是默认 schema
        if source.database and "schema" not in options:
            options["schema"] = source.database
        options.setdefault("login_timeout", CONNECT_TIMEOUT_SECONDS)
        # 新版 dmPython 接 kwargs；老版只接 positional + 拼 host:port —— 用
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
