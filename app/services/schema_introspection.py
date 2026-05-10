from __future__ import annotations

import time
from typing import Any, Protocol


class SchemaSource(Protocol):
    id: str
    db_type: Any
    database: str
    username: str
    extra: dict[str, Any]


_CACHE_TTL_SECONDS = 300
# Cache key includes (datasource_id, schema, table_filter, dialect, include_tables tuple).
# `include_tables` is derived from each lineage analysis's parsed SQL, so distinct
# scripts produce distinct keys. Long-running app + many scripts → unbounded growth.
# Cap entries with lazy GC: when over budget, drop expired first, then evict oldest
# in insertion order.
_CACHE_MAX_ENTRIES = 256
_CACHE: dict[tuple[str, str, str, str, tuple[str, ...]], tuple[float, dict[str, list[str]]]] = {}


def fetch_schema_metadata(
    source: SchemaSource,
    schema_name: str = "",
    table_filter: str = "",
    include_tables: list[str] | None = None,
    schema_dialect: str = "",
    use_cache: bool = True,
) -> dict[str, list[str]]:
    from app.dbclients.factory import fetch_rows

    include_tables = _normalize_names(include_tables or [])
    cache_key = (
        str(getattr(source, "id", "")),
        schema_name.strip(),
        table_filter.strip(),
        schema_dialect.strip().lower(),
        tuple(include_tables),
    )
    if use_cache:
        cached = _CACHE.get(cache_key)
        if cached and time.time() - cached[0] <= _CACHE_TTL_SECONDS:
            return {table: list(columns) for table, columns in cached[1].items()}

    sql = _schema_query(
        source,
        schema_name=schema_name,
        table_filter=table_filter,
        include_tables=include_tables,
        schema_dialect=schema_dialect,
    )
    rows = fetch_rows(source, sql, max_rows=None, raise_on_overflow=False)
    schema = _rows_to_schema(rows)
    if use_cache:
        _CACHE[cache_key] = (time.time(), schema)
        if len(_CACHE) > _CACHE_MAX_ENTRIES:
            _evict_cache()
    return schema


def _evict_cache() -> None:
    now = time.time()
    expired = [k for k, (ts, _) in _CACHE.items() if now - ts > _CACHE_TTL_SECONDS]
    for k in expired:
        _CACHE.pop(k, None)
    if len(_CACHE) > _CACHE_MAX_ENTRIES:
        for k in list(_CACHE.keys())[: len(_CACHE) - _CACHE_MAX_ENTRIES]:
            _CACHE.pop(k, None)


def clear_schema_cache() -> None:
    _CACHE.clear()


def _schema_query(
    source: SchemaSource,
    schema_name: str = "",
    table_filter: str = "",
    include_tables: list[str] | None = None,
    schema_dialect: str = "",
) -> str:
    db_type = _schema_dialect(source, schema_dialect)
    include_tables = _normalize_names(include_tables or [])
    if db_type in {"mysql", "ob_mysql"}:
        schema = _sql_literal(schema_name or source.database)
        filters = [f"table_schema = {schema}"]
        filters.extend(_table_filters("table_name", table_filter, include_tables))
        return (
            "select table_schema, table_name, column_name "
            "from information_schema.columns "
            f"where {' and '.join(filters)} "
            "order by table_schema, table_name, ordinal_position"
        )
    if db_type in {"oracle", "ob_oracle"}:
        owner = _sql_literal((schema_name or source.extra.get("schema") or source.username or "").upper())
        filters = [f"owner = {owner}"]
        filters.extend(_table_filters("table_name", table_filter, include_tables, uppercase=True))
        return (
            "select owner as table_schema, table_name, column_name "
            "from all_tab_columns "
            f"where {' and '.join(filters)} "
            "order by owner, table_name, column_id"
        )
    if db_type == "dm":
        owner = _sql_literal((schema_name or source.extra.get("schema") or source.database or source.username or "").upper())
        filters = [f"owner = {owner}"]
        filters.extend(_table_filters("table_name", table_filter, include_tables, uppercase=True))
        return (
            "select owner as table_schema, table_name, column_name "
            "from all_tab_columns "
            f"where {' and '.join(filters)} "
            "order by owner, table_name, column_id"
        )
    if db_type == "db2":
        schema = _sql_literal((schema_name or source.extra.get("schema") or source.username or "").upper())
        filters = [f"tabschema = {schema}"]
        filters.extend(_table_filters("tabname", table_filter, include_tables, uppercase=True))
        return (
            "select tabschema as table_schema, tabname as table_name, colname as column_name "
            "from syscat.columns "
            f"where {' and '.join(filters)} "
            "order by tabschema, tabname, colno"
        )
    raise ValueError(f"Unsupported database type: {source.db_type}")


def _rows_to_schema(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    schema: dict[str, list[str]] = {}
    seen: dict[str, set[str]] = {}
    for row in rows:
        table_schema = _row_value(row, "table_schema", "owner", "tabschema")
        table_name = _row_value(row, "table_name", "tabname")
        column_name = _row_value(row, "column_name", "colname")
        if not table_name or not column_name:
            continue
        keys = [table_name]
        if table_schema:
            keys.append(f"{table_schema}.{table_name}")
        for key in keys:
            columns = schema.setdefault(key, [])
            column_seen = seen.setdefault(key, set())
            if column_name not in column_seen:
                columns.append(column_name)
                column_seen.add(column_name)
    return schema


def _row_value(row: dict[str, Any], *names: str) -> str:
    lowered = {str(key).lower(): value for key, value in row.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value is not None:
            return str(value)
    return ""


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _schema_dialect(source: SchemaSource, override: str = "") -> str:
    value = (
        override
        or source.extra.get("schema_dialect")
        or source.extra.get("schema_mode")
        or str(getattr(source.db_type, "value", source.db_type))
    ).strip().lower()
    aliases = {
        "mysql": "mysql",
        "oracle": "oracle",
        "dm": "dm",
        "db2": "db2",
        "oceanbase": "ob_mysql",
        "ob": "ob_mysql",
        "ob_mysql": "ob_mysql",
        "oceanbase_mysql": "ob_mysql",
        "ob-oracle": "ob_oracle",
        "ob_oracle": "ob_oracle",
        "oceanbase_oracle": "ob_oracle",
    }
    return aliases.get(value, value)


def _table_filters(column: str, table_filter: str, include_tables: list[str], uppercase: bool = False) -> list[str]:
    filters: list[str] = []
    if table_filter.strip():
        value = table_filter.strip().upper() if uppercase else table_filter.strip()
        filters.append(f"{column} like {_sql_literal(value)}")
    if include_tables:
        values = [name.upper() if uppercase else name for name in include_tables]
        filters.append(f"{column} in ({', '.join(_sql_literal(value) for value in values)})")
    return filters


def _normalize_names(names: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for name in names:
        clean = name.strip().strip('"`[]')
        if "." in clean:
            clean = clean.split(".")[-1]
        key = clean.lower()
        if clean and key not in seen:
            result.append(clean)
            seen.add(key)
    return result
