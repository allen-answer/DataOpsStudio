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

    def statement_timeout_sql(self, seconds: float) -> str | None:
        # MySQL `max_execution_time` 单位毫秒，且只作用于只读 SELECT —— 正好
        # 是本系统所有查询的形态。MariaDB 不认这个变量，下发会失败，由
        # factory 的 best-effort 包装吞掉（记 warning，不影响真查询）。
        return f"SET SESSION MAX_EXECUTION_TIME={int(seconds * 1000)}"

    def estimate_rows_from_explain(self, conn, sql: str) -> int | None:
        # MySQL `EXPLAIN <sql>` 返回 plan 行,每行有 `rows` 列(估算扫描行数)。
        # join 多表时每个 driving table 一行 —— 单步骤 rows 是该步过滤后行数,
        # 不是累乘的最终输出。取 max 作为「最坏单步代价」估算更接近真实风险:
        # 选 sum 会高估,选 last 会漏 join broadcast 的 fan-out。
        # 失败(SQL 已被 sql_preflight 静态拦的不该到这,但 driver 偶发也得防)
        # 返 None,让 caller try/except 兜底,不阻塞真查询。
        cursor = None
        try:
            cursor = conn.cursor()
            cursor.execute(f"EXPLAIN {sql}")
            cols = [d[0].lower() if d and d[0] else "" for d in cursor.description or []]
            if "rows" not in cols:
                return None
            idx = cols.index("rows")
            max_rows = 0
            for row in cursor.fetchall():
                val = row[idx]
                if val is None:
                    continue
                try:
                    n = int(val)
                except (TypeError, ValueError):
                    continue
                if n > max_rows:
                    max_rows = n
            return max_rows
        except Exception:
            return None
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:
                    pass

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

    def list_indexes_sql(self, schema: str, table: str) -> str | None:
        # information_schema.STATISTICS 一行一(索引,列),适合 UI 列表渲染。
        # NON_UNIQUE=0 表示唯一索引。SEQ_IN_INDEX 是该索引内列的次序(组合索引)。
        if schema:
            return (
                "SELECT INDEX_NAME AS index_name, COLUMN_NAME AS column_name, "
                "NON_UNIQUE AS non_unique, SEQ_IN_INDEX AS seq_in_index, "
                "INDEX_TYPE AS index_type "
                "FROM information_schema.STATISTICS "
                f"WHERE TABLE_SCHEMA = '{schema}' AND TABLE_NAME = '{table}' "
                "ORDER BY INDEX_NAME, SEQ_IN_INDEX"
            )
        return (
            "SELECT INDEX_NAME AS index_name, COLUMN_NAME AS column_name, "
            "NON_UNIQUE AS non_unique, SEQ_IN_INDEX AS seq_in_index, "
            "INDEX_TYPE AS index_type "
            "FROM information_schema.STATISTICS "
            f"WHERE TABLE_NAME = '{table}' "
            "ORDER BY TABLE_SCHEMA, INDEX_NAME, SEQ_IN_INDEX"
        )

    def list_views_sql(self, schema: str) -> str | None:
        if schema:
            return (
                "SELECT TABLE_NAME AS name FROM information_schema.VIEWS "
                f"WHERE TABLE_SCHEMA = '{schema}' ORDER BY TABLE_NAME"
            )
        # 空 schema 时用 DATABASE() 当前库
        return (
            "SELECT TABLE_NAME AS name FROM information_schema.VIEWS "
            "WHERE TABLE_SCHEMA = DATABASE() ORDER BY TABLE_NAME"
        )

    def show_create_table_sql(self, schema: str, table: str) -> str | None:
        # SHOW CREATE TABLE 返回 (Table, "Create Table") 两列,DDL 在第二列。
        # caller 取 row[1] / row[-1] 拿 DDL 字符串。
        # MySQL 标识符用反引号包裹,schema/table 已经 _validate_identifier 过。
        if schema:
            return f"SHOW CREATE TABLE `{schema}`.`{table}`"
        return f"SHOW CREATE TABLE `{table}`"

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
