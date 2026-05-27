"""Dialect 抽象基类。

Phase 11 spike：把散在 `services/datasource_introspect.py` / `dbclients/factory.py`
/ `dbclients/pool.py` 的 `if db_type == ...` 分支收口到一个 Dialect 类。

**只放真正会分叉的能力** —— 已搬 `introspect_columns_sql` / `connection_test_sql`
/ `connect`。其它候选（`extract_error_detail` / `ping_sql` / `pagination_clause`
/ `quote_identifier`）等真出现 present pain 再加进来。**不预先定义完整接口** ——
让接口由实际搬迁的 commit 拼出来。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.models import DataSource


# 连接 / 查询超时默认值，所有方言子类共享。原本住在 factory.py，搬到这里让
# dialect.connect() 实现自己引用，避免 factory 与 dialects 互相 import。
CONNECT_TIMEOUT_SECONDS = 10
QUERY_TIMEOUT_SECONDS = 300


class Dialect(ABC):
    """每库一个子类。"""

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

    def bulk_columns_sql(self, schema: str) -> str | None:
        """一次拉一个 schema 全部表的字段(给 SQL 编辑器补全做批量预热)。

        约定:
        - 返回的列至少包含 `table_name` + `name` + `data_type` + `nullable` + `ordinal`
        - 按 (table_name, ordinal) 排序,caller groupby table_name 即可
        - 不支持的方言返 None,caller fallback 到逐表 introspect_columns_sql

        默认 None —— 子类按需重载。bulk 优势在大 schema(几百张表)预热时把
        N 个 round trip 折成 1 次。
        """
        return None

    @abstractmethod
    def connection_test_sql(self) -> str:
        """连接探活 SQL（`test_connection` 用）。

        约定：返回结果至少含一行一列，列别名 `ok`。各方言「无 FROM」语义不同：
        - MySQL：`select 1 as ok`
        - Oracle / DM：`select 1 as ok from dual`（必须有 FROM）
        - DB2：`select 1 as ok from sysibm.sysdummy1`
        """
        raise NotImplementedError

    def statement_timeout_sql(self, seconds: float) -> str | None:
        """返回一条「设置本会话语句超时」的 SQL；不支持的方言返回 None。

        在每次查询执行前由 `factory._apply_statement_timeout` best-effort 下发
        —— 防止一条慢查询长期占住数据库连接。`seconds` 由 caller 保证 > 0。

        默认返回 None（不支持）。MySQL 子类覆盖。Oracle / DM 走驱动连接属性路径
        （`apply_call_timeout`），不再用 SQL。
        """
        return None

    def apply_call_timeout(self, conn: Any, seconds: float) -> bool:
        """driver 连接属性级 round-trip 超时；不支持返回 False。

        优先于 `statement_timeout_sql` 被 `factory._apply_statement_timeout`
        调用。返回 True 表示已生效（caller 跳过 SQL fallback），False 表示
        驱动不支持该属性。

        默认 False。Oracle / DM 子类覆盖（设 `conn.callTimeout = ms`）。
        """
        return False

    def estimate_rows_from_explain(self, conn: Any, sql: str) -> int | None:
        """走 driver EXPLAIN 估算 SQL 返回行数;不支持的方言返 None。

        给 `sql_preflight.assess_with_explain` 用:任务下发前看 plan 估算,
        发现超 `max_rows × 10` 加 warn finding。**不阻塞**真查询(EXPLAIN
        失败 / 不支持都不该让真任务跑不起来),caller 用 try/except 兜底。

        默认返 None(不支持)。MySQL 子类实现 —— Oracle / DM 走 `EXPLAIN PLAN
        FOR ...; SELECT FROM PLAN_TABLE` 两步,DB2 走 explain mode,留后续切片。
        """
        return None

    def list_indexes_sql(self, schema: str, table: str) -> str | None:
        """返回查"某表所有索引(含列级)"的 SQL,不支持的方言返 None。

        约定:
        - schema / table 已被 caller `_validate_identifier` 校验,可直接 inline
        - 返回结果至少包含:index_name, column_name, non_unique (0/1), seq_in_index,
          index_type(各方言原始字符串,UI 透传)
        - 同一索引多个列展开为多行(seq_in_index 排序)

        默认 None。MySQL / Oracle / DM 子类覆盖,DB2 留 None。
        """
        return None

    def list_views_sql(self, schema: str) -> str | None:
        """返回查"某 schema 下所有视图"的 SQL,不支持的方言返 None。

        - 至少含 view_name 字段。schema 为空时按方言默认 (current_schema / user)。
        默认 None。MySQL / Oracle / DM 子类覆盖。
        """
        return None

    def show_create_table_sql(self, schema: str, table: str) -> str | None:
        """返回查"基础建表 DDL"的 SQL,不支持的方言返 None。

        约定:执行后取第一行最后一列作为 DDL 文本。
        - MySQL:`SHOW CREATE TABLE \\`s\\`.\\`t\\`` 返 (Table, Create Table) 两列
        - Oracle/DM:DBMS_METADATA.GET_DDL 返 CLOB,驱动 LOB 处理复杂,暂留 None
        - DB2 留 None

        默认 None。
        """
        return None

    @abstractmethod
    def connect(self, source: DataSource, module_name: str) -> Any:
        """新建一个 DB 连接对象。

        - `source`：用户在前端配的 datasource（host/port/credentials/extra dict）
        - `module_name`：`first_available_module(db_type)` 选出的可用驱动包名
          —— 同一 db_type 可能有多个候选驱动（pymysql / MySQLdb；oracledb /
          cx_Oracle；ibm_db_dbi / ibm_db），子类 `import_module(module_name)`
          后按 module_name 分支处理 kwarg 差异

        约定：
        - 返回值是裸 connection 对象（实现 DB-API v2 cursor() / close()），由
          `pool.borrow` 接管 release/close。子类不要自己包 try/finally close。
        - 失败抛异常即可（factory.fetch_rows 会包成 DbClientError）。
        - 子类需 `source.extra.get(...)` 读取方言特有配置（charset / dsn /
          conn_str / schema 等），不要假设 extra 里有特定 key。
        """
        raise NotImplementedError
