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
from app.sqlide.limit_injector import inject_limit, needs_injection
from app.sqlide.models import ExecuteResponse
from app.utils.sql_guard import validate_readonly_sql


logger = logging.getLogger(__name__)


class SqlWorkbenchError(RuntimeError):
    """显式领域错(校验失败 / 驱动错)。executor 内部 catch 后转 ExecuteResponse,
    但 API 层在 datasource lookup / authz 等步骤仍可能抛此类。"""


# 服务端硬上限:即使 caller 传更大也截。防绕过前端 cap。
_MAX_ROWS_HARD_CAP = 10_000

# Cell 级 + 整体内存防护(防 OOM)
# 用户报告:在 SQL 控制台查询数据直接导致内存崩。根因是大 BLOB / CLOB cell
# 序列化时 `str(value)` 把整个 LOB(可能几 MB / 几十 MB)拉进内存;1000 行
# × 几 MB cell = 几 GB,容器 OOM。
_MAX_CELL_BYTES = 64 * 1024              # 单 cell 截到 64KB(LOB / 长 text)
_MAX_TOTAL_BYTES = 64 * 1024 * 1024      # 整个结果 64MB 硬上限,超即提前终止
_CELL_TRUNC_SUFFIX = "...[CELL_TRUNCATED]"


def execute_sql(
    source: DataSource,
    sql: str,
    *,
    max_rows: int = 1000,
    _execution: Any = None,
) -> ExecuteResponse:
    """Run SELECT/WITH SQL against the given datasource.

    返回 ExecuteResponse 统一 envelope。任何错都落 error 字段不向外抛。

    `_execution`:可选 Execution 对象,runtime 层注入。当前 v0.5 仅占位 —— 后续
    用来在 borrow conn 后写 exe.connection_id(MySQL KILL QUERY 用)。现在保持
    None,driver-level KILL 跳过(KILL 是 stub),完成后 cancel_requested check
    + 丢弃结果是主路径。

    **内存防护**(用户报 OOM 后加):
    - 单 cell 超 64KB 截断 + 加 "[CELL_TRUNCATED]" 标记(LOB / 大 text 防御)
    - 整个结果累计 > 64MB 时提前结束,result.truncated=True + error 字段说明
    - 防御性:即便 fetch 拿到 1000 行,序列化阶段也不会让单行 / 总量爆内存
    """
    max_rows = max(1, min(int(max_rows or 1000), _MAX_ROWS_HARD_CAP))

    # 1) 校验 SQL(白名单)
    try:
        validate_readonly_sql(sql)
    except ValueError as exc:
        return ExecuteResponse(success=False, error=str(exc))

    # 2) Preview 路径强制注入 LIMIT —— pymysql 默认 buffered cursor 在 execute
    # 那一刻就把整个结果集拉到 client 内存,`max_rows + 1` 截断根本来不及触发。
    # 在 SQL 出门前注入 LIMIT 让 server 端只返 max_rows + 1 行(多 1 行检测
    # truncated),client 内存恒定 O(max_rows × 单行)。
    # 已带 LIMIT/FETCH FIRST/ROWNUM 的 SQL 不动 —— 尊重用户显式意图。
    db_type_str = source.db_type.value if hasattr(source.db_type, "value") else str(source.db_type)
    effective_sql = sql
    limit_injected = False
    if needs_injection(sql, max_rows=max_rows + 1):
        effective_sql = inject_limit(sql, max_rows=max_rows + 1, db_type=db_type_str)
        limit_injected = True
        logger.info("preview limit injected max_rows=%d db_type=%s", max_rows, db_type_str)

    # 3) 拉数据(拉 max_rows + 1 检测 truncated)
    start = time.perf_counter()
    try:
        result = fetch_rows_with_schema(
            source,
            effective_sql,
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

    # 3) 截断 + 列序对齐 + 内存防护
    rows_raw = result.rows
    truncated = len(rows_raw) > max_rows
    if truncated:
        rows_raw = rows_raw[:max_rows]

    columns = list(result.columns)
    rows: list[list[Any]] = []
    total_bytes = 0
    memory_truncated = False
    for row in rows_raw:
        serialized_row: list[Any] = []
        row_bytes = 0
        for col in columns:
            cell = _serialize_cell(row.get(col))
            # 单 cell 截断到 _MAX_CELL_BYTES(对 str / bytes-hex / 大对象 fallback)
            if isinstance(cell, str) and len(cell) > _MAX_CELL_BYTES:
                cell = cell[:_MAX_CELL_BYTES] + _CELL_TRUNC_SUFFIX
            serialized_row.append(cell)
            # 粗算字节(str.len ≈ bytes 数,中文双倍但足够保守)
            if isinstance(cell, str):
                row_bytes += len(cell)
            else:
                row_bytes += 16  # 数字 / None / bool 固定开销
        rows.append(serialized_row)
        total_bytes += row_bytes
        if total_bytes > _MAX_TOTAL_BYTES:
            memory_truncated = True
            logger.warning(
                "sql workbench execute memory cap hit: %d rows serialized, "
                "total_bytes=%d > cap=%d, dropping rest",
                len(rows), total_bytes, _MAX_TOTAL_BYTES,
            )
            break

    if memory_truncated:
        truncated = True

    return ExecuteResponse(
        success=True,
        columns=columns,
        rows=rows,
        row_count=len(rows),
        elapsed_ms=elapsed,
        truncated=truncated,
        error=(
            f"memory cap reached: result truncated at {len(rows)} rows / "
            f"~{total_bytes // (1024*1024)}MB (cap {_MAX_TOTAL_BYTES // (1024*1024)}MB); "
            f"add LIMIT or SELECT fewer columns"
            if memory_truncated else None
        ),
    )


def _serialize_cell(value: Any) -> Any:
    """JSON-safe 单元格序列化。datetime / Decimal / bytes 等转字符串。

    前端 result grid 只接 JSON 原生类型,这里做最小的归一化。

    **注意**:本函数**不**做 size 截断,size 控制在 caller 层(execute_sql)。
    这里只关心"把 driver 返回的 native 类型转 JSON-safe"。
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
    # 大 BLOB cap:避免 GB 级 BLOB 把 .hex() 直接拉进内存
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        if len(raw) > _MAX_CELL_BYTES:
            return raw[:_MAX_CELL_BYTES].hex() + _CELL_TRUNC_SUFFIX
        return raw.hex()
    # Oracle / DM CLOB-like 对象:有 .read() 方法的 LOB,先读 size 决定要不要 .read()
    # 防止 str(lob) 触发隐式 .read() 把整个 CLOB 拉到内存
    if hasattr(value, "read") and callable(value.read) and hasattr(value, "size") and callable(value.size):
        try:
            sz = value.size()
            if sz > _MAX_CELL_BYTES:
                head = value.read(1, _MAX_CELL_BYTES) if _looks_oracledb_lob(value) else value.read(_MAX_CELL_BYTES)
                return str(head) + _CELL_TRUNC_SUFFIX
            return str(value.read())
        except Exception:
            pass
    # 兜底 str() — 注意大对象在这里可能炸,但已经在前几个分支拦住了 LOB / bytes
    s = str(value)
    return s


def _looks_oracledb_lob(value: Any) -> bool:
    """oracledb / cx_Oracle LOB.read(offset, length) 是 1-based offset。"""
    return type(value).__module__ in ("oracledb", "cx_Oracle")
