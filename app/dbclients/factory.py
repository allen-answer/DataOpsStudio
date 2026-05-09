from __future__ import annotations

import logging
import time
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
            result = _fetch_with_dbapi(connection, sql, max_rows, raise_on_overflow, chunk_size, progress_callback)
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
        yield from _iter_with_dbapi(connection, sql, max_rows, chunk_size, progress_callback)
        success = True
    finally:
        if success:
            cm.__exit__(None, None, None)
        else:
            import sys
            cm.__exit__(*sys.exc_info())


def _connect(source: DataSource, module_name: str) -> Any:
    return get_dialect(source.db_type).connect(source, module_name)


def _iter_with_dbapi(
    connection: Any,
    sql: str,
    max_rows: int | None = None,
    chunk_size: int | None = None,
    progress_callback: Any | None = None,
):
    cursor = None
    fetched = 0
    try:
        cursor = connection.cursor()
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
