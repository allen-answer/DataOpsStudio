"""字段级血缘核心：select 列拆解 / star 展开 / source_info 推断 / CTE+子查询
列衍生映射。这一层不关心 DML 语义，只关心"一个表达式的字段来源是哪些表
的哪些列"。
"""
from __future__ import annotations

from typing import Any

from app.lineage._common import normalize_table_name as _normalize_table_name
from app.lineage._common import unique_strings as _unique_strings
from app.lineage._common import weaker_confidence as _weaker_confidence
from app.lineage.helpers import exp, sql, transform_type, variables_in_expression, window_partition_columns
from app.lineage.tables import (
    alias_names, explicit_alias, is_physical_source_table, table_name,
)
from app.lineage.warnings import unique_warning_dicts as _unique_warning_dicts


def schema_columns(schema: dict[str, list[str]], table: str) -> list[str]:
    return schema.get(_normalize_table_name(table), [])


def tables_with_column(schema: dict[str, list[str]], tables: list[str], column: str) -> list[str]:
    """在多表场景下，根据 schema 元数据判定一个未限定字段在哪些表里出现。"""
    result: list[str] = []
    column_key = _normalize_table_name(column)
    for table in tables:
        if any(_normalize_table_name(item) == column_key for item in schema_columns(schema, table)):
            result.append(table)
    return _unique_strings(result)


def select_direct_source_tables(select: Any) -> list[str]:
    """SELECT 直接 FROM / JOIN 的物理表名（按声明顺序，去重）。子查询在这里
    暂时返回它们能找到的物理表（视作"虚拟来源"）—— `_source_target_tables`
    同时处理两类。"""
    tables: list[str] = []
    from_expression = select.args.get("from_")
    if from_expression is not None:
        tables.extend(source_target_tables(from_expression.this))
    for join in select.args.get("joins") or []:
        tables.extend(source_target_tables(join.this))
    return _unique_strings(tables)


def source_target_tables(expression: Any) -> list[str]:
    e = exp()
    if expression is None:
        return []
    if isinstance(expression, e.Table):
        return [table_name(expression)]
    if isinstance(expression, e.Subquery):
        aliases = alias_names(expression)
        return [
            name
            for table in expression.find_all(e.Table)
            for name in [table_name(table)]
            if is_physical_source_table(name, set(), set(), aliases)
        ]
    return []


def expanded_star_columns(
    expression: Any,
    default_tables: list[str],
    schema: dict[str, list[str]],
    alias_map: dict[str, str],
    subquery_tables: dict[str, list[str]],
) -> list[dict[str, str]]:
    """`SELECT *` / `SELECT t.*` 展开 —— 需要 schema 元数据有该表列定义。
    没有 schema 信息时返回空列表，调用方走普通 source_info 路径。"""
    e = exp()
    stars = list(expression.find_all(e.Star))
    if not stars:
        return []
    result: list[dict[str, str]] = []
    for star in stars:
        starred_table = ""
        parent = star.parent
        if isinstance(parent, e.Column) and parent.table:
            starred_table = alias_map.get(parent.table, parent.table)
        tables = [starred_table] if starred_table else list(default_tables)
        for table in tables:
            actual_tables = subquery_tables.get(table, [table])
            for actual_table in actual_tables:
                for column in schema_columns(schema, actual_table):
                    result.append(
                        {
                            "output_column": column,
                            "source_column": f"{actual_table}.{column}",
                            "source_table": actual_table,
                        }
                    )
    return result


def source_info(
    expression: Any,
    alias_map: dict[str, str],
    subquery_map: dict[tuple[str, str], dict[str, list[str]]],
    subquery_tables: dict[str, list[str]],
    default_tables: list[str] | None = None,
    schema: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """字段级追溯核心：扫表达式里的 Column 节点，对每一个判定它来自哪张表
    （可能是 alias / 子查询 / 单表 / 多表 schema 推断）。返回 source_columns /
    source_tables / confidence / warnings 四元。

    不确定时（多表都有同名字段、schema 缺失）confidence 降级 + 加 warning。"""
    e = exp()
    source_columns: list[str] = []
    source_tables: list[str] = []
    warnings: list[dict[str, str]] = []
    confidence = "high"
    default_tables = default_tables or []
    schema = schema or {}

    for column in expression.find_all(e.Column):
        if column.table:
            expanded = subquery_map.get((column.table, column.name))
            if expanded:
                source_columns.extend(expanded["source_columns"] or [sql(column)])
                source_tables.extend(expanded["source_tables"])
                continue
            if column.table in subquery_tables:
                source_columns.append(sql(column))
                source_tables.extend(subquery_tables[column.table])
                continue
            source_columns.append(sql(column))
            source_tables.append(alias_map.get(column.table, column.table))
            continue

        source_columns.append(sql(column))
        if len(default_tables) == 1:
            default_table = default_tables[0]
            expanded = subquery_map.get((default_table, column.name))
            if expanded:
                source_columns.pop()
                source_columns.extend(expanded["source_columns"] or [sql(column)])
                source_tables.extend(expanded["source_tables"])
            elif default_table in subquery_tables:
                source_tables.extend(subquery_tables[default_table])
            else:
                source_tables.append(default_table)
        elif len(default_tables) > 1:
            matched_tables = tables_with_column(schema, default_tables, column.name)
            if len(matched_tables) == 1:
                matched_table = matched_tables[0]
                source_columns[-1] = f"{matched_table}.{column.name}"
                source_tables.append(matched_table)
                confidence = _weaker_confidence(confidence, "medium")
            elif len(matched_tables) > 1:
                source_tables.extend(matched_tables)
                confidence = _weaker_confidence(confidence, "low")
                warnings.append(
                    {
                        "type": "字段歧义",
                        "message": f"未限定字段 {column.name} 同时存在于多张来源表: {', '.join(matched_tables)}",
                    }
                )
            else:
                confidence = _weaker_confidence(confidence, "low")
                warnings.append(
                    {
                        "type": "字段来源未知",
                        "message": f"未限定字段 {column.name} 无法在当前 Schema 元数据中归属来源表",
                    }
                )

    return {
        "source_columns": _unique_strings(source_columns),
        "source_tables": _unique_strings(source_tables),
        "confidence": confidence,
        "warnings": _unique_warning_dicts(warnings),
    }


def select_columns(
    select: Any,
    alias_map: dict[str, str],
    subquery_map: dict[tuple[str, str], dict[str, list[str]]],
    subquery_tables: dict[str, list[str]],
    select_index: int,
    script_variables: list[dict[str, str]],
    schema: dict[str, list[str]],
) -> list[dict[str, Any]]:
    """SELECT 的 expressions list 拆成字段级血缘条目。star 展开优先；其它
    表达式走 source_info 推断。

    schema-aware 降级（参考 DataHub 实践）：
    - SELECT * 缺 schema → 单条 medium-confidence 占位 + warning（不当 high
      可信，因为我们没法静态知道真实列）
    - SELECT t.* 但 schema 没该表 → 同上
    - 多表 unqualified column 缺 schema → source_info 已降 low + warning
    """
    e = exp()
    result: list[dict[str, Any]] = []
    default_tables = select_direct_source_tables(select)
    for expression in select.expressions:
        expanded_star = expanded_star_columns(expression, default_tables, schema, alias_map, subquery_tables)
        if expanded_star:
            result.extend(
                {
                    "select_index": select_index,
                    "output_column": item["output_column"],
                    "expression": sql(expression),
                    "source_columns": [item["source_column"]],
                    "source_tables": [item["source_table"]],
                    "variables": variables_in_expression(expression, script_variables),
                    "transform": "星号展开",
                    "confidence": "high",
                    "warnings": [],
                }
                for item in expanded_star
            )
            continue
        # SELECT * / SELECT t.* 但 expanded_star 为空 → schema 元数据缺该表列
        # 定义。给一个降级占位（confidence=medium + warning），让前端 / report
        # 能标"通配符未展开，列级关系仅按表级假设"。
        stars = list(expression.find_all(e.Star))
        if stars:
            star_target = ""
            for star in stars:
                parent = star.parent
                if isinstance(parent, e.Column) and parent.table:
                    star_target = alias_map.get(parent.table, parent.table)
                    break
            star_tables = [star_target] if star_target else list(default_tables)
            result.append({
                "select_index": select_index,
                "output_column": expression.alias_or_name or sql(expression),
                "expression": sql(expression),
                "source_columns": [sql(expression)],
                "source_tables": star_tables,
                "variables": variables_in_expression(expression, script_variables),
                "transform": "星号展开（未解析）",
                "confidence": "medium",
                "warnings": [{
                    "type": "通配符未展开",
                    "message": "SELECT * 缺少 Schema 元数据，无法静态展开真实列；列级关系仅按表级假设",
                }],
            })
            continue
        info = source_info(expression, alias_map, subquery_map, subquery_tables, default_tables, schema)
        entry: dict[str, Any] = {
            "select_index": select_index,
            "output_column": expression.alias_or_name or sql(expression),
            "expression": sql(expression),
            "source_columns": info["source_columns"],
            "source_tables": info["source_tables"],
            "variables": variables_in_expression(expression, script_variables),
            "transform": transform_type(expression),
            "confidence": info["confidence"],
            "warnings": info["warnings"],
        }
        # 窗口函数额外暴露 partition_by / order_by 列（让前端能区分"分组依赖" vs 普通源）
        if entry["transform"] == "窗口":
            window_meta = window_partition_columns(expression)
            if window_meta["partition_by"] or window_meta["order_by"]:
                entry["window"] = window_meta
        result.append(entry)
    return result


def derived_column_map(statement: Any, alias_map: dict[str, str]) -> dict[tuple[str, str], dict[str, list[str]]]:
    """CTE / 子查询的字段衍生映射：(derived_alias, output_column) → 物理来源。
    给 source_info 在追溯 `cte.col` 时一步打通到底层物理列。"""
    e = exp()
    result: dict[tuple[str, str], dict[str, list[str]]] = {}
    for cte in statement.find_all(e.CTE):
        _add_derived_select_columns(result, cte.alias, cte.this, alias_map)
    for subquery in statement.find_all(e.Subquery):
        subquery_alias = explicit_alias(subquery)
        if not subquery_alias:
            continue
        _add_derived_select_columns(result, subquery_alias, subquery.this, alias_map)
    return result


def _add_derived_select_columns(
    result: dict[tuple[str, str], dict[str, list[str]]],
    derived_alias: str,
    query: Any,
    alias_map: dict[str, str],
) -> None:
    if not derived_alias:
        return
    nested_map = {key: value for key, value in result.items() if key[0] != derived_alias}
    nested_tables = {alias: values["source_tables"] for (alias, column), values in nested_map.items() if column == "*"}
    e = exp()
    for select in query.find_all(e.Select):
        default_tables = select_direct_source_tables(select)
        for expression in select.expressions:
            output_column = expression.alias_or_name or sql(expression)
            info = source_info(expression, alias_map, nested_map, nested_tables, default_tables, {})
            key = (derived_alias, output_column)
            existing = result.get(key, {"source_columns": [], "source_tables": []})
            result[key] = {
                "source_columns": _unique_strings(existing["source_columns"] + info["source_columns"]),
                "source_tables": _unique_strings(existing["source_tables"] + info["source_tables"]),
            }
    result[(derived_alias, "*")] = {
        "source_columns": [],
        "source_tables": _unique_strings(table for select in query.find_all(e.Select) for table in select_direct_source_tables(select)),
    }


def derived_table_map(statement: Any) -> dict[str, list[str]]:
    """CTE / 子查询 alias → 它依赖的物理表列表。给 source_info 在 column.table
    是某个 CTE 名字时拿到底层物理表（穿透 1 层）。"""
    e = exp()
    result: dict[str, list[str]] = {}
    cte_set = set()
    targets = set()
    aliases_set = set()
    # 不直接调 cte_names / target_table_names / alias_names —— 已在 tables.py，
    # 但本模块已 import 了 alias_names；为减少循环 import 触发面，就地用
    # find_all 简化（语义对等）
    from app.lineage.tables import cte_names as _cte_names, target_table_names as _target, alias_names as _aliases
    cte_set = _cte_names(statement)
    targets = _target(statement)
    aliases_set = _aliases(statement)
    for cte in statement.find_all(e.CTE):
        if cte.alias:
            result[cte.alias] = _unique_strings(
                name
                for table in cte.find_all(e.Table)
                for name in [table_name(table)]
                if is_physical_source_table(name, cte_set, targets, aliases_set)
            )
    for subquery in statement.find_all(e.Subquery):
        subquery_alias = explicit_alias(subquery)
        if not subquery_alias:
            continue
        result[subquery_alias] = _unique_strings(
            name
            for table in subquery.find_all(e.Table)
            for name in [table_name(table)]
            if is_physical_source_table(name, cte_set, targets, aliases_set)
        )
    return result
