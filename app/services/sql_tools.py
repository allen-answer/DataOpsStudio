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
    """提取 SELECT 输出列名 — 没 alias 的复合表达式自动起短别名(防冲突).

    Args:
        sql: 单条或多条 SELECT
        dialect: sqlglot 方言

    Returns:
        每个 SELECT 列对应一个展示用列名. 优先级:
          1. AS alias -> 用 alias
          2. 普通列 t.id -> "id"
          3. SUM(amt) / COUNT(*) / AVG(x) -> 推 "sum_amt" / "count_all" / "avg_x"
          4. CASE / 算术 / 复杂表达式 -> "expr_1" / "expr_2"
          冲突时尾部加 _2 _3 (case-insensitive 比对)
    """
    import sqlglot
    from sqlglot import exp

    statements = sqlglot.parse(sql, read=dialect or None)
    taken: set[str] = set()  # case-insensitive 已用名集合
    expr_seq = 0  # 给 CASE / 复杂表达式生成 expr_N 时的序号
    columns: list[str] = []
    for statement in statements:
        if statement is None:
            continue
        for select in statement.find_all(exp.Select):
            for expression in select.expressions:
                expr_seq += 1
                columns.append(_column_label(expression, taken, expr_seq))
    return columns


def _column_label(expression: Any, taken: set[str], expr_seq: int) -> str:
    """返回该 SELECT 表达式在 UI / column_mappings 里用的列名,**保证不重复**.

    Args:
        expression: sqlglot SELECT 单项
        taken: in/out — 已用列名集合(case-insensitive),返回值会被加进去
        expr_seq: 该列在 SELECT 中的位置(1-indexed),给 expr_N fallback 用

    规则:
      - exp.Alias -> 用 alias 字符串(信任用户写的)
      - exp.Column -> 用 column name(信任 alias_or_name,但拒纯数字)
      - exp.Sum/Count/Avg/Min/Max -> 推 "sum_amt" / "count_all" 类语义化短名
      - exp.Case/Cast/Binary/Subquery/其他 -> 用 "expr_{N}" 占位
      - 任何冲突 -> 尾部加 _2 _3 直到不冲突
    """
    from sqlglot import exp

    # SELECT * / t.* — 保留原样,不当列名处理(前端会单独 filter * 走列展开路径)
    if isinstance(expression, exp.Star):
        taken.add("*")
        return "*"

    raw: str = ""
    if isinstance(expression, exp.Alias):
        alias = expression.alias
        if alias:
            raw = alias
    elif isinstance(expression, exp.Column):
        name = expression.alias_or_name
        # sqlglot 偶尔对纯数字列名返序号 — 拒掉走 fallback
        if name and not name.isdigit():
            raw = name

    if not raw:
        raw = _suggest_alias(expression, expr_seq)

    return _make_unique(raw, taken)


def _suggest_alias(expression: Any, expr_seq: int) -> str:
    """基于表达式类型推 short alias. 不保证唯一,由 _make_unique 兜底."""
    from sqlglot import exp

    # 常见聚合函数:取第一个参数的列名 拼前缀
    aggregate_prefixes = {
        exp.Sum: "sum",
        exp.Count: "count",
        exp.Avg: "avg",
        exp.Min: "min",
        exp.Max: "max",
    }
    for cls, prefix in aggregate_prefixes.items():
        if isinstance(expression, cls):
            inner = expression.this
            if isinstance(inner, exp.Star):
                return f"{prefix}_all"
            if isinstance(inner, exp.Column):
                col_name = inner.alias_or_name
                if col_name and not col_name.isdigit():
                    return f"{prefix}_{col_name}".lower()
            return f"{prefix}_expr_{expr_seq}"

    # 通用 Func: 取 func name + 第一个 column 参数
    if isinstance(expression, exp.Func):
        try:
            func_name = expression.sql_name().lower()
        except Exception:
            func_name = "func"
        # 长函数名截断防 UI 撑爆
        if len(func_name) > 16:
            func_name = func_name[:16]
        inner = expression.this if hasattr(expression, "this") else None
        if isinstance(inner, exp.Column):
            col_name = inner.alias_or_name
            if col_name and not col_name.isdigit():
                return f"{func_name}_{col_name}".lower()
        return f"{func_name}_{expr_seq}"

    # CASE / Cast / Binary 数学 / Subquery 等 — 用 expr_N 占位
    return f"expr_{expr_seq}"


def _make_unique(base: str, taken: set[str]) -> str:
    """如果 base(case-insensitive) 已被占用,尾部加 _2 _3 …. 加完 taken 同步更新."""
    candidate = base
    norm = candidate.lower()
    if norm not in taken:
        taken.add(norm)
        return candidate
    suffix = 2
    while f"{base}_{suffix}".lower() in taken:
        suffix += 1
    candidate = f"{base}_{suffix}"
    taken.add(candidate.lower())
    return candidate


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
