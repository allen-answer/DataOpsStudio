from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import NamedTuple
from typing import Any

from app.dbclients.dialects import get_dialect
from app.dbclients.drivers import first_available_module
from app.dbclients import pool as _pool
from app.models import DataSource
from app.services.compare_schema import column_warnings, uniquify_columns


class DbClientError(RuntimeError):
    pass


logger = logging.getLogger(__name__)


class QueryRows(NamedTuple):
    rows: list[dict[str, Any]]
    columns: list[str]
    raw_columns: list[str]
    warnings: list[dict[str, Any]]


class QueryColumns(NamedTuple):
    columns: list[str]
    raw_columns: list[str]
    warnings: list[dict[str, Any]]


def fetch_rows(
    source: DataSource,
    sql: str,
    max_rows: int | None = None,
    raise_on_overflow: bool = True,
    chunk_size: int | None = None,
    progress_callback: Any | None = None,
) -> list[dict[str, Any]]:
    return fetch_rows_with_schema(
        source,
        sql,
        max_rows=max_rows,
        raise_on_overflow=raise_on_overflow,
        chunk_size=chunk_size,
        progress_callback=progress_callback,
    ).rows


def fetch_rows_with_schema(
    source: DataSource,
    sql: str,
    max_rows: int | None = None,
    raise_on_overflow: bool = True,
    chunk_size: int | None = None,
    progress_callback: Any | None = None,
) -> QueryRows:
    module_name = first_available_module(source.db_type)
    if not module_name:
        raise RuntimeError(f"{source.db_type.value} driver is not installed")

    start = time.perf_counter()
    logger.info(
        "query start datasource=%s db_type=%s host=%s port=%s sql=%s",
        source.name,
        source.db_type.value,
        source.host,
        source.port,
        _short_sql(sql),
    )
    try:
        with _pool.borrow(source, lambda: _connect(source, module_name)) as connection:
            result = _fetch_with_dbapi(connection, source.db_type, sql, max_rows, raise_on_overflow, chunk_size, progress_callback)
        logger.info(
            "query success datasource=%s db_type=%s rows=%s elapsed=%.3fs",
            source.name,
            source.db_type.value,
            len(result.rows),
            time.perf_counter() - start,
        )
        return result
    except DbClientError:
        logger.exception("query failed datasource=%s db_type=%s", source.name, source.db_type.value)
        raise
    except Exception as exc:
        logger.exception("query failed datasource=%s db_type=%s", source.name, source.db_type.value)
        raise DbClientError(f"{source.name}({source.db_type.value}) query failed: {exc}") from exc


def test_connection(source: DataSource) -> dict[str, Any]:
    start = time.perf_counter()
    sql = get_dialect(source.db_type).connection_test_sql()
    rows = fetch_rows(source, sql, max_rows=1)
    return {
        "ok": True,
        "message": "连接成功",
        "datasource": source.name,
        "db_type": source.db_type.value,
        "elapsed_seconds": round(time.perf_counter() - start, 3),
        "sample": rows[:1],
    }


def _fetch_with_dbapi(
    connection: Any,
    db_type: Any,
    sql: str,
    max_rows: int | None = None,
    raise_on_overflow: bool = True,
    chunk_size: int | None = None,
    progress_callback: Any | None = None,
) -> QueryRows:
    cursor = None
    try:
        try:
            cursor = connection.cursor()
        except Exception as exc:
            raise DbClientError(f"create cursor failed: {exc}") from exc

        _apply_statement_timeout(cursor, db_type, connection)
        try:
            cursor.execute(sql)
        except Exception as exc:
            raise DbClientError(f"execute SQL failed: {exc}; SQL={_short_sql(sql)}") from exc

        try:
            raw_columns = [desc[0] for desc in cursor.description or []]
            columns = uniquify_columns(raw_columns)
            rows = _fetch_rows_in_chunks(cursor, columns, max_rows, raise_on_overflow, chunk_size, progress_callback)
            return QueryRows(
                rows=rows,
                columns=columns,
                raw_columns=raw_columns,
                warnings=column_warnings(raw_columns, columns),
            )
        except Exception as exc:
            # ibm_db / dmPython 等 C 扩展驱动在 cursor 里调 PyErr_Set 标真错，
            # Python 看到 "returned a result with an exception set" 这种 generic
            # 描述 → 真 DB 报错（SQLCODE / SQLSTATE / errno）被吞。把 SQL +
            # 能挖到的底层状态都拼进 message，避免只看到 Python wrapper。
            detail = _extract_driver_error_detail(connection, cursor)
            raise DbClientError(
                f"fetch rows failed: {exc}; SQL={_short_sql(sql)}"
                + (f"; driver_detail={detail}" if detail else "")
            ) from exc
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
        # connection 由 pool.borrow context manager 接管 release/close


def fetch_columns(source: DataSource, sql: str) -> list[str]:
    """Return column names for `sql` without materializing rows. Uses
    `cursor.description` so empty result sets still yield the schema."""
    return fetch_column_details(source, sql).columns


def fetch_column_details(source: DataSource, sql: str) -> QueryColumns:
    """Return unique column names plus raw cursor names for diagnostics."""
    module_name = first_available_module(source.db_type)
    if not module_name:
        raise RuntimeError(f"{source.db_type.value} driver is not installed")
    with _pool.borrow(source, lambda: _connect(source, module_name)) as connection:
        cursor = None
        try:
            cursor = connection.cursor()
            _apply_statement_timeout(cursor, source.db_type, connection)
            try:
                cursor.execute(sql)
            except Exception as exc:
                raise DbClientError(f"execute SQL failed: {exc}; SQL={_short_sql(sql)}") from exc
            raw_columns = [desc[0] for desc in cursor.description or []]
            columns = uniquify_columns(raw_columns)
            return QueryColumns(
                columns=columns,
                raw_columns=raw_columns,
                warnings=column_warnings(raw_columns, columns),
            )
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:
                    pass


def iter_rows(
    source: DataSource,
    sql: str,
    max_rows: int | None = None,
    chunk_size: int | None = None,
    progress_callback: Any | None = None,
):
    module_name = first_available_module(source.db_type)
    if not module_name:
        raise RuntimeError(f"{source.db_type.value} driver is not installed")
    # iter_rows 是 generator —— 用 contextmanager 手动 enter/exit 让 yield 能跨
    # 上下文边界正确管理 connection 归还。pool.borrow 内部 except 抓 broken=True
    # 自动弃池；正常 generator close（StopIteration / GC）走 else 分支正常 release。
    cm = _pool.borrow(source, lambda: _connect(source, module_name))
    connection = cm.__enter__()
    success = False
    try:
        yield from _iter_with_dbapi(connection, source.db_type, sql, max_rows, chunk_size, progress_callback)
        success = True
    finally:
        if success:
            cm.__exit__(None, None, None)
        else:
            import sys
            cm.__exit__(*sys.exc_info())


def _connect(source: DataSource, module_name: str) -> Any:
    return get_dialect(source.db_type).connect(source, module_name)


# Phase 13: 单任务超时覆盖。runner 在 run_task 入口 push 该任务的
# query_timeout_seconds(若 limits 显式设了),fetch_rows / iter_rows 路径下查询
# 执行前由 _statement_timeout_seconds() 优先读 ContextVar,否则 fallback env。
# 用 ContextVar 而非传参,避免 fetch_rows / iter_rows / fetch_column_details
# 三个对外 API 签名都加 limits 参数。
_query_timeout_override: ContextVar[float | None] = ContextVar(
    "_query_timeout_override", default=None,
)


@contextmanager
def query_timeout_override(seconds: float | None):
    """runner 进入 task 时调:`with query_timeout_override(task.limits.query_timeout_seconds): ...`。

    None / 负值 → 不设覆盖(等同于走 env 默认);0 → 显式关闭超时;>0 → 用该值。
    """
    token = _query_timeout_override.set(seconds)
    try:
        yield
    finally:
        _query_timeout_override.reset(token)


def _statement_timeout_seconds() -> float:
    """语句超时秒数。优先 ContextVar(单任务覆盖);否则 env
    `DATAOPS_DB_STATEMENT_TIMEOUT_SECONDS`,默认 900(15 分钟);`<= 0` 关闭。
    每次查询读一遍 —— 改 env 后无需重启即生效。"""
    override = _query_timeout_override.get()
    if override is not None:
        return float(override) if override > 0 else 0.0
    try:
        value = float(os.getenv("DATAOPS_DB_STATEMENT_TIMEOUT_SECONDS", "900"))
    except (TypeError, ValueError):
        return 900.0
    return value if value > 0 else 0.0


def _apply_statement_timeout(cursor: Any, db_type: Any, connection: Any = None) -> None:
    """查询执行前 best-effort 下发会话语句超时 —— 防慢查询长期占住 DB 连接。

    两路径:
    - **连接属性路径**(Oracle / DM via oracledb / dmPython `conn.callTimeout`)
      作用于该连接所有后续 round-trip
    - **会话 SQL 路径**(MySQL `SET SESSION MAX_EXECUTION_TIME`)cursor 级下发

    优先试连接属性,失败 / 不支持时 fallback 到 SQL 路径。失败只记 warning,
    绝不让真查询陪葬:MariaDB / 老版本 / 不支持的方言下发会报错,超时是安全
    网而非查询的前置条件,吞掉即可。

    `connection=None` 时直接走 SQL 路径(向后兼容旧 caller / 单测路径)。
    """
    seconds = _statement_timeout_seconds()
    if seconds <= 0:
        return
    try:
        dialect = get_dialect(db_type)
    except Exception:  # noqa: BLE001 —— 取不到 dialect 不该影响查询
        return
    # Path A: 连接属性(Oracle / DM)
    if connection is not None:
        try:
            if dialect.apply_call_timeout(connection, seconds):
                return
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "apply call timeout failed (db_type=%s): %s",
                getattr(db_type, "value", db_type), exc,
            )
    # Path B: 会话 SQL(MySQL)
    try:
        timeout_sql = dialect.statement_timeout_sql(seconds)
    except Exception:  # noqa: BLE001
        return
    if not timeout_sql:
        return
    try:
        cursor.execute(timeout_sql)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "set statement timeout failed (db_type=%s): %s",
            getattr(db_type, "value", db_type), exc,
        )


def _iter_with_dbapi(
    connection: Any,
    db_type: Any,
    sql: str,
    max_rows: int | None = None,
    chunk_size: int | None = None,
    progress_callback: Any | None = None,
):
    cursor = None
    fetched = 0
    try:
        cursor = connection.cursor()
        _apply_statement_timeout(cursor, db_type, connection)
        cursor.execute(sql)
        columns = uniquify_columns([desc[0] for desc in cursor.description or []])
        batch_size = max(1, int(chunk_size or 5000))
        while True:
            remaining = None if max_rows is None else max_rows - fetched
            if remaining is not None and remaining <= 0:
                break
            batch = cursor.fetchmany(batch_size if remaining is None else min(batch_size, remaining))
            if not batch:
                break
            fetched += len(batch)
            if progress_callback is not None:
                progress_callback(fetched)
            for row in batch:
                yield dict(zip(columns, row, strict=False))
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
        # connection 由 pool.borrow 接管 release/close


def _short_sql(sql: str) -> str:
    compact = " ".join(sql.split())
    return compact[:500] + ("..." if len(compact) > 500 else "")


def _extract_driver_error_detail(connection: Any, cursor: Any) -> str:
    """Pull driver-native error info that the generic Python wrapper hides.

    ibm_db cursor 在出错时设了 PyErr 标记位，但 Python 层只看到
    "fetchmany returned a result with an exception set" 这种 generic 描述。
    真正的 SQLCODE / SQLSTATE / errno 必须从 driver 对象里挖。各驱动接口不
    一致：ibm_db 走 `ibm_db.stmt_errormsg()` / `ibm_db.conn_errormsg()`、
    pymysql 走 `connection.show_warnings()`、dmPython 走 `cursor.errno` /
    `cursor.errmsg`。这里 best-effort 探测，拿不到就返回空串，不影响主路径。
    """
    parts: list[str] = []
    # ibm_db 风格：errorcode / errormessage 属性
    for obj_name, obj in (("cursor", cursor), ("connection", connection)):
        for attr in ("errorcode", "errno", "error_code"):
            try:
                val = getattr(obj, attr, None)
                if val and val != 0:
                    parts.append(f"{obj_name}.{attr}={val}")
            except Exception:
                pass
        for attr in ("errormessage", "errmsg", "error_message"):
            try:
                val = getattr(obj, attr, None)
                if val:
                    parts.append(f"{obj_name}.{attr}={val}")
            except Exception:
                pass
    # ibm_db 模块级 helper（如果驱动是 ibm_db）
    try:
        import ibm_db  # type: ignore
        try:
            stmt_msg = ibm_db.stmt_errormsg()
            if stmt_msg:
                parts.append(f"ibm_db.stmt_errormsg={stmt_msg}")
        except Exception:
            pass
        try:
            conn_msg = ibm_db.conn_errormsg()
            if conn_msg:
                parts.append(f"ibm_db.conn_errormsg={conn_msg}")
        except Exception:
            pass
    except ImportError:
        pass
    return " | ".join(parts)


def _fetch_rows_in_chunks(
    cursor: Any,
    columns: list[str],
    max_rows: int | None,
    raise_on_overflow: bool,
    chunk_size: int | None,
    progress_callback: Any | None,
) -> list[dict[str, Any]]:
    if max_rows is None and not chunk_size:
        rows = cursor.fetchall()
        return [dict(zip(columns, row, strict=False)) for row in rows]

    batch_size = max(1, int(chunk_size or max_rows or 5000))
    limit = max_rows + 1 if max_rows is not None and raise_on_overflow else max_rows
    result: list[dict[str, Any]] = []
    while True:
        remaining = None if limit is None else limit - len(result)
        if remaining is not None and remaining <= 0:
            break
        fetch_size = batch_size if remaining is None else min(batch_size, remaining)
        batch = cursor.fetchmany(fetch_size)
        if not batch:
            break
        result.extend(dict(zip(columns, row, strict=False)) for row in batch)
        if progress_callback is not None:
            progress_callback(len(result))
    if max_rows is not None and raise_on_overflow and len(result) > max_rows:
        raise DbClientError(f"query returned more than max_rows={max_rows}")
    return result[:max_rows] if max_rows is not None and not raise_on_overflow else result
