"""达梦 Dialect。

DM 高度兼容 Oracle 的数据字典视图（all_tab_columns / all_col_comments），
所以直接复用 OracleDialect 的实现，不开新类（避免空壳子继承）。

如果将来 DM 在 introspect 上出现真分叉（比如自家的 dba_*），再开 DmDialect 子类。
"""
from __future__ import annotations

from app.dbclients.dialects import register
from app.dbclients.dialects.oracle import OracleDialect
from app.models import DatabaseType


register(DatabaseType.DM, OracleDialect())
