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

    # 自动给无 alias 的复合表达式注入短别名 — UI 显示用 labels,SQL 跑也用
    # rewritten_sql 让 DB cursor.description 跟列名对得上(避免 OceanBase 返 6/7/8)
    rewritten_sql, output_columns, alias_injected = rewrite_sql_inject_aliases(sql, dialect)
    if not output_columns:
        output_columns = _unique_strings(item["output_column"] for item in lineage["columns"])
        rewritten_sql = sql
        alias_injected = False
    return {
        "readonly_ok": not readonly_error,
        "readonly_error": readonly_error,
        "formatted_sql": formatted,
        "converted_sql": converted,
        "output_columns": output_columns,
        "key_candidates": _key_candidates(output_columns),
        # 新增:**自动注入 alias 后的 SQL**(同语义,但聚合/CASE 等都加了 AS)
        # 前端可弹"应用建议"按钮,让用户用 rewritten_sql 替换原 sql
        # alias_injected=False 时 rewritten_sql 等于原 sql,前端不需要弹提示
        "rewritten_sql": rewritten_sql,
        "alias_injected": alias_injected,
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


def rewrite_sql_inject_aliases(
    sql: str,
    dialect: str | None = None,
) -> tuple[str, list[str], bool]:
    """**核心 API**:自动给 SELECT 里没 alias 的复合表达式注入短 AS alias.

    用法:
      rewritten, labels, changed = rewrite_sql_inject_aliases(
          "SELECT id, SUM(amt) FROM t GROUP BY id"
      )
      # rewritten = "SELECT id, SUM(amt) AS sum_amt FROM t GROUP BY id"
      # labels    = ["id", "sum_amt"]
      # changed   = True  (SQL 实际有改动)

    规则:
      - exp.Alias / exp.Column / exp.Star -> 不改(已有 alias / 普通列 / SELECT *)
      - 其他复合表达式 -> 注入 AS <auto_alias>
      - auto_alias 沿用 _column_label 规则 (sum_amt / count_all / case_N / expr_N)
      - 防冲突: case-insensitive 跟同 SELECT 已有列名 / 之前生成的别名比对,冲突加 _2 _3

    嵌套 SELECT (CTE 内层 / Subquery / UNION 各分支) **都会**被改写 -
    sqlglot.find_all(exp.Select) 会遍历所有 Select 节点.

    parse 失败 -> 直接返原 SQL,changed=False (不让改写挂掉用户原本能跑的 SQL).

    Returns:
        (rewritten_sql, top_select_labels, changed)
        labels 仅含顶层 SELECT 的列名(给 column_mappings / 字段映射 UI 用),
        changed 标识 SQL 是否真的有改动(UI 可据此提示用户).
    """
    import sqlglot
    from sqlglot import exp

    try:
        statements = sqlglot.parse(sql, read=dialect or None)
    except Exception:
        # parse 不了的 SQL — 返原值 + 空 labels,不挂
        return sql, [], False

    top_labels: list[str] = []
    changed = False

    for statement in statements:
        if statement is None:
            continue
        first_select_in_stmt = True
        for select in statement.find_all(exp.Select):
            taken: set[str] = set()  # 每个 SELECT 独立 taken,内外层别名不互串
            expr_seq = 0
            new_expressions = []
            for expression in select.expressions:
                expr_seq += 1
                label = _column_label(expression, taken, expr_seq)
                if first_select_in_stmt:
                    top_labels.append(label)
                # 决定要不要注入 alias
                if _needs_alias_injection(expression):
                    new_expressions.append(exp.alias_(expression.copy(), label))
                    changed = True
                else:
                    new_expressions.append(expression)
            select.set("expressions", new_expressions)
            first_select_in_stmt = False

    if not changed:
        return sql, top_labels, False

    try:
        rewritten = ";\n".join(
            stmt.sql(dialect=dialect or None, pretty=False)
            for stmt in statements
            if stmt is not None
        )
        return rewritten, top_labels, True
    except Exception:
        # serialize 失败兜底 — 返原 SQL,不让用户 SQL 跑不了
        return sql, top_labels, False


def _needs_alias_injection(expression: Any) -> bool:
    """判断该 SELECT 单项是否需要注入 alias.

    - exp.Alias  -> 已有 alias,不动
    - exp.Column -> 普通列 t.id,DB 直接返列名,不需要 alias
    - exp.Star   -> SELECT * / t.*,展开后是真列名,不动
    - 其它(Func/Case/Cast/Binary/Subquery/...) -> 需要 alias
    """
    from sqlglot import exp
    return not isinstance(expression, (exp.Alias, exp.Column, exp.Star))


def expand_select_star(
    sql: str,
    *,
    dialect: str | None = None,
    columns_lookup,
) -> tuple[str, list[dict[str, Any]]]:
    """把 SQL 里的 `SELECT *` / `SELECT t.*` 展开成显式列名列表.

    用户想"查所有字段但不写 *"时点编辑器工具栏的"展开 *"按钮触发此函数.

    Args:
        sql: 用户写的 SELECT
        dialect: sqlglot 方言
        columns_lookup: `(schema: str, table: str) -> list[str] | None` callable;
            返回该表的列名顺序列表; None 表示找不到(cache miss / 表不存在).
            通常从 metadata cache 取,cache miss 时 caller 可选择 fallback 真 DB 拉.

    Returns:
        (rewritten_sql, warnings)
        warnings 形如 [{"code": "table_not_in_cache", "table": "ks.his_done", "message": "..."}, ...]
        无 * 可展开时 rewritten_sql == sql,warnings 含 code='no_star' 提示.
        parse 失败返原 SQL + code='parse_failed' warning.
    """
    import sqlglot
    from sqlglot import exp

    warnings: list[dict[str, Any]] = []
    try:
        statements = sqlglot.parse(sql, read=dialect or None)
    except Exception as exc:
        warnings.append({"code": "parse_failed", "message": str(exc)})
        return sql, warnings

    any_star = False
    changed = False

    for statement in statements:
        if statement is None:
            continue
        for select in statement.find_all(exp.Select):
            # 收集本 SELECT 的 FROM 中 (alias_or_name, schema_qualified_table)
            # alias_map: alias → (schema, table)
            alias_map: dict[str, tuple[str, str]] = {}
            tables_in_order: list[tuple[str, str, str]] = []  # (alias, schema, table)
            for table_node in select.find_all(exp.Table):
                # 只看属于本 select 直接 FROM 的(不挖进 subquery / CTE)
                # 用 find_ancestor(Select) 判定
                parent_sel = table_node.find_ancestor(exp.Select)
                if parent_sel is not select:
                    continue
                schema = (table_node.db or "").strip("\"`[]") or ""
                tname = (table_node.name or "").strip("\"`[]") or ""
                if not tname:
                    continue
                alias = table_node.alias or tname
                alias_map[alias.lower()] = (schema, tname)
                tables_in_order.append((alias, schema, tname))

            new_expressions = []
            for expression in select.expressions:
                # 处理 SELECT *(无 qualifier)
                if isinstance(expression, exp.Star):
                    any_star = True
                    if not tables_in_order:
                        warnings.append({"code": "star_without_from", "message": "SELECT * 无 FROM,无法展开"})
                        new_expressions.append(expression)
                        continue
                    # 展开为所有表的所有列(多表 JOIN 时全部列出,加 alias 防歧义)
                    multi_table = len(tables_in_order) > 1
                    expanded = []
                    for alias, schema, tname in tables_in_order:
                        cols = columns_lookup(schema, tname)
                        if not cols:
                            warnings.append({
                                "code": "table_not_in_cache",
                                "table": f"{schema}.{tname}" if schema else tname,
                                "message": f"表 {schema}.{tname} 没拉到字段缓存,无法展开 — 先点 [全量] 按钮重新加载",
                            })
                            continue
                        for col in cols:
                            if multi_table:
                                expanded.append(exp.column(col, table=alias))
                            else:
                                expanded.append(exp.column(col))
                    if expanded:
                        new_expressions.extend(expanded)
                        changed = True
                    else:
                        new_expressions.append(expression)  # 没拉到列,保留 *
                # 处理 SELECT t.*(有 qualifier)
                elif isinstance(expression, exp.Column) and isinstance(expression.this, exp.Star):
                    any_star = True
                    table_ref = (expression.table or "").strip("\"`[]") or ""
                    if not table_ref:
                        new_expressions.append(expression)
                        continue
                    info = alias_map.get(table_ref.lower())
                    if info is None:
                        warnings.append({
                            "code": "alias_not_found",
                            "table": table_ref,
                            "message": f"找不到表别名 {table_ref} 对应的 FROM 表",
                        })
                        new_expressions.append(expression)
                        continue
                    schema, tname = info
                    cols = columns_lookup(schema, tname)
                    if not cols:
                        warnings.append({
                            "code": "table_not_in_cache",
                            "table": f"{schema}.{tname}" if schema else tname,
                            "message": f"表 {schema}.{tname} 没拉到字段缓存,无法展开 — 先点 [全量] 按钮",
                        })
                        new_expressions.append(expression)
                        continue
                    for col in cols:
                        new_expressions.append(exp.column(col, table=table_ref))
                    changed = True
                else:
                    new_expressions.append(expression)

            select.set("expressions", new_expressions)

    if not any_star:
        warnings.append({"code": "no_star", "message": "SQL 里没有 * 可展开"})
        return sql, warnings

    if not changed:
        return sql, warnings

    try:
        rewritten = ";\n".join(
            stmt.sql(dialect=dialect or None, pretty=False)
            for stmt in statements
            if stmt is not None
        )
        return rewritten, warnings
    except Exception as exc:
        warnings.append({"code": "serialize_failed", "message": str(exc)})
        return sql, warnings


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
