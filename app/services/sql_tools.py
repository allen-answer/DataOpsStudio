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
                columns.append(_column_label(expression))
    return _unique_strings(columns)


def _column_label(expression: Any) -> str:
    """返回 SELECT 单列在 UI 上展示的列名.

    - `t.id` -> "id" (Column 节点 .alias_or_name 是 "id")
    - `SUM(amt)` -> "SUM(amt)" (Func 没 alias 时 alias_or_name 可能返序号字符串 "6"
       甚至空,绝不能当列名;直接用 expression.sql())
    - `SUM(amt) AS total` -> "total" (Alias 节点 .alias 是 "total")
    - `CASE WHEN x THEN 1 ELSE 0 END` -> 该 CASE 表达式的 SQL 形式

    核心规则:**只有 exp.Column 和 exp.Alias 能信任 alias_or_name**,其他类型
    (Func / Case / Cast / Binary / Subquery 等) 一律 fallback 到 expression.sql()
    避免出现数字编号 / 空字符串这种迷惑列名.
    """
    from sqlglot import exp

    if isinstance(expression, exp.Alias):
        # AS alias 优先
        alias = expression.alias
        if alias:
            return alias
        return expression.sql()
    if isinstance(expression, exp.Column):
        # 普通列引用,alias_or_name 是 column name(去掉 table prefix)
        return expression.alias_or_name or expression.sql()
    # Func / Case / Cast / Math / 其他复合表达式:用原 SQL 文本
    # alias_or_name 在 sqlglot 里对 Func 等可能返序号,绝不当列名
    return expression.sql()


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
