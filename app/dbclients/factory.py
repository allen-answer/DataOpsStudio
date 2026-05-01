from __future__ import annotations

import importlib
import logging
import time
from typing import Any

from app.dbclients.drivers import add_db2_dll_directories, first_available_module
from app.models import DataSource, DatabaseType


class DbClientError(RuntimeError):
    pass


logger = logging.getLogger(__name__)
CONNECT_TIMEOUT_SECONDS = 10
QUERY_TIMEOUT_SECONDS = 300


def fetch_rows(
    source: DataSource,
    sql: str,
    max_rows: int | None = None,
    raise_on_overflow: bool = True,
    chunk_size: int | None = None,
    progress_callback: Any | None = None,
) -> list[dict[str, Any]]:
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
        connection = _connect(source, module_name)
        rows = _fetch_with_dbapi(connection, sql, max_rows, raise_on_overflow, chunk_size, progress_callback)
        logger.info(
            "query success datasource=%s db_type=%s rows=%s elapsed=%.3fs",
            source.name,
            source.db_type.value,
            len(rows),
            time.perf_counter() - start,
        )
        return rows
    except DbClientError:
        logger.exception("query failed datasource=%s db_type=%s", source.name, source.db_type.value)
        raise
    except Exception as exc:
        logger.exception("query failed datasource=%s db_type=%s", source.name, source.db_type.value)
        raise DbClientError(f"{source.name}({source.db_type.value}) query failed: {exc}") from exc


def test_connection(source: DataSource) -> dict[str, Any]:
    start = time.perf_counter()
    sql = _connection_test_sql(source.db_type)
    rows = fetch_rows(source, sql, max_rows=1)
    return {
        "ok": True,
        "message": "连接成功",
        "datasource": source.name,
        "db_type": source.db_type.value,
        "elapsed_seconds": round(time.perf_counter() - start, 3),
        "sample": rows[:1],
    }


def _connection_test_sql(db_type: DatabaseType) -> str:
    if db_type in {DatabaseType.ORACLE, DatabaseType.DM}:
        return "select 1 as ok from dual"
    if db_type == DatabaseType.DB2:
        return "select 1 as ok from sysibm.sysdummy1"
    return "select 1 as ok"


def _fetch_with_dbapi(
    connection: Any,
    sql: str,
    max_rows: int | None = None,
    raise_on_overflow: bool = True,
    chunk_size: int | None = None,
    progress_callback: Any | None = None,
) -> list[dict[str, Any]]:
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
            columns = [desc[0] for desc in cursor.description or []]
            rows = _fetch_rows_in_chunks(cursor, columns, max_rows, raise_on_overflow, chunk_size, progress_callback)
            return rows
        except Exception as exc:
            raise DbClientError(f"fetch rows failed: {exc}") from exc
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
        try:
            connection.close()
        except Exception:
            pass


def fetch_columns(source: DataSource, sql: str) -> list[str]:
    """Return column names for `sql` without materializing rows. Uses
    `cursor.description` so empty result sets still yield the schema."""
    module_name = first_available_module(source.db_type)
    if not module_name:
        raise RuntimeError(f"{source.db_type.value} driver is not installed")
    connection = _connect(source, module_name)
    cursor = None
    try:
        cursor = connection.cursor()
        try:
            cursor.execute(sql)
        except Exception as exc:
            raise DbClientError(f"execute SQL failed: {exc}; SQL={_short_sql(sql)}") from exc
        return [desc[0] for desc in cursor.description or []]
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
        try:
            connection.close()
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
    connection = _connect(source, module_name)
    yield from _iter_with_dbapi(connection, sql, max_rows, chunk_size, progress_callback)


def _connect(source: DataSource, module_name: str) -> Any:
    if source.db_type == DatabaseType.MYSQL:
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
    if source.db_type == DatabaseType.ORACLE:
        driver = importlib.import_module(module_name)
        dsn = source.extra.get("dsn") or f"{source.host}:{source.port}/{source.database}"
        options = {}
        if "tcp_connect_timeout" in source.extra:
            options["tcp_connect_timeout"] = source.extra["tcp_connect_timeout"]
        return driver.connect(user=source.username, password=source.password, dsn=dsn, **options)
    if source.db_type == DatabaseType.DB2:
        add_db2_dll_directories()
        if module_name == "ibm_db_dbi":
            driver = importlib.import_module(module_name)
        else:
            import ibm_db_dbi as driver
        conn_str = source.extra.get("conn_str") or (
            f"DATABASE={source.database};HOSTNAME={source.host};PORT={source.port};"
            f"PROTOCOL=TCPIP;UID={source.username};PWD={source.password};"
        )
        if "CONNECTTIMEOUT" not in conn_str.upper():
            conn_str += f"CONNECTTIMEOUT={int(source.extra.get('connect_timeout', CONNECT_TIMEOUT_SECONDS))};"
        return driver.connect(conn_str, "", "")
    if source.db_type == DatabaseType.DM:
        driver = importlib.import_module(module_name)
        options = dict(source.extra)
        if source.database and "schema" not in options:
            options["schema"] = source.database
        options.setdefault("login_timeout", CONNECT_TIMEOUT_SECONDS)
        try:
            return driver.connect(
                user=source.username,
                password=source.password,
                server=source.host,
                port=source.port,
                **options,
            )
        except Exception:
            return driver.connect(source.username, source.password, f"{source.host}:{source.port}", **options)
    raise RuntimeError(f"Unsupported database type: {source.db_type}")


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
        columns = [desc[0] for desc in cursor.description or []]
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
        try:
            connection.close()
        except Exception:
            pass


def _short_sql(sql: str) -> str:
    compact = " ".join(sql.split())
    return compact[:500] + ("..." if len(compact) > 500 else "")


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
