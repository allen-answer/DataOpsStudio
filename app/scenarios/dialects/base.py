"""Materialize dialect 抽象基类。

只放真正分叉的能力（identifier quoting / schema 是否可建 / 占位符 / 等）。
DDL 形态（CREATE TABLE / CREATE INDEX）在基类给默认实现，多数方言不用 override。
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.scenarios.models import ColumnDef


class MaterializeDialect(ABC):
    name: str

    # ─── 必须覆盖：标识符引用 + 占位符 + schema 是否可建 + DROP 安全语义 ───

    @abstractmethod
    def quote_identifier(self, ident: str) -> str:
        """Quote 一个标识符（表名 / 列名）。子类负责自身方言的转义规则。"""
        raise NotImplementedError

    @abstractmethod
    def schema_create_sql(self, schema: str) -> str | None:
        """生成 `CREATE DATABASE/SCHEMA IF NOT EXISTS ...` 的 SQL。

        Oracle 这种 schema=user 的方言无法在 INSERT 流程里随便建用户，返回
        None，apply_plan 跳过这一步。
        """
        raise NotImplementedError

    @abstractmethod
    def drop_table_sql(self, qfull: str) -> str:
        """生成「如果表不存在不报错」的 DROP 语句。

        MySQL 直接用 `IF EXISTS`；Oracle / DM 用 PL/SQL 块吞 ORA-00942。
        """
        raise NotImplementedError

    @abstractmethod
    def placeholder(self, index: int) -> str:
        """Index 从 0 开始。MySQL 全 `%s`；Oracle / DM 用 `:1, :2, ...`。"""
        raise NotImplementedError

    # ─── 默认实现：可被覆盖 ─────────────────────────────────────────────

    def quote_qualified(self, full_name: str) -> str:
        """`schema.base` → 各段独立 quote 后用 `.` 连接。"""
        if "." in full_name:
            schema, base = full_name.split(".", 1)
            return f"{self.quote_identifier(schema)}.{self.quote_identifier(base)}"
        return self.quote_identifier(full_name)

    def create_table_sql(self, qfull: str, columns: list[ColumnDef]) -> str:
        """通用 CREATE TABLE：每列 `name type [NOT NULL]` + 最末 `PRIMARY KEY (...)`。

        type 字符串原样输出，让 yml 直接控方言类型（VARCHAR vs VARCHAR2 / DECIMAL
        vs NUMBER）。子类要做类型转换可 override。
        """
        parts: list[str] = []
        pks: list[str] = []
        for c in columns:
            bit = f"{self.quote_identifier(c.name)} {c.type}"
            if not c.nullable:
                bit += " NOT NULL"
            parts.append(bit)
            if c.pk:
                pks.append(self.quote_identifier(c.name))
        if pks:
            parts.append(f"PRIMARY KEY ({', '.join(pks)})")
        return f"CREATE TABLE {qfull} (\n  " + ",\n  ".join(parts) + "\n)"

    def create_index_sql(
        self,
        idx_name: str,
        qfull: str,
        columns: list[str],
        *,
        unique: bool,
    ) -> str:
        cols = ", ".join(self.quote_identifier(c) for c in columns)
        unique_kw = "UNIQUE " if unique else ""
        return f"CREATE {unique_kw}INDEX {idx_name} ON {qfull} ({cols})"

    def insert_sql(self, qfull: str, col_names: list[str]) -> str:
        cols = ", ".join(self.quote_identifier(c) for c in col_names)
        placeholders = ", ".join(self.placeholder(i) for i in range(len(col_names)))
        return f"INSERT INTO {qfull} ({cols}) VALUES ({placeholders})"

    def analyze_table_sql(self, qfull: str) -> str | None:
        """Phase 14 P0-3: materialize 完跑 ANALYZE,让优化器拿到真实统计信息。

        没这步,新建表的 EXPLAIN cardinality 是默认估算(MySQL 默认 1000 行),
        SQL 优化用途下 EXPLAIN 跟实际 plan 完全对不上。

        - MySQL: `ANALYZE TABLE t`(同步,跟数据规模相关 < 1s)
        - Oracle / DM: `BEGIN DBMS_STATS.GATHER_TABLE_STATS('schema', 'table'); END;`
          (DM 兼容)
        - DB2: `RUNSTATS ON TABLE schema.table WITH DISTRIBUTION AND DETAILED INDEXES ALL`
        - 不支持的方言返 None,materialize_streaming 跳过该步

        默认返 None。子类覆盖。
        """
        return None
