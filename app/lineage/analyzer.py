from __future__ import annotations

import re
from typing import Any

from app.lineage._common import is_alias_reference as _is_alias_reference
from app.lineage._common import normalize_table_name as _normalize_table_name
from app.lineage._common import raw_sql_aliases as _raw_sql_aliases
from app.lineage._common import unique_strings as _unique_strings
from app.lineage._common import weaker_confidence as _weaker_confidence
from app.lineage.dialects import resolve_dialect as _resolve_dialect
from app.lineage.graph import graph_edges as _graph_edges
from app.lineage.graph import graph_groups as _graph_groups
from app.lineage.segments import (
    extract_dynamic_sql_segments as _extract_dynamic_sql_segments,
    extract_procedure_segments as _extract_procedure_segments,
    parse_lineage_statements as _parse_lineage_statements,
    parse_segments as _parse_segments,
)
from app.lineage.variables import (
    assigned_value as _assigned_value,
    script_variables as _script_variables,
    variable_names as _variable_names,
)
from app.lineage.warnings import (
    analysis_warnings as _analysis_warnings,
    unique_warning_dicts as _unique_warning_dicts,
)


def analyze_sql_lineage(sql: str, dialect: str | None = None, schema: dict[str, list[str]] | None = None) -> dict[str, Any]:
    try:
        import sqlglot
    except ModuleNotFoundError as exc:
        raise RuntimeError("sqlglot is not installed. Please install sqlglot to use SQL lineage analysis.") from exc

    dialect = _resolve_dialect(dialect)
    script_variables = _script_variables(sql)
    dynamic_sql_segments = _extract_dynamic_sql_segments(sql)
    dynamic_sqls = [s["sql"] for s in dynamic_sql_segments]
    procedure_segments = _extract_procedure_segments(sql)
    procedure_sqls = [s["sql"] for s in procedure_segments]
    try:
        primary_statements = _parse_lineage_statements(sqlglot, sql, dialect)
    except Exception:
        # If the script's outer shell cannot be parsed (e.g. PL/SQL control flow),
        # we still want to analyze procedure-body / dynamic segments that parse cleanly.
        if not (procedure_sqls or dynamic_sqls):
            raise
        primary_statements = []
    statements = _unique_parsed_statements(
        primary_statements
        + _parse_segments(sqlglot, dynamic_sqls, dialect, ignore_errors=True)
        + _parse_segments(sqlglot, procedure_sqls, dialect, ignore_errors=True)
    )
    analysis_statements = _unique_analysis_statements([
        statement
        for parsed_statement in statements
        if parsed_statement is not None
        for statement in _analysis_statements(parsed_statement)
    ])
    normalized_schema = _normalize_schema(schema or {})
    parse_errors: list[dict[str, str]] = []
    analyses: list[dict[str, Any]] = []
    for statement in analysis_statements:
        try:
            analyses.append(_analyze_statement(statement, script_variables, normalized_schema))
        except Exception as exc:
            parse_errors.append({"sql": _sql(statement), "error": str(exc)})
    graph_edges = _graph_edges(analyses)
    graph_groups = _graph_groups(graph_edges, analyses)
    warnings = _analysis_warnings(analyses, dynamic_sql_segments, parse_errors, procedure_segments)
    return {
        "statement_count": len(analyses),
        "tables": _unique_items(item for analysis in analyses for item in analysis["tables"]),
        "columns": [column for analysis in analyses for column in analysis["columns"]],
        "insert_mappings": _statement_indexed_items(analyses, "insert_mappings"),
        "joins": [join for analysis in analyses for join in analysis["joins"]],
        "filters": [item for analysis in analyses for item in analysis["filters"]],
        "group_by": [item for analysis in analyses for item in analysis["group_by"]],
        "unions": [item for analysis in analyses for item in analysis["unions"]],
        "variables": script_variables,
        "aliases": _unique_strings(alias for analysis in analyses for alias in analysis.get("aliases", [])),
        "dynamic_sql_count": len(dynamic_sql_segments),
        "dynamic_sql_segments": dynamic_sql_segments,
        "procedure_segments": procedure_segments,
        "graph_edges": graph_edges,
        "graph_groups": graph_groups,
        "parse_errors": parse_errors,
        "warnings": warnings,
        "statements": analyses,
    }


def _exp():
    from sqlglot import exp

    return exp


def _sql(expression: Any | None) -> str:
    if expression is None:
        return ""
    original_sql = getattr(expression, "_lineage_original_sql", "")
    if original_sql:
        return original_sql
    try:
        from sqlglot import ErrorLevel

        return expression.sql(unsupported_level=ErrorLevel.IGNORE)
    except TypeError:
        return expression.sql()


def _unique_parsed_statements(statements: list[Any]) -> list[Any]:
    result: list[Any] = []
    seen: set[str] = set()
    for statement in statements:
        key = _sql(statement) if statement is not None else ""
        if key in seen:
            continue
        seen.add(key)
        result.append(statement)
    return result


def _unique_analysis_statements(statements: list[Any]) -> list[Any]:
    """Dedupe DML statements by structurally-normalized SQL.

    A PROCEDURE block parsed at the top level and the same DML re-parsed from
    procedure_segments produce two distinct AST objects with identical semantics.
    Compare their canonical sqlglot output (lowercased, whitespace-collapsed).
    """
    result: list[Any] = []
    seen: set[str] = set()
    for statement in statements:
        if statement is None:
            continue
        canonical = " ".join(_sql(statement).lower().split())
        if canonical in seen:
            continue
        seen.add(canonical)
        result.append(statement)
    return result


def _analysis_statements(statement: Any) -> list[Any]:
    exp = _exp()
    if isinstance(statement, exp.Create):
        nested = [item for item in statement.find_all(exp.Insert)]
        if nested:
            return nested
        if isinstance(statement.args.get("expression"), (exp.Select, exp.Union)):
            return [statement]
    if isinstance(statement, (exp.Insert, exp.Select, exp.Union, exp.Update, exp.Merge)):
        return [statement]
    return []


def _analyze_statement(
    statement: Any,
    script_variables: list[dict[str, str]] | None = None,
    schema: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    exp = _exp()
    alias_map = _table_alias_map(statement)
    subquery_tables = _derived_table_map(statement)
    subquery_map = _derived_column_map(statement, alias_map)
    selects = list(statement.find_all(exp.Select))
    aliases = sorted(_alias_names(statement) | _raw_sql_aliases(_sql(statement)))
    return {
        "type": statement.key.upper(),
        "aliases": aliases,
        "tables": _tables(statement),
        "columns": [
            column
            for select_index, select in enumerate(selects, start=1)
            for column in _select_columns(
                select,
                alias_map,
                subquery_map,
                subquery_tables,
                select_index,
                script_variables or [],
                schema or {},
            )
        ],
        "joins": [
            join
            for select_index, select in enumerate(selects, start=1)
            for join in _joins(select, select_index)
        ],
        "insert_mappings": _insert_mappings(statement, alias_map, subquery_map, subquery_tables, script_variables or [], schema or {}),
        "filters": [
            filter_item
            for select_index, select in enumerate(selects, start=1)
            for filter_item in _filters(select, select_index)
        ],
        "group_by": [
            group_item
            for select_index, select in enumerate(selects, start=1)
            for group_item in _group_by(select, select_index)
        ],
        "unions": _unions(statement),
        "variables": _variables_in_expression(statement, script_variables or []),
        "sql": _sql(statement),
    }


def _tables(statement: Any) -> list[dict[str, str]]:
    exp = _exp()
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    cte_names = _cte_names(statement)
    target_tables = _target_table_names(statement)
    alias_names = _alias_names(statement)
    for table in statement.find_all(exp.Table):
        table_name = _table_name(table)
        if not _is_physical_source_table(table_name, cte_names, target_tables, alias_names):
            continue
        alias = _explicit_alias(table)
        key = (table_name, alias)
        if key in seen:
            continue
        seen.add(key)
        result.append({"table": table_name, "alias": alias})
    return result


def _table_alias_map(statement: Any) -> dict[str, str]:
    exp = _exp()
    alias_map: dict[str, str] = {}
    cte_names = _cte_names(statement)
    target_tables = _target_table_names(statement)
    alias_names = _alias_names(statement)
    for table in statement.find_all(exp.Table):
        table_name = _table_name(table)
        if not _is_physical_source_table(table_name, cte_names, target_tables, alias_names):
            continue
        alias = _explicit_alias(table)
        if alias:
            alias_map[alias] = table_name
        alias_map[table.name] = table_name
    return alias_map


def _select_columns(
    select: Any,
    alias_map: dict[str, str],
    subquery_map: dict[tuple[str, str], dict[str, list[str]]],
    subquery_tables: dict[str, list[str]],
    select_index: int,
    script_variables: list[dict[str, str]],
    schema: dict[str, list[str]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    default_tables = _select_direct_source_tables(select)
    for expression in select.expressions:
        expanded_star = _expanded_star_columns(expression, default_tables, schema, alias_map, subquery_tables)
        if expanded_star:
            result.extend(
                {
                    "select_index": select_index,
                    "output_column": item["output_column"],
                    "expression": _sql(expression),
                    "source_columns": [item["source_column"]],
                    "source_tables": [item["source_table"]],
                    "variables": _variables_in_expression(expression, script_variables),
                    "transform": "星号展开",
                    "confidence": "high",
                    "warnings": [],
                }
                for item in expanded_star
            )
            continue
        source_info = _source_info(expression, alias_map, subquery_map, subquery_tables, default_tables, schema)
        result.append(
            {
                "select_index": select_index,
                "output_column": expression.alias_or_name or _sql(expression),
                "expression": _sql(expression),
                "source_columns": source_info["source_columns"],
                "source_tables": source_info["source_tables"],
                "variables": _variables_in_expression(expression, script_variables),
                "transform": _transform_type(expression),
                "confidence": source_info["confidence"],
                "warnings": source_info["warnings"],
            }
        )
    return result


def _insert_mappings(
    statement: Any,
    alias_map: dict[str, str],
    subquery_map: dict[tuple[str, str], dict[str, list[str]]],
    subquery_tables: dict[str, list[str]],
    script_variables: list[dict[str, str]],
    schema: dict[str, list[str]],
) -> list[dict[str, Any]]:
    exp = _exp()
    if isinstance(statement, exp.Update):
        return _update_table_mappings(statement)
    if isinstance(statement, exp.Merge):
        return _merge_table_mappings(statement)
    if isinstance(statement, exp.Create):
        return _create_table_mappings(statement, alias_map, subquery_map, subquery_tables, script_variables, schema)
    if not isinstance(statement, exp.Insert):
        return []

    target = statement.this
    target_table = _insert_target_table(target)
    target_columns = _insert_target_columns(target)
    source_select = _insert_source_select(statement)
    if not isinstance(source_select, exp.Select):
        return []

    mappings: list[dict[str, Any]] = []
    default_tables = _select_direct_source_tables(source_select)
    for position, expression in enumerate(source_select.expressions, start=1):
        expanded_star = _expanded_star_columns(expression, default_tables, schema, alias_map, subquery_tables)
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
                        "expression": _sql(expression),
                        "source_columns": [item["source_column"]],
                        "source_tables": [item["source_table"]],
                        "variables": _variables_in_expression(expression, script_variables),
                        "transform": "星号展开",
                        "dml_type": _insert_dml_type(statement),
                        "confidence": "high",
                        "warnings": [],
                    }
                )
            continue
        target_column = target_columns[position - 1] if position <= len(target_columns) else ""
        source_info = _source_info(expression, alias_map, subquery_map, subquery_tables, default_tables, schema)
        mappings.append(
            {
                "position": position,
                "target_table": target_table,
                "target_column": target_column,
                "target": f"{target_table}.{target_column}" if target_table and target_column else target_column,
                "select_output_column": expression.alias_or_name or _sql(expression),
                "expression": _sql(expression),
                "source_columns": source_info["source_columns"],
                "source_tables": source_info["source_tables"],
                "variables": _variables_in_expression(expression, script_variables),
                "transform": _transform_type(expression),
                "dml_type": _insert_dml_type(statement),
                "confidence": source_info["confidence"],
                "warnings": source_info["warnings"],
            }
        )
    return mappings


def _create_table_mappings(
    statement: Any,
    alias_map: dict[str, str],
    subquery_map: dict[tuple[str, str], dict[str, list[str]]],
    subquery_tables: dict[str, list[str]],
    script_variables: list[dict[str, str]],
    schema: dict[str, list[str]],
) -> list[dict[str, Any]]:
    exp = _exp()
    target_table = _create_target_table(statement)
    is_temp = _is_temp_create(statement)
    source_query = statement.args.get("expression")
    source_select = source_query if isinstance(source_query, exp.Select) else source_query.find(exp.Select) if source_query is not None else None
    if not isinstance(source_select, exp.Select):
        return []

    mappings: list[dict[str, Any]] = []
    default_tables = _select_direct_source_tables(source_select)
    for position, expression in enumerate(source_select.expressions, start=1):
        expanded_star = _expanded_star_columns(expression, default_tables, schema, alias_map, subquery_tables)
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
                        "expression": _sql(expression),
                        "source_columns": [item["source_column"]],
                        "source_tables": [item["source_table"]],
                        "variables": _variables_in_expression(expression, script_variables),
                        "transform": "星号展开",
                        "dml_type": _create_dml_type(statement),
                        "is_temp": is_temp,
                        "confidence": "high",
                        "warnings": [],
                    }
                )
            continue
        target_column = expression.alias_or_name or _sql(expression)
        source_info = _source_info(expression, alias_map, subquery_map, subquery_tables, default_tables, schema)
        mappings.append(
            {
                "position": position,
                "target_table": target_table,
                "target_column": target_column,
                "target": f"{target_table}.{target_column}" if target_table and target_column else target_column,
                "select_output_column": target_column,
                "expression": _sql(expression),
                "source_columns": source_info["source_columns"],
                "source_tables": source_info["source_tables"],
                "variables": _variables_in_expression(expression, script_variables),
                "transform": _transform_type(expression),
                "dml_type": _create_dml_type(statement),
                "is_temp": is_temp,
                "confidence": source_info["confidence"],
                "warnings": source_info["warnings"],
            }
        )
    return mappings


def _update_table_mappings(statement: Any) -> list[dict[str, Any]]:
    exp = _exp()
    target_table = _table_name(statement.this) if isinstance(statement.this, exp.Table) else _sql(statement.this)
    target_key = _normalize_table_name(target_table)

    source_tables: list[str] = []
    for table in statement.find_all(exp.Table):
        name = _table_name(table)
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


def _merge_table_mappings(statement: Any) -> list[dict[str, Any]]:
    exp = _exp()
    target_table = _table_name(statement.this) if isinstance(statement.this, exp.Table) else _sql(statement.this)

    source_tables: list[str] = []
    using_expr = statement.args.get("using")
    if using_expr is not None:
        if isinstance(using_expr, exp.Table):
            source_tables.append(_table_name(using_expr))
        else:
            for table in using_expr.find_all(exp.Table):
                source_tables.append(_table_name(table))

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


def _insert_target_table(target: Any) -> str:
    exp = _exp()
    if isinstance(target, exp.Schema):
        target = target.this
    if isinstance(target, exp.Table):
        return _table_name(target)
    return _sql(target) if target is not None else ""


def _create_target_table(statement: Any) -> str:
    exp = _exp()
    target = statement.this
    if isinstance(target, exp.Schema):
        target = target.this
    if isinstance(target, exp.Table):
        return _table_name(target)
    return _sql(target) if target is not None else ""


def _insert_dml_type(statement: Any) -> str:
    explicit = getattr(statement, "_lineage_dml_type", "")
    if explicit:
        return explicit
    if statement.args.get("overwrite"):
        return "INSERT_OVERWRITE"
    return "INSERT"


def _create_dml_type(statement: Any) -> str:
    if _is_temp_create(statement):
        return "CREATE_TEMP_TABLE_AS"
    return "CREATE_OR_REPLACE_TABLE_AS" if statement.args.get("replace") else "CREATE_TABLE_AS"


def _is_temp_create(statement: Any) -> bool:
    exp = _exp()
    if not isinstance(statement, exp.Create):
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


def _insert_target_columns(target: Any) -> list[str]:
    exp = _exp()
    if not isinstance(target, exp.Schema):
        return []
    return [expression.name or _sql(expression) for expression in target.expressions]


def _insert_source_select(statement: Any) -> Any:
    exp = _exp()
    expression = statement.expression
    if isinstance(expression, exp.Select):
        return expression
    if expression is not None:
        nested_select = expression.find(exp.Select)
        if nested_select is not None:
            return nested_select
    return statement.find(exp.Select)


def _derived_column_map(statement: Any, alias_map: dict[str, str]) -> dict[tuple[str, str], dict[str, list[str]]]:
    exp = _exp()
    result: dict[tuple[str, str], dict[str, list[str]]] = {}
    for cte in statement.find_all(exp.CTE):
        _add_derived_select_columns(result, cte.alias, cte.this, alias_map)
    for subquery in statement.find_all(exp.Subquery):
        subquery_alias = _explicit_alias(subquery)
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
    for select in query.find_all(_exp().Select):
        default_tables = _select_direct_source_tables(select)
        for expression in select.expressions:
            output_column = expression.alias_or_name or _sql(expression)
            source_info = _source_info(expression, alias_map, nested_map, nested_tables, default_tables, {})
            key = (derived_alias, output_column)
            existing = result.get(key, {"source_columns": [], "source_tables": []})
            result[key] = {
                "source_columns": _unique_strings(existing["source_columns"] + source_info["source_columns"]),
                "source_tables": _unique_strings(existing["source_tables"] + source_info["source_tables"]),
            }
    result[(derived_alias, "*")] = {
        "source_columns": [],
        "source_tables": _unique_strings(table for select in query.find_all(_exp().Select) for table in _select_direct_source_tables(select)),
    }


def _derived_table_map(statement: Any) -> dict[str, list[str]]:
    exp = _exp()
    result: dict[str, list[str]] = {}
    cte_names = _cte_names(statement)
    target_tables = _target_table_names(statement)
    alias_names = _alias_names(statement)
    for cte in statement.find_all(exp.CTE):
        if cte.alias:
            result[cte.alias] = _unique_strings(
                table_name
                for table in cte.find_all(exp.Table)
                for table_name in [_table_name(table)]
                if _is_physical_source_table(table_name, cte_names, target_tables, alias_names)
            )
    for subquery in statement.find_all(exp.Subquery):
        subquery_alias = _explicit_alias(subquery)
        if not subquery_alias:
            continue
        result[subquery_alias] = _unique_strings(
            table_name
            for table in subquery.find_all(exp.Table)
            for table_name in [_table_name(table)]
            if _is_physical_source_table(table_name, cte_names, target_tables, alias_names)
        )
    return result


def _source_info(
    expression: Any,
    alias_map: dict[str, str],
    subquery_map: dict[tuple[str, str], dict[str, list[str]]],
    subquery_tables: dict[str, list[str]],
    default_tables: list[str] | None = None,
    schema: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    exp = _exp()
    source_columns: list[str] = []
    source_tables: list[str] = []
    warnings: list[dict[str, str]] = []
    confidence = "high"
    default_tables = default_tables or []
    schema = schema or {}

    for column in expression.find_all(exp.Column):
        if column.table:
            expanded = subquery_map.get((column.table, column.name))
            if expanded:
                source_columns.extend(expanded["source_columns"] or [_sql(column)])
                source_tables.extend(expanded["source_tables"])
                continue
            if column.table in subquery_tables:
                source_columns.append(_sql(column))
                source_tables.extend(subquery_tables[column.table])
                continue
            source_columns.append(_sql(column))
            source_tables.append(alias_map.get(column.table, column.table))
            continue

        source_columns.append(_sql(column))
        if len(default_tables) == 1:
            default_table = default_tables[0]
            expanded = subquery_map.get((default_table, column.name))
            if expanded:
                source_columns.pop()
                source_columns.extend(expanded["source_columns"] or [_sql(column)])
                source_tables.extend(expanded["source_tables"])
            elif default_table in subquery_tables:
                source_tables.extend(subquery_tables[default_table])
            else:
                source_tables.append(default_table)
        elif len(default_tables) > 1:
            matched_tables = _tables_with_column(schema, default_tables, column.name)
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


def _tables_with_column(schema: dict[str, list[str]], tables: list[str], column: str) -> list[str]:
    result: list[str] = []
    column_key = _normalize_table_name(column)
    for table in tables:
        if any(_normalize_table_name(item) == column_key for item in _schema_columns(schema, table)):
            result.append(table)
    return _unique_strings(result)


def _select_direct_source_tables(select: Any) -> list[str]:
    exp = _exp()
    tables: list[str] = []
    from_expression = select.args.get("from_")
    if from_expression is not None:
        tables.extend(_source_target_tables(from_expression.this))
    for join in select.args.get("joins") or []:
        tables.extend(_source_target_tables(join.this))
    return _unique_strings(tables)


def _source_target_tables(expression: Any) -> list[str]:
    exp = _exp()
    if expression is None:
        return []
    if isinstance(expression, exp.Table):
        return [_table_name(expression)]
    if isinstance(expression, exp.Subquery):
        alias_names = _alias_names(expression)
        return [
            table_name
            for table in expression.find_all(exp.Table)
            for table_name in [_table_name(table)]
            if _is_physical_source_table(table_name, set(), set(), alias_names)
        ]
    return []


def _expanded_star_columns(
    expression: Any,
    default_tables: list[str],
    schema: dict[str, list[str]],
    alias_map: dict[str, str],
    subquery_tables: dict[str, list[str]],
) -> list[dict[str, str]]:
    exp = _exp()
    stars = list(expression.find_all(exp.Star))
    if not stars:
        return []
    result: list[dict[str, str]] = []
    for star in stars:
        table_name = ""
        parent = star.parent
        if isinstance(parent, exp.Column) and parent.table:
            table_name = alias_map.get(parent.table, parent.table)
        tables = [table_name] if table_name else list(default_tables)
        for table in tables:
            actual_tables = subquery_tables.get(table, [table])
            for actual_table in actual_tables:
                for column in _schema_columns(schema, actual_table):
                    result.append(
                        {
                            "output_column": column,
                            "source_column": f"{actual_table}.{column}",
                            "source_table": actual_table,
                        }
                    )
    return result


def _schema_columns(schema: dict[str, list[str]], table: str) -> list[str]:
    return schema.get(_normalize_table_name(table), [])


def _cte_names(statement: Any) -> set[str]:
    return {cte.alias for cte in statement.find_all(_exp().CTE) if cte.alias}


def _alias_names(statement: Any) -> set[str]:
    exp = _exp()
    aliases: set[str] = set()
    for table in statement.find_all(exp.Table):
        alias = _explicit_alias(table)
        if alias:
            aliases.add(_normalize_table_name(alias))
    for subquery in statement.find_all(exp.Subquery):
        alias = _explicit_alias(subquery)
        if alias:
            aliases.add(_normalize_table_name(alias))
    for cte in statement.find_all(exp.CTE):
        if cte.alias:
            aliases.add(_normalize_table_name(cte.alias))
    return aliases


def _is_physical_source_table(
    table_name: str,
    cte_names: set[str],
    target_tables: set[str],
    alias_names: set[str],
) -> bool:
    normalized = _normalize_table_name(table_name)
    if normalized in {_normalize_table_name(name) for name in cte_names}:
        return False
    if normalized in {_normalize_table_name(name) for name in target_tables}:
        return False
    if "." not in table_name and normalized in alias_names:
        return False
    return True


def _target_table_names(statement: Any) -> set[str]:
    exp = _exp()
    targets: set[str] = set()
    for create in statement.find_all(exp.Create):
        target_name = _create_target_table(create)
        if target_name:
            targets.add(target_name)
    for insert in statement.find_all(exp.Insert):
        target_name = _insert_target_table(insert.this)
        if target_name:
            targets.add(target_name)
    for update in statement.find_all(exp.Update):
        if isinstance(update.this, exp.Table):
            targets.add(_table_name(update.this))
    for merge in statement.find_all(exp.Merge):
        if isinstance(merge.this, exp.Table):
            targets.add(_table_name(merge.this))
    return targets


def _joins(select: Any, select_index: int) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for join in select.args.get("joins") or []:
        on_expression = join.args.get("on")
        result.append(
            {
                "select_index": str(select_index),
                "table": _join_target(join.this),
                "kind": (join.args.get("kind") or "JOIN").upper(),
                "on": _sql(on_expression) if on_expression else "",
            }
        )
    return result


def _filters(select: Any, select_index: int) -> list[dict[str, str]]:
    where = select.args.get("where")
    if not where:
        return []
    return [{"select_index": str(select_index), "condition": _sql(where.this)}]


def _group_by(select: Any, select_index: int) -> list[dict[str, str]]:
    group = select.args.get("group")
    if not group:
        return []
    return [{"select_index": str(select_index), "expression": _sql(item)} for item in group.expressions]


def _unions(statement: Any) -> list[dict[str, str]]:
    exp = _exp()
    result: list[dict[str, str]] = []
    for index, union in enumerate(statement.find_all(exp.Union), start=1):
        is_distinct = union.args.get("distinct")
        result.append(
            {
                "index": str(index),
                "type": "UNION" if is_distinct else "UNION ALL",
                "left": _sql(union.left) if union.left else "",
                "right": _sql(union.right) if union.right else "",
            }
        )
    return result


def _transform_type(expression: Any) -> str:
    exp = _exp()
    if any(True for _ in expression.find_all(exp.AggFunc)):
        return "聚合"
    if isinstance(expression, exp.Column):
        return "直接映射"
    if isinstance(expression, exp.Alias) and isinstance(expression.this, exp.Column):
        return "直接映射"
    return "表达式"


def _variables_in_expression(expression: Any, script_variables: list[dict[str, str]]) -> list[str]:
    exp = _exp()
    sql = _sql(expression)
    known = {item["name"] for item in script_variables}
    names = [variable for variable in _variable_names(sql) if not known or variable in known]
    if known:
        names.extend(column.name for column in expression.find_all(exp.Column) if column.name in known)
    return _unique_strings(names)


def _normalize_schema(schema: dict[str, list[str]]) -> dict[str, list[str]]:
    return {_normalize_table_name(table): list(columns) for table, columns in schema.items()}


def _table_name(table: Any) -> str:
    catalog = table.args.get("catalog")
    db = table.args.get("db")
    parts = []
    if catalog:
        parts.append(catalog.sql())
    if db:
        parts.append(db.sql())
    parts.append(table.name)
    return ".".join(part for part in parts if part)


def _explicit_alias(expression: Any) -> str:
    alias = expression.args.get("alias")
    return alias.name if alias else ""


def _join_target(expression: Any) -> str:
    if expression is None:
        return ""
    exp = _exp()
    alias = _explicit_alias(expression)
    if isinstance(expression, exp.Table):
        table_name = _table_name(expression)
        return f"{table_name} AS {alias}" if alias else table_name
    if isinstance(expression, exp.Subquery):
        return f"子查询 AS {alias}" if alias else "子查询"
    return _sql(expression)


def _unique_items(items: Any) -> list[Any]:
    result: list[Any] = []
    seen: set[str] = set()
    for item in items:
        key = repr(item)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _statement_indexed_items(analyses: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for statement_index, analysis in enumerate(analyses, start=1):
        for item in analysis[key]:
            result.append({"statement_index": statement_index, **item})
    return result
