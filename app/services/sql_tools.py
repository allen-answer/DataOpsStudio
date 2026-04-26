from __future__ import annotations

from typing import Any

from app.lineage.analyzer import analyze_sql_lineage
from app.utils.sql_guard import validate_readonly_sql


def sql_assist(sql: str, dialect: str | None = None, target_dialect: str | None = None) -> dict[str, Any]:
    if not sql.strip():
        raise ValueError("sql is required")
    readonly_error = ""
    try:
        validate_readonly_sql(sql)
    except Exception as exc:
        readonly_error = str(exc)

    formatted = _format_sql(sql, dialect)
    converted = _convert_sql(sql, dialect, target_dialect) if target_dialect else ""
    lineage = analyze_sql_lineage(sql, dialect)
    output_columns = _extract_output_columns(sql, dialect)
    if not output_columns:
        output_columns = _unique_strings(item["output_column"] for item in lineage["columns"])
    return {
        "readonly_ok": not readonly_error,
        "readonly_error": readonly_error,
        "formatted_sql": formatted,
        "converted_sql": converted,
        "output_columns": output_columns,
        "key_candidates": _key_candidates(output_columns),
    }


def _format_sql(sql: str, dialect: str | None) -> str:
    import sqlglot

    return ";\n".join(statement.sql(pretty=True) for statement in sqlglot.parse(sql, read=dialect or None))


def _convert_sql(sql: str, dialect: str | None, target_dialect: str | None) -> str:
    import sqlglot

    return ";\n".join(sqlglot.transpile(sql, read=dialect or None, write=target_dialect or None, pretty=True))


def _extract_output_columns(sql: str, dialect: str | None) -> list[str]:
    import sqlglot
    from sqlglot import exp

    statements = sqlglot.parse(sql, read=dialect or None)
    columns: list[str] = []
    for statement in statements:
        if statement is None:
            continue
        for select in statement.find_all(exp.Select):
            for expression in select.expressions:
                columns.append(expression.alias_or_name or expression.sql())
    return _unique_strings(columns)


def _key_candidates(columns: list[str]) -> list[str]:
    preferred = []
    for column in columns:
        normalized = _normalize_column_name(column)
        if (
            normalized in {"id", "uuid", "no", "code", "cd"}
            or normalized.endswith(("_id", "_no", "_num", "_code", "_cd"))
            or "num" in normalized
        ):
            preferred.append(column)
    return preferred[:20]


def _normalize_column_name(column: str) -> str:
    return column.strip().strip('"`[]').split(".")[-1].lower()


def _unique_strings(items: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result
