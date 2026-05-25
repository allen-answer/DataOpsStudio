"""SQL Workbench 执行核心 —— 把一条 SELECT 跑出来变 ExecuteResponse。

约束:
- sql_guard.validate_readonly_sql 拦 DML / DDL / 多语句 / SELECT FOR UPDATE
- max_rows 服务端再 clamp(模型 ge=1 le=10000),拉 max_rows + 1 检测 truncated
- 任意阶段失败统一返 ExecuteResponse(success=False),不向 caller 抛 —— 这样 API
  层就不用包多个 try/except,跟 slow_sql.analyze_sql 的口径一致
"""
from __future__ import annotations

import logging
import time
from typing import Any

from app.dbclients.factory import DbClientError, fetch_rows_with_schema
from app.models import DataSource
from app.sqlide.models import ExecuteResponse
from app.utils.sql_guard import validate_readonly_sql


logger = logging.getLogger(__name__)


class SqlWorkbenchError(RuntimeError):
    """显式领域错(校验失败 / 驱动错)。executor 内部 catch 后转 ExecuteResponse,
    但 API 层在 datasource lookup / authz 等步骤仍可能抛此类。"""


# 服务端硬上限:即使 caller 传更大也截。防绕过前端 cap。
_MAX_ROWS_HARD_CAP = 10_000


def execute_sql(
    source: DataSource,
    sql: str,
    *,
    max_rows: int = 1000,
) -> ExecuteResponse:
    """Run SELECT/WITH SQL against the given datasource.

    返回 ExecuteResponse 统一 envelope。任何错都落 error 字段不向外抛。
    """
    max_rows = max(1, min(int(max_rows or 1000), _MAX_ROWS_HARD_CAP))

    # 1) 校验 SQL(白名单)
    try:
        validate_readonly_sql(sql)
    except ValueError as exc:
        return ExecuteResponse(success=False, error=str(exc))

    # 2) 拉数据(拉 max_rows + 1 检测 truncated)
    start = time.perf_counter()
    try:
        result = fetch_rows_with_schema(
            source,
            sql,
            max_rows=max_rows + 1,
            raise_on_overflow=False,
        )
    except DbClientError as exc:
        elapsed = int((time.perf_counter() - start) * 1000)
        return ExecuteResponse(success=False, elapsed_ms=elapsed, error=str(exc))
    except Exception as exc:  # pragma: no cover —— 兜底,任何未预期错都不让前端 500
        elapsed = int((time.perf_counter() - start) * 1000)
        logger.exception("sql workbench execute unexpected failure")
        return ExecuteResponse(success=False, elapsed_ms=elapsed, error=f"unexpected: {exc}")

    elapsed = int((time.perf_counter() - start) * 1000)

    # 3) 截断 + 列序对齐
    rows_raw = result.rows
    truncated = len(rows_raw) > max_rows
    if truncated:
        rows_raw = rows_raw[:max_rows]

    columns = list(result.columns)
    rows: list[list[Any]] = [
        [_serialize_cell(row.get(col)) for col in columns]
        for row in rows_raw
    ]

    return ExecuteResponse(
        success=True,
        columns=columns,
        rows=rows,
        row_count=len(rows),
        elapsed_ms=elapsed,
        truncated=truncated,
    )


def _serialize_cell(value: Any) -> Any:
    """JSON-safe 单元格序列化。datetime / Decimal / bytes 等转字符串。

    前端 result grid 只接 JSON 原生类型,这里做最小的归一化。
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    # datetime / date / time → ISO 字符串
    try:
        from datetime import date, datetime, time as _time
        if isinstance(value, (datetime, date, _time)):
            return value.isoformat()
    except Exception:  # pragma: no cover
        pass
    # Decimal → float(精度损失;Phase 1 接受,后续可考虑加 cell_meta 表达类型)
    try:
        from decimal import Decimal
        if isinstance(value, Decimal):
            return float(value)
    except Exception:  # pragma: no cover
        pass
    # bytes → hex 字符串(防 JSON 序列化失败)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    # 兜底 str()
    return str(value)
