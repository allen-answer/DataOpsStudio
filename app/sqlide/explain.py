"""SQL Workbench Explain —— 拉执行计划。

v0.2 保守版本:
- MySQL / OceanBase MySQL mode:跑 `EXPLAIN <sql>`,fetch_rows 拿计划表
- Oracle / DM / OceanBase Oracle mode:返 unsupported(EXPLAIN PLAN 需要写
  PLAN_TABLE,跟 slow_sql.analyze_sql 重复且有合规风险 —— 让用户去 SQL 诊断
  模块用更完整的 EXPLAIN PLAN 链路)
- DB2:同 unsupported

复用 sql_guard 拦 DML/DDL —— EXPLAIN 仍只对 SELECT/WITH。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from app.dbclients.factory import DbClientError, fetch_rows_with_schema
from app.utils.sql_guard import validate_readonly_sql


logger = logging.getLogger(__name__)


@dataclass
class ExplainResult:
    success: bool
    dialect: str = ""
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    explain_sql: str = ""
    elapsed_ms: int = 0
    error: str | None = None
    unsupported: bool = False  # True 时表示该 db_type 不支持 EXPLAIN(跟一般错误区分)


def _db_type(source: Any) -> str:
    raw = getattr(source.db_type, "value", source.db_type)
    return str(raw).lower()


def _serialize_cell(value: Any) -> Any:
    """跟 executor._serialize_cell 同口径(避免 import cycle 单独保留一份)。"""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    try:
        from datetime import date, datetime, time as _time
        if isinstance(value, (datetime, date, _time)):
            return value.isoformat()
    except Exception:
        pass
    try:
        from decimal import Decimal
        if isinstance(value, Decimal):
            return float(value)
    except Exception:
        pass
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    return str(value)


def explain_sql(source: Any, sql: str) -> ExplainResult:
    """跑 EXPLAIN 拿执行计划。Oracle / DM / DB2 返 unsupported。"""
    try:
        validate_readonly_sql(sql)
    except ValueError as exc:
        return ExplainResult(success=False, error=str(exc))

    db_type = _db_type(source)
    if db_type in ("mysql",):
        return _explain_mysql(source, sql)
    if db_type in ("oracle", "dm"):
        return ExplainResult(
            success=False,
            unsupported=True,
            dialect=db_type,
            error=(
                "Oracle / DM EXPLAIN PLAN 在 SQL Workbench v0.2 未启用 —— "
                "需要写入 PLAN_TABLE,跟「SQL 诊断」模块的完整 EXPLAIN 链路重复。"
                "请前往 SQL 诊断 使用 EXPLAIN PLAN(allow_dm_explain / "
                "allow_oracle_plan_table 已显式打开的 datasource 才允许)。"
            ),
        )
    if db_type == "db2":
        return ExplainResult(
            success=False, unsupported=True, dialect=db_type,
            error="DB2 EXPLAIN 在 SQL Workbench v0.2 未启用,请使用 SQL 诊断模块",
        )
    return ExplainResult(
        success=False, unsupported=True, dialect=db_type,
        error=f"不支持的 db_type: {db_type}",
    )


def _explain_mysql(source: Any, sql: str) -> ExplainResult:
    """MySQL EXPLAIN —— 直接前缀 EXPLAIN + fetch_rows。"""
    explain_sql_text = f"EXPLAIN {sql.rstrip(';').rstrip()}"
    start = time.perf_counter()
    try:
        result = fetch_rows_with_schema(source, explain_sql_text, max_rows=500, raise_on_overflow=False)
    except DbClientError as exc:
        return ExplainResult(
            success=False, dialect="mysql", explain_sql=explain_sql_text,
            elapsed_ms=int((time.perf_counter() - start) * 1000), error=str(exc),
        )
    except Exception as exc:  # pragma: no cover
        logger.exception("explain mysql unexpected failure")
        return ExplainResult(
            success=False, dialect="mysql", explain_sql=explain_sql_text,
            elapsed_ms=int((time.perf_counter() - start) * 1000),
            error=f"unexpected: {exc}",
        )
    elapsed = int((time.perf_counter() - start) * 1000)
    columns = list(result.columns)
    rows = [[_serialize_cell(r.get(c)) for c in columns] for r in result.rows]
    return ExplainResult(
        success=True, dialect="mysql", columns=columns, rows=rows,
        explain_sql=explain_sql_text, elapsed_ms=elapsed,
    )
