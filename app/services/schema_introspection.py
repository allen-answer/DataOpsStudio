from __future__ import annotations

from typing import Any, Protocol

class SchemaSource(Protocol):
    db_type: Any
    database: str
    username: str
    extra: dict[str, Any]


def fetch_schema_metadata(source: SchemaSource) -> dict[str, list[str]]:
    from app.dbclients.factory import fetch_rows

    sql = _schema_query(source)
    rows = fetch_rows(source, sql, max_rows=None, raise_on_overflow=False)
    return _rows_to_schema(rows)


def _schema_query(source: SchemaSource) -> str:
    db_type = str(getattr(source.db_type, "value", source.db_type))
    if db_type == "MySQL":
        schema = _sql_literal(source.database)
        return (
            "select table_schema, table_name, column_name "
            "from information_schema.columns "
            f"where table_schema = {schema} "
            "order by table_schema, table_name, ordinal_position"
        )
    if db_type == "Oracle":
        owner = _sql_literal((source.extra.get("schema") or source.username or "").upper())
        return (
            "select owner as table_schema, table_name, column_name "
            "from all_tab_columns "
            f"where owner = {owner} "
            "order by owner, table_name, column_id"
        )
    if db_type == "DM":
        owner = _sql_literal((source.extra.get("schema") or source.database or source.username or "").upper())
        return (
            "select owner as table_schema, table_name, column_name "
            "from all_tab_columns "
            f"where owner = {owner} "
            "order by owner, table_name, column_id"
        )
    if db_type == "DB2":
        schema = _sql_literal((source.extra.get("schema") or source.username or "").upper())
        return (
            "select tabschema as table_schema, tabname as table_name, colname as column_name "
            "from syscat.columns "
            f"where tabschema = {schema} "
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
