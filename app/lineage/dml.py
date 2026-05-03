"""DML 目标 / 列映射 / dml_type 判定。

INSERT / CREATE TABLE AS / UPDATE / MERGE 各有自己的 mapping 形态：
- INSERT / CREATE 走 column-level mapping（每个 source select expression 对一个 target column）
- UPDATE / MERGE 没有显式 select 列，只能给"目标表 ← 来源表"级别的 mapping
"""
from __future__ import annotations

from typing import Any

from app.lineage._common import normalize_table_name as _normalize_table_name
from app.lineage._common import unique_strings as _unique_strings
from app.lineage.columns import (
    expanded_star_columns, select_direct_source_tables, source_info,
)
from app.lineage.helpers import exp, sql, transform_type, variables_in_expression
from app.lineage.tables import table_name


def insert_target_table(target: Any) -> str:
    e = exp()
    if isinstance(target, e.Schema):
        target = target.this
    if isinstance(target, e.Table):
        return table_name(target)
    return sql(target) if target is not None else ""


def insert_target_columns(target: Any) -> list[str]:
    """`INSERT INTO t (a, b, c)` 里 `(a, b, c)` 那部分。没显式列出来则返回空，
    调用方 fall back 到 select 表达式自己的 alias_or_name。"""
    e = exp()
    if not isinstance(target, e.Schema):
        return []
    return [expression.name or sql(expression) for expression in target.expressions]


def insert_source_select(statement: Any) -> Any:
    """从 INSERT 的 expression 子树里挖出底层 SELECT。直接 SELECT、SELECT 包
    在 UNION/Subquery 里、CTAS 等都覆盖。"""
    e = exp()
    expression = statement.expression
    if isinstance(expression, e.Select):
        return expression
    if expression is not None:
        nested_select = expression.find(e.Select)
        if nested_select is not None:
            return nested_select
    return statement.find(e.Select)


def insert_dml_type(statement: Any) -> str:
    explicit = getattr(statement, "_lineage_dml_type", "")
    if explicit:
        return explicit
    if statement.args.get("overwrite"):
        return "INSERT_OVERWRITE"
    return "INSERT"


def create_target_table(statement: Any) -> str:
    e = exp()
    target = statement.this
    if isinstance(target, e.Schema):
        target = target.this
    if isinstance(target, e.Table):
        return table_name(target)
    return sql(target) if target is not None else ""


def create_dml_type(statement: Any) -> str:
    if is_temp_create(statement):
        return "CREATE_TEMP_TABLE_AS"
    return "CREATE_OR_REPLACE_TABLE_AS" if statement.args.get("replace") else "CREATE_TABLE_AS"


def is_temp_create(statement: Any) -> bool:
    """CREATE TEMPORARY / TEMP / GLOBAL TEMPORARY 任意一种都算临时表。
    临时表在批量血缘报告里会被过滤出"外部源 / 最终产物"列表，避免把
    跨段中间产物误报成业务输入。"""
    e = exp()
    if not isinstance(statement, e.Create):
        return False
    if statement.args.get("temporary"):
        return True
    properties = statement.args.get("properties")
    if properties is None:
        return False
    for prop in properties.expressions:
        prop_name = type(prop).__name__.lower()
        if "temporary" in prop_name or "temp" in prop_name:
            return True
    return False


def update_table_mappings(statement: Any) -> list[dict[str, Any]]:
    """UPDATE 没有"select 列 → target 列"映射，只能给"来源表 → 目标表"
    粒度。confidence=medium 反映 column 级追溯不可得。"""
    e = exp()
    target_table = table_name(statement.this) if isinstance(statement.this, e.Table) else sql(statement.this)
    target_key = _normalize_table_name(target_table)

    source_tables: list[str] = []
    for table in statement.find_all(e.Table):
        name = table_name(table)
        if not name or _normalize_table_name(name) == target_key:
            continue
        source_tables.append(name)

    source_tables = _unique_strings(source_tables)
    return [
        {
            "position": i + 1,
            "target_table": target_table,
            "target_column": "",
            "target": target_table,
            "select_output_column": "",
            "expression": "",
            "source_columns": [],
            "source_tables": [src],
            "variables": [],
            "transform": "UPDATE",
            "dml_type": "UPDATE",
            "confidence": "medium",
            "warnings": [],
        }
        for i, src in enumerate(source_tables)
    ]


def merge_table_mappings(statement: Any) -> list[dict[str, Any]]:
    """MERGE 同 UPDATE，给"USING 子句的源表 → 目标表"粒度。"""
    e = exp()
    target_table = table_name(statement.this) if isinstance(statement.this, e.Table) else sql(statement.this)

    source_tables: list[str] = []
    using_expr = statement.args.get("using")
    if using_expr is not None:
        if isinstance(using_expr, e.Table):
            source_tables.append(table_name(using_expr))
        else:
            for table in using_expr.find_all(e.Table):
                source_tables.append(table_name(table))

    source_tables = _unique_strings(source_tables)
    return [
        {
            "position": i + 1,
            "target_table": target_table,
            "target_column": "",
            "target": target_table,
            "select_output_column": "",
            "expression": "",
            "source_columns": [],
            "source_tables": [src],
            "variables": [],
            "transform": "MERGE",
            "dml_type": "MERGE",
            "confidence": "medium",
            "warnings": [],
        }
        for i, src in enumerate(source_tables)
    ]


def delete_table_mappings(statement: Any) -> list[dict[str, Any]]:
    """DELETE keeps table-level dependencies when its predicate reads other tables."""
    e = exp()
    target_table = table_name(statement.this) if isinstance(statement.this, e.Table) else sql(statement.this)
    target_key = _normalize_table_name(target_table)

    source_tables: list[str] = []
    for table in statement.find_all(e.Table):
        name = table_name(table)
        if not name or _normalize_table_name(name) == target_key:
            continue
        source_tables.append(name)

    source_tables = _unique_strings(source_tables)
    where_expr = statement.args.get("where")
    return [
        {
            "position": i + 1,
            "target_table": target_table,
            "target_column": "",
            "target": target_table,
            "select_output_column": "",
            "expression": sql(where_expr) if where_expr is not None else "",
            "source_columns": [],
            "source_tables": [src],
            "variables": variables_in_expression(where_expr, []) if where_expr is not None else [],
            "transform": "DELETE",
            "dml_type": "DELETE",
            "confidence": "medium",
            "warnings": [],
        }
        for i, src in enumerate(source_tables)
    ]


def insert_mappings(
    statement: Any,
    alias_map: dict[str, str],
    subquery_map: dict[tuple[str, str], dict[str, list[str]]],
    subquery_tables: dict[str, list[str]],
    script_variables: list[dict[str, str]],
    schema: dict[str, list[str]],
) -> list[dict[str, Any]]:
    e = exp()
    if isinstance(statement, e.Update):
        return update_table_mappings(statement)
    if isinstance(statement, e.Merge):
        return merge_table_mappings(statement)
    if isinstance(statement, e.Delete):
        return delete_table_mappings(statement)
    if isinstance(statement, e.Create):
        return create_table_mappings(statement, alias_map, subquery_map, subquery_tables, script_variables, schema)
    if not isinstance(statement, e.Insert):
        return []

    target = statement.this
    target_table = insert_target_table(target)
    target_columns = insert_target_columns(target)
    source_select = insert_source_select(statement)
    if not isinstance(source_select, e.Select):
        return []

    mappings: list[dict[str, Any]] = []
    default_tables = select_direct_source_tables(source_select)
    for position, expression in enumerate(source_select.expressions, start=1):
        expanded_star = expanded_star_columns(expression, default_tables, schema, alias_map, subquery_tables)
        if expanded_star:
            for item in expanded_star:
                target_column = target_columns[len(mappings)] if len(mappings) < len(target_columns) else item["output_column"]
                mappings.append(
                    {
                        "position": len(mappings) + 1,
                        "target_table": target_table,
                        "target_column": target_column,
                        "target": f"{target_table}.{target_column}" if target_table and target_column else target_column,
                        "select_output_column": item["output_column"],
                        "expression": sql(expression),
                        "source_columns": [item["source_column"]],
                        "source_tables": [item["source_table"]],
                        "variables": variables_in_expression(expression, script_variables),
                        "transform": "星号展开",
                        "dml_type": insert_dml_type(statement),
                        "confidence": "high",
                        "warnings": [],
                    }
                )
            continue
        target_column = target_columns[position - 1] if position <= len(target_columns) else ""
        info = source_info(expression, alias_map, subquery_map, subquery_tables, default_tables, schema)
        mappings.append(
            {
                "position": position,
                "target_table": target_table,
                "target_column": target_column,
                "target": f"{target_table}.{target_column}" if target_table and target_column else target_column,
                "select_output_column": expression.alias_or_name or sql(expression),
                "expression": sql(expression),
                "source_columns": info["source_columns"],
                "source_tables": info["source_tables"],
                "variables": variables_in_expression(expression, script_variables),
                "transform": transform_type(expression),
                "dml_type": insert_dml_type(statement),
                "confidence": info["confidence"],
                "warnings": info["warnings"],
            }
        )
    return mappings


def create_table_mappings(
    statement: Any,
    alias_map: dict[str, str],
    subquery_map: dict[tuple[str, str], dict[str, list[str]]],
    subquery_tables: dict[str, list[str]],
    script_variables: list[dict[str, str]],
    schema: dict[str, list[str]],
) -> list[dict[str, Any]]:
    e = exp()
    target_table = create_target_table(statement)
    is_temp = is_temp_create(statement)
    source_query = statement.args.get("expression")
    source_select = source_query if isinstance(source_query, e.Select) else source_query.find(e.Select) if source_query is not None else None
    if not isinstance(source_select, e.Select):
        return []

    mappings: list[dict[str, Any]] = []
    default_tables = select_direct_source_tables(source_select)
    for position, expression in enumerate(source_select.expressions, start=1):
        expanded_star = expanded_star_columns(expression, default_tables, schema, alias_map, subquery_tables)
        if expanded_star:
            for item in expanded_star:
                target_column = item["output_column"]
                mappings.append(
                    {
                        "position": len(mappings) + 1,
                        "target_table": target_table,
                        "target_column": target_column,
                        "target": f"{target_table}.{target_column}" if target_table and target_column else target_column,
                        "select_output_column": item["output_column"],
                        "expression": sql(expression),
                        "source_columns": [item["source_column"]],
                        "source_tables": [item["source_table"]],
                        "variables": variables_in_expression(expression, script_variables),
                        "transform": "星号展开",
                        "dml_type": create_dml_type(statement),
                        "is_temp": is_temp,
                        "confidence": "high",
                        "warnings": [],
                    }
                )
            continue
        target_column = expression.alias_or_name or sql(expression)
        info = source_info(expression, alias_map, subquery_map, subquery_tables, default_tables, schema)
        mappings.append(
            {
                "position": position,
                "target_table": target_table,
                "target_column": target_column,
                "target": f"{target_table}.{target_column}" if target_table and target_column else target_column,
                "select_output_column": target_column,
                "expression": sql(expression),
                "source_columns": info["source_columns"],
                "source_tables": info["source_tables"],
                "variables": variables_in_expression(expression, script_variables),
                "transform": transform_type(expression),
                "dml_type": create_dml_type(statement),
                "is_temp": is_temp,
                "confidence": info["confidence"],
                "warnings": info["warnings"],
            }
        )
    return mappings
