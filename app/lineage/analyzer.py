from __future__ import annotations

import re
from typing import Any

from app.lineage._common import is_alias_reference as _is_alias_reference
from app.lineage._common import normalize_table_name as _normalize_table_name
from app.lineage._common import raw_sql_aliases as _raw_sql_aliases
from app.lineage._common import unique_strings as _unique_strings


def analyze_sql_lineage(sql: str, dialect: str | None = None, schema: dict[str, list[str]] | None = None) -> dict[str, Any]:
    try:
        import sqlglot
    except ModuleNotFoundError as exc:
        raise RuntimeError("sqlglot is not installed. Please install sqlglot to use SQL lineage analysis.") from exc

    script_variables = _script_variables(sql)
    dynamic_sql_segments = _extract_dynamic_sql_segments(sql)
    dynamic_sqls = [s["sql"] for s in dynamic_sql_segments]
    statements = _unique_parsed_statements(
        _parse_lineage_statements(sqlglot, sql, dialect)
        + _parse_segments(sqlglot, dynamic_sqls, dialect, ignore_errors=True)
    )
    analysis_statements = [
        statement
        for parsed_statement in statements
        if parsed_statement is not None
        for statement in _analysis_statements(parsed_statement)
    ]
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
        "graph_edges": graph_edges,
        "graph_groups": graph_groups,
        "parse_errors": parse_errors,
        "statements": analyses,
    }


def _exp():
    from sqlglot import exp

    return exp


def _sql(expression: Any | None) -> str:
    if expression is None:
        return ""
    try:
        from sqlglot import ErrorLevel

        return expression.sql(unsupported_level=ErrorLevel.IGNORE)
    except TypeError:
        return expression.sql()


def _parse_lineage_statements(sqlglot: Any, sql: str, dialect: str | None) -> list[Any]:
    try:
        return sqlglot.parse(sql, read=dialect or None)
    except Exception:
        statements: list[Any] = []
        errors: list[Exception] = []
        dynamic_sqls = [s["sql"] for s in _extract_dynamic_sql_segments(sql)]
        for segment in _extract_analyzable_segments(sql) + dynamic_sqls:
            parsed = _parse_segments(sqlglot, [segment], dialect, ignore_errors=True)
            if parsed:
                statements.extend(parsed)
            else:
                try:
                    sqlglot.parse(segment, read=dialect or None)
                except Exception as exc:
                    errors.append(exc)
        if statements:
            return statements
        if errors:
            raise errors[0]
        return sqlglot.parse(sql, read=dialect or None)


def _parse_segments(sqlglot: Any, segments: list[str], dialect: str | None, ignore_errors: bool = False) -> list[Any]:
    statements: list[Any] = []
    for segment in segments:
        try:
            statements.extend(sqlglot.parse(segment, read=dialect or None))
        except Exception:
            if not ignore_errors:
                raise
    return statements


def _extract_analyzable_segments(sql: str) -> list[str]:
    segments: list[str] = []
    for raw_segment in re.split(r";", sql):
        segment = raw_segment.strip()
        if not segment:
            continue
        segment = re.sub(r"^(begin|then|else)\b", "", segment, flags=re.IGNORECASE).strip()
        segment = re.sub(r"\bend\s*$", "", segment, flags=re.IGNORECASE).strip()
        if re.match(r"^(with|select|insert|create\s+(or\s+replace\s+)?procedure|create\s+(or\s+replace\s+)?function)\b", segment, re.IGNORECASE):
            segments.append(segment)
    return segments


def _extract_dynamic_sql_segments(sql: str) -> list[dict[str, str]]:
    seen: set[str] = set()
    result: list[dict[str, str]] = []

    keyword_pattern = re.compile(
        r"(?:execute\s+immediate|exec(?:ute)?\s+(?:sys\.)?sp_executesql)\s+(?:N)?'((?:''|[^'])*)'",
        flags=re.IGNORECASE | re.DOTALL,
    )
    for match in keyword_pattern.finditer(sql):
        segment = _unescape_sql_string(match.group(1))
        if _looks_like_lineage_sql(segment):
            cleaned = _clean_dynamic_segment(segment)
            if cleaned not in seen:
                seen.add(cleaned)
                result.append({"sql": cleaned, "source": "execute_keyword", "confidence": "high"})

    string_pattern = re.compile(r"(?:N)?'((?:''|[^']){20,})'", flags=re.IGNORECASE | re.DOTALL)
    for match in string_pattern.finditer(sql):
        segment = _unescape_sql_string(match.group(1))
        if _looks_like_lineage_sql(segment):
            cleaned = _clean_dynamic_segment(segment)
            if cleaned not in seen:
                seen.add(cleaned)
                result.append({"sql": cleaned, "source": "string_literal", "confidence": "medium"})

    return result


def _unescape_sql_string(value: str) -> str:
    return value.replace("''", "'")


def _clean_dynamic_segment(segment: str) -> str:
    return " ".join(segment.strip().rstrip(";").split())


def _looks_like_lineage_sql(segment: str) -> bool:
    cleaned = segment.strip()
    if not cleaned:
        return False
    return bool(
        re.match(r"^(with|select|insert)\b", cleaned, flags=re.IGNORECASE)
        or re.search(r"\binsert\s+into\b.+\bselect\b", cleaned, flags=re.IGNORECASE | re.DOTALL)
    )


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


def _analysis_statements(statement: Any) -> list[Any]:
    exp = _exp()
    if isinstance(statement, exp.Create):
        nested = [item for item in statement.find_all(exp.Insert)]
        if nested:
            return nested
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
                }
                for item in expanded_star
            )
            continue
        source_info = _source_info(expression, alias_map, subquery_map, subquery_tables, default_tables)
        result.append(
            {
                "select_index": select_index,
                "output_column": expression.alias_or_name or _sql(expression),
                "expression": _sql(expression),
                "source_columns": source_info["source_columns"],
                "source_tables": source_info["source_tables"],
                "variables": _variables_in_expression(expression, script_variables),
                "transform": _transform_type(expression),
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
                        "dml_type": "INSERT",
                    }
                )
            continue
        target_column = target_columns[position - 1] if position <= len(target_columns) else ""
        source_info = _source_info(expression, alias_map, subquery_map, subquery_tables, default_tables)
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
                "dml_type": "INSERT",
            }
        )
    return mappings


def _update_table_mappings(statement: Any) -> list[dict[str, Any]]:
    exp = _exp()
    target_table = _table_name(statement.this) if isinstance(statement.this, exp.Table) else _sql(statement.this)

    source_tables: list[str] = []
    from_expr = statement.args.get("from_")
    if from_expr is not None:
        for table in from_expr.find_all(exp.Table):
            name = _table_name(table)
            if name and _normalize_table_name(name) != _normalize_table_name(target_table):
                source_tables.append(name)
    where_expr = statement.args.get("where")
    if where_expr is not None:
        for subq in where_expr.find_all(exp.Subquery):
            for table in subq.find_all(exp.Table):
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
            "transform": "UPDATE",
            "dml_type": "UPDATE",
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
            source_info = _source_info(expression, alias_map, nested_map, nested_tables, default_tables)
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
) -> dict[str, list[str]]:
    exp = _exp()
    source_columns: list[str] = []
    source_tables: list[str] = []
    default_tables = default_tables or []

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

    return {
        "source_columns": _unique_strings(source_columns),
        "source_tables": _unique_strings(source_tables),
    }


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


def _script_variables(sql: str) -> list[dict[str, str]]:
    variables: list[dict[str, str]] = []
    for variable in _variable_names(sql):
        variables.append(
            {
                "name": variable,
                "placeholder": variable,
                "assigned_value": _assigned_value(sql, variable),
            }
        )
    return variables


def _variables_in_expression(expression: Any, script_variables: list[dict[str, str]]) -> list[str]:
    exp = _exp()
    sql = _sql(expression)
    known = {item["name"] for item in script_variables}
    names = [variable for variable in _variable_names(sql) if not known or variable in known]
    if known:
        names.extend(column.name for column in expression.find_all(exp.Column) if column.name in known)
    return _unique_strings(names)


def _variable_names(sql: str) -> list[str]:
    names: list[str] = []
    patterns = [
        r"\$\{\s*([A-Za-z_][\w$#]*)\s*\}",
        r"(?<!:):([A-Za-z_][\w$#]*)",
        r"@([A-Za-z_][\w$#]*)",
    ]
    for pattern in patterns:
        names.extend(match.group(1) for match in re.finditer(pattern, sql))
    return _unique_strings(names)


def _assigned_value(sql: str, variable: str) -> str:
    escaped = re.escape(variable)
    patterns = [
        rf"\b{escaped}\b\s*:=\s*(.*?)(?:;|\n|$)",
        rf"\b{escaped}\b\s*=\s*(.*?)(?:;|\n|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, sql, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return " ".join(match.group(1).strip().split())
    return ""


def _normalize_schema(schema: dict[str, list[str]]) -> dict[str, list[str]]:
    return {_normalize_table_name(table): list(columns) for table, columns in schema.items()}


def _graph_edges(analyses: list[dict[str, Any]]) -> list[dict[str, str]]:
    edges: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for analysis in analyses:
        aliases = set(analysis.get("aliases", []))
        for mapping in analysis.get("insert_mappings", []):
            for source_table in mapping.get("source_tables", []):
                target_table = mapping.get("target_table", "")
                if _is_alias_reference(source_table, aliases):
                    continue
                key = (_normalize_table_name(source_table), _normalize_table_name(target_table))
                if not source_table or not target_table or key in seen:
                    continue
                seen.add(key)
                edges.append({"source_table": source_table, "target_table": target_table})
    return edges


def _graph_groups(edges: list[dict[str, str]], analyses: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    by_target: dict[str, dict[str, Any]] = {}
    for edge in edges:
        target_table = edge["target_table"]
        target_key = _normalize_table_name(target_table)
        group = by_target.get(target_key)
        if group is None:
            group = {"target_table": target_table, "source_tables": [], "dependency_tables": [], "_source_keys": set(), "_dependency_keys": set()}
            by_target[target_key] = group
            groups.append(group)
        source_key = _normalize_table_name(edge["source_table"])
        if source_key in group["_source_keys"]:
            continue
        group["_source_keys"].add(source_key)
        group["source_tables"].append(edge["source_table"])

    for analysis in analyses or []:
        aliases = set(analysis.get("aliases", []))
        target_tables = _unique_strings(mapping.get("target_table", "") for mapping in analysis.get("insert_mappings", []))
        field_source_keys = {
            _normalize_table_name(source_table)
            for mapping in analysis.get("insert_mappings", [])
            for source_table in mapping.get("source_tables", [])
            if not _is_alias_reference(source_table, aliases)
        }
        dependency_tables = [
            table["table"]
            for table in analysis.get("tables", [])
            if _normalize_table_name(table["table"]) not in field_source_keys
            and not _is_alias_reference(table["table"], aliases)
        ]
        for target_table in target_tables:
            target_key = _normalize_table_name(target_table)
            group = by_target.get(target_key)
            if group is None:
                group = {"target_table": target_table, "source_tables": [], "dependency_tables": [], "_source_keys": set(), "_dependency_keys": set()}
                by_target[target_key] = group
                groups.append(group)
            for dependency_table in dependency_tables:
                dependency_key = _normalize_table_name(dependency_table)
                if dependency_key in group["_source_keys"] or dependency_key in group["_dependency_keys"]:
                    continue
                group["_dependency_keys"].add(dependency_key)
                group["dependency_tables"].append(dependency_table)

    for group in groups:
        group.pop("_source_keys", None)
        group.pop("_dependency_keys", None)
    return groups


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
