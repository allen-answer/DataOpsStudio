"""Dialect 抽象基类。

Phase 11 spike：把散在 `services/datasource_introspect.py` / `dbclients/factory.py`
/ `dbclients/pool.py` 的 `if db_type == ...` 分支收口到一个 Dialect 类。

**只放真正会分叉的能力** —— 当前只搬 `introspect_columns_sql`（已有 ≥2 个分支）。
其它候选（`extract_error_detail` / `ping_sql` / `pagination_clause` / `quote_identifier`）
等真出现 present pain 再加进来。**不预先定义完整接口** —— 让接口由实际搬迁的 commit 拼出来。
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class Dialect(ABC):
    """每库一个子类。当前只暴露 introspect_columns_sql。"""

    name: str  # 子类必须设，用于 registry 注册和日志

    @abstractmethod
    def introspect_columns_sql(self, schema: str, table: str) -> str:
        """返回从 information_schema / 数据字典查表字段的 SQL。

        约定：
        - schema/table 已被 caller 通过白名单校验过（`_validate_identifier`），
          所以子类可直接 inline 进字符串。
        - 返回的列至少包含：name, data_type, nullable, comment, ordinal。
        - schema 为 "" 时省略 schema 过滤（跨 schema 拉同名表）。
        - 子类负责自己方言的 ORDER BY / 大小写处理。
        """
        raise NotImplementedError

    @abstractmethod
    def connection_test_sql(self) -> str:
        """连接探活 SQL（`test_connection` 用）。

        约定：返回结果至少含一行一列，列别名 `ok`。各方言「无 FROM」语义不同：
        - MySQL：`select 1 as ok`
        - Oracle / DM：`select 1 as ok from dual`（必须有 FROM）
        - DB2：`select 1 as ok from sysibm.sysdummy1`
        """
        raise NotImplementedError
