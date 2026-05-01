from __future__ import annotations

import re
from typing import Any

from app.lineage._common import is_alias_reference as _is_alias_reference
from app.lineage._common import normalize_table_name as _normalize_table_name
from app.lineage._common import raw_sql_aliases as _raw_sql_aliases
from app.lineage._common import unique_strings as _unique_strings
from app.lineage.dialects import resolve_dialect as _resolve_dialect


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


def _parse_lineage_statements(sqlglot: Any, sql: str, dialect: str | None) -> list[Any]:
    try:
        return sqlglot.parse(sql, read=dialect or None) + _parse_segments(
            sqlglot, _extract_replace_segments(sql), dialect, ignore_errors=True
        )
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


def _extract_replace_segments(sql: str) -> list[str]:
    return [
        segment.strip()
        for segment in re.split(r";", sql)
        if re.match(r"^\s*replace\s+into\b", segment, flags=re.IGNORECASE)
        and re.search(r"\bselect\b", segment, flags=re.IGNORECASE | re.DOTALL)
    ]


def _parse_segments(sqlglot: Any, segments: list[str], dialect: str | None, ignore_errors: bool = False) -> list[Any]:
    statements: list[Any] = []
    for segment in segments:
        compatible = _parse_compatible_segment(sqlglot, segment, dialect)
        if compatible:
            statements.extend(compatible)
            continue
        try:
            statements.extend(sqlglot.parse(segment, read=dialect or None))
        except Exception:
            if not ignore_errors:
                raise
    return statements


def _parse_compatible_segment(sqlglot: Any, segment: str, dialect: str | None) -> list[Any]:
    normalized = segment.strip()
    replacements = [
        (r"^replace\s+into\b", "INSERT INTO", "REPLACE"),
    ]
    for pattern, replacement, dml_type in replacements:
        if not re.match(pattern, normalized, flags=re.IGNORECASE):
            continue
        compatible_sql = re.sub(pattern, replacement, normalized, count=1, flags=re.IGNORECASE)
        try:
            parsed = sqlglot.parse(compatible_sql, read=dialect or None)
        except Exception:
            return []
        for statement in parsed:
            setattr(statement, "_lineage_dml_type", dml_type)
            setattr(statement, "_lineage_original_sql", normalized)
        return parsed
    return []


def _extract_analyzable_segments(sql: str) -> list[str]:
    segments: list[str] = []
    for raw_segment in re.split(r";", sql):
        segment = raw_segment.strip()
        if not segment:
            continue
        segment = re.sub(r"^(begin|then|else)\b", "", segment, flags=re.IGNORECASE).strip()
        segment = re.sub(r"\bend\s*$", "", segment, flags=re.IGNORECASE).strip()
        if re.match(r"^(with|select|insert|replace\s+into|create\s+(or\s+replace\s+)?procedure|create\s+(or\s+replace\s+)?function|create\s+(or\s+replace\s+)?(temporary\s+|temp\s+)?table)\b", segment, re.IGNORECASE):
            segments.append(segment)
    return segments


_RE_PROC_HEADER = re.compile(
    r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:DEFINER\s*=\s*\S+\s+)?(?P<kind>PROCEDURE|FUNCTION|PACKAGE\s+BODY|TRIGGER)\s+(?P<name>[\w$#.\"`\[\]]+)",
    flags=re.IGNORECASE,
)
_RE_BEGIN = re.compile(r"\bBEGIN\b", flags=re.IGNORECASE)
_RE_BLOCK_TOKEN = re.compile(r"\bBEGIN\b|\bEND\b", flags=re.IGNORECASE)
_RE_BODY_DML = re.compile(
    r"\b(WITH|SELECT|INSERT|REPLACE\s+INTO|UPDATE|DELETE|MERGE|CREATE\s+(?:OR\s+REPLACE\s+)?(?:GLOBAL\s+TEMPORARY\s+|TEMPORARY\s+|TEMP\s+)?TABLE|TRUNCATE)\b",
    flags=re.IGNORECASE,
)


def _extract_procedure_segments(sql: str) -> list[dict[str, str]]:
    """Extract DML statements nested inside CREATE PROCEDURE/FUNCTION/PACKAGE BODY/TRIGGER blocks.

    Skips control-flow shells (IF/LOOP/EXCEPTION) and nested BEGIN/END blocks via token-balanced scan.
    """
    seen: set[str] = set()
    result: list[dict[str, str]] = []
    for header in _RE_PROC_HEADER.finditer(sql):
        name = header.group("name").strip()
        kind = " ".join(header.group("kind").split()).upper()
        body_start = _RE_BEGIN.search(sql, pos=header.end())
        if not body_start:
            continue
        depth = 1
        cursor = body_start.end()
        body_end = len(sql)
        for tok_match in _RE_BLOCK_TOKEN.finditer(sql, pos=cursor):
            tok = tok_match.group(0).upper()
            if tok == "BEGIN":
                depth += 1
            else:
                depth -= 1
                if depth == 0:
                    body_end = tok_match.start()
                    break
        body = sql[body_start.end():body_end]
        for index, segment in enumerate(_iter_procedure_body_segments(body), start=1):
            cleaned = _clean_dynamic_segment(segment)
            if cleaned in seen:
                continue
            seen.add(cleaned)
            result.append(
                {
                    "procedure_name": name,
                    "procedure_kind": kind,
                    "segment_index": str(index),
                    "sql": cleaned,
                    "confidence": "high",
                }
            )
    return result


def _iter_procedure_body_segments(body: str) -> list[str]:
    """Split a procedure body into top-level statements, skipping control-flow shells."""
    segments: list[str] = []
    depth = 0
    buffer: list[str] = []
    pos = 0
    length = len(body)
    while pos < length:
        char = body[pos]
        # Skip string literals to avoid splitting on ; inside them.
        if char in "'\"":
            quote = char
            buffer.append(char)
            pos += 1
            while pos < length:
                buffer.append(body[pos])
                if body[pos] == quote and (pos + 1 >= length or body[pos + 1] != quote):
                    pos += 1
                    break
                if body[pos] == quote:
                    buffer.append(body[pos + 1])
                    pos += 2
                    continue
                pos += 1
            continue
        # Track nested BEGIN/END so a ; inside a nested block doesn't end the outer segment.
        if char.isalpha():
            tail = body[pos:pos + 6].upper()
            if tail.startswith("BEGIN") and (pos + 5 >= length or not body[pos + 5].isalnum()):
                depth += 1
                buffer.append(body[pos:pos + 5])
                pos += 5
                continue
            if tail.startswith("END") and (pos + 3 >= length or not body[pos + 3].isalnum()):
                if depth > 0:
                    depth -= 1
                buffer.append(body[pos:pos + 3])
                pos += 3
                continue
        if char == ";" and depth == 0:
            segments.append("".join(buffer).strip())
            buffer = []
            pos += 1
            continue
        buffer.append(char)
        pos += 1
    tail = "".join(buffer).strip()
    if tail:
        segments.append(tail)
    cleaned: list[str] = []
    for seg in segments:
        # The segment may start with control-flow shells (IF ... THEN, ELSIF ... THEN, FOR ... LOOP).
        # Find the first DML keyword and analyze from there.
        match = _RE_BODY_DML.search(seg)
        if match:
            cleaned.append(seg[match.start():].strip())
    return cleaned


def _extract_dynamic_sql_segments(sql: str) -> list[dict[str, str]]:
    seen: set[str] = set()
    result: list[dict[str, str]] = []

    def add(segment: str, source: str, confidence: str) -> None:
        cleaned = _clean_dynamic_segment(segment)
        if cleaned in seen or not _looks_like_lineage_sql(cleaned):
            return
        seen.add(cleaned)
        result.append({"sql": cleaned, "source": source, "confidence": confidence})

    keyword_pattern = re.compile(
        r"(?:execute\s+immediate|exec(?:ute)?\s+(?:sys\.)?sp_executesql)\s+(?:N)?'((?:''|[^'])*)'",
        flags=re.IGNORECASE | re.DOTALL,
    )
    for match in keyword_pattern.finditer(sql):
        add(_unescape_sql_string(match.group(1)), "execute_keyword", "high")

    # MySQL: SET @sql := '...'; PREPARE stmt FROM @sql; EXECUTE stmt;
    set_vars = _capture_session_var_assignments(sql)
    prepare_pattern = re.compile(
        r"\bPREPARE\s+\w+\s+FROM\s+(?:@(\w+)|(?:N)?'((?:''|[^'])*)')",
        flags=re.IGNORECASE | re.DOTALL,
    )
    for match in prepare_pattern.finditer(sql):
        var_name, literal = match.group(1), match.group(2)
        if literal:
            add(_unescape_sql_string(literal), "prepare_literal", "high")
        elif var_name and var_name.lower() in set_vars:
            for assignment in set_vars[var_name.lower()]:
                add(assignment["sql"], "prepare_var", assignment["confidence"])

    # Oracle/PL-SQL: v_sql := 'INSERT INTO ' || p_t || ' SELECT ...'; EXECUTE IMMEDIATE v_sql;
    plsql_vars = _capture_plsql_var_assignments(sql)
    immediate_var_pattern = re.compile(
        r"\bEXECUTE\s+IMMEDIATE\s+([\w$#]+)\s*[;\n]",
        flags=re.IGNORECASE,
    )
    for match in immediate_var_pattern.finditer(sql):
        var_name = match.group(1).lower()
        if var_name in plsql_vars:
            for assignment in plsql_vars[var_name]:
                add(assignment["sql"], "var_concat", assignment["confidence"])

    string_pattern = re.compile(r"(?:N)?'((?:''|[^']){20,})'", flags=re.IGNORECASE | re.DOTALL)
    for match in string_pattern.finditer(sql):
        add(_unescape_sql_string(match.group(1)), "string_literal", "medium")

    return result


_RE_SET_ASSIGNMENT = re.compile(r"^\s*SET\s+@(\w+)\s*:?=\s*(.+)$", flags=re.IGNORECASE | re.DOTALL)
_RE_PLSQL_ASSIGNMENT = re.compile(r"^\s*([\w$#]+)\s*:=\s*(.+)$", flags=re.IGNORECASE | re.DOTALL)


def _capture_session_var_assignments(sql: str) -> dict[str, list[dict[str, str]]]:
    return _capture_var_assignments(sql, _RE_SET_ASSIGNMENT)


def _capture_plsql_var_assignments(sql: str) -> dict[str, list[dict[str, str]]]:
    return _capture_var_assignments(sql, _RE_PLSQL_ASSIGNMENT)


def _capture_var_assignments(
    sql: str, assignment_re: re.Pattern[str]
) -> dict[str, list[dict[str, str]]]:
    # Strip block-shell tokens so var assignments inside procedure bodies become top-level statements.
    flat = re.sub(r"\b(BEGIN|END|DECLARE|THEN|ELSE|ELSIF|LOOP)\b", " ", sql, flags=re.IGNORECASE)
    result: dict[str, list[dict[str, str]]] = {}
    for segment in _split_top_level_statements(flat):
        match = assignment_re.match(segment)
        if not match:
            continue
        name = match.group(1).lower()
        rhs = match.group(2).strip()
        if "||" in rhs or rhs.upper().startswith("CONCAT"):
            rebuilt = _rebuild_concat(rhs)
            if rebuilt and _looks_like_lineage_sql(rebuilt):
                result.setdefault(name, []).append({"sql": rebuilt, "confidence": "low"})
            continue
        if rhs.startswith("'") or rhs[:2].upper() == "N'":
            literal = _strip_quoted(rhs)
            if _looks_like_lineage_sql(literal):
                result.setdefault(name, []).append({"sql": literal, "confidence": "high"})
    return result


def _split_top_level_statements(sql: str) -> list[str]:
    """Split a SQL script into top-level statements, respecting quoted strings."""
    statements: list[str] = []
    buf: list[str] = []
    pos = 0
    length = len(sql)
    while pos < length:
        char = sql[pos]
        if char in "'\"":
            quote = char
            buf.append(char)
            pos += 1
            while pos < length:
                buf.append(sql[pos])
                if sql[pos] == quote and (pos + 1 >= length or sql[pos + 1] != quote):
                    pos += 1
                    break
                if sql[pos] == quote:
                    buf.append(sql[pos + 1])
                    pos += 2
                    continue
                pos += 1
            continue
        if char == ";":
            statements.append("".join(buf).strip())
            buf = []
            pos += 1
            continue
        buf.append(char)
        pos += 1
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return [s for s in statements if s]


def _strip_quoted(value: str) -> str:
    cleaned = value.strip()
    if cleaned[:1].upper() == "N":
        cleaned = cleaned[1:]
    if cleaned.startswith("'") and cleaned.endswith("'"):
        cleaned = cleaned[1:-1]
    return _unescape_sql_string(cleaned)


def _rebuild_concat(expression: str) -> str:
    """Reassemble literal-only segments of a concat; replace variable parts with :placeholders.

    Handles two forms: 'lit' || var || 'lit' (PL/SQL) and CONCAT('lit', var, 'lit') (MySQL).
    Variable segments become :var so sqlglot can still parse the resulting SQL.
    """
    expression = expression.strip()
    parts: list[str]
    concat_match = re.match(r"CONCAT\s*\((.*)\)\s*$", expression, flags=re.IGNORECASE | re.DOTALL)
    if concat_match:
        parts = _split_top_level(concat_match.group(1), ",")
    else:
        parts = _split_top_level(expression, "||")
    rebuilt: list[str] = []
    for raw in parts:
        item = raw.strip()
        if not item:
            continue
        if item.startswith("'") or item[:2].upper() == "N'":
            rebuilt.append(_strip_quoted(item))
        else:
            placeholder = re.sub(r"\W+", "_", item).strip("_") or "var"
            rebuilt.append(f":{placeholder}")
    return "".join(rebuilt)


def _split_top_level(expression: str, delimiter: str) -> list[str]:
    """Split on delimiter respecting parentheses and quoted strings."""
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    pos = 0
    delim_len = len(delimiter)
    while pos < len(expression):
        char = expression[pos]
        if char == "'":
            buf.append(char)
            pos += 1
            while pos < len(expression):
                buf.append(expression[pos])
                if expression[pos] == "'" and (pos + 1 >= len(expression) or expression[pos + 1] != "'"):
                    pos += 1
                    break
                if expression[pos] == "'":
                    buf.append(expression[pos + 1])
                    pos += 2
                    continue
                pos += 1
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if depth == 0 and expression[pos:pos + delim_len] == delimiter:
            parts.append("".join(buf))
            buf = []
            pos += delim_len
            continue
        buf.append(char)
        pos += 1
    parts.append("".join(buf))
    return parts


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
        or re.search(r"\b(insert|replace)\s+into\b.+\bselect\b", cleaned, flags=re.IGNORECASE | re.DOTALL)
        or re.search(r"\bcreate\s+(or\s+replace\s+)?(temporary\s+|temp\s+)?table\b.+\bas\s+select\b", cleaned, flags=re.IGNORECASE | re.DOTALL)
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


def _weaker_confidence(current: str, candidate: str) -> str:
    order = {"high": 0, "medium": 1, "low": 2}
    return candidate if order.get(candidate, 0) > order.get(current, 0) else current


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


def _graph_edges(analyses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    by_key: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for statement_index, analysis in enumerate(analyses, start=1):
        aliases = set(analysis.get("aliases", []))
        for mapping in analysis.get("insert_mappings", []):
            for source_table in mapping.get("source_tables", []):
                target_table = mapping.get("target_table", "")
                if _is_alias_reference(source_table, aliases):
                    continue
                key = (
                    _normalize_table_name(source_table),
                    _normalize_table_name(target_table),
                    str(statement_index),
                    str(mapping.get("dml_type", "")),
                )
                if not source_table or not target_table:
                    continue
                edge = by_key.get(key)
                if edge is None:
                    edge = {
                        "source_table": source_table,
                        "target_table": target_table,
                        "statement_index": statement_index,
                        "edge_type": mapping.get("dml_type", "INSERT"),
                        "source_columns": [],
                        "target_columns": [],
                        "confidence": "high",
                        "reason": "",
                    }
                    by_key[key] = edge
                    edges.append(edge)
                edge["source_columns"] = _unique_strings(edge["source_columns"] + mapping.get("source_columns", []))
                target_column = mapping.get("target_column", "")
                if target_column:
                    edge["target_columns"] = _unique_strings(edge["target_columns"] + [target_column])
                edge["confidence"] = _weaker_confidence(edge["confidence"], mapping.get("confidence", "high"))
                reason = mapping.get("expression") or mapping.get("transform", "")
                if reason:
                    edge["reason"] = "; ".join(_unique_strings([part for part in [edge["reason"], reason] if part]))
    return edges


def _graph_groups(edges: list[dict[str, Any]], analyses: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
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


def _analysis_warnings(
    analyses: list[dict[str, Any]],
    dynamic_sql_segments: list[dict[str, str]],
    parse_errors: list[dict[str, str]],
    procedure_segments: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    if dynamic_sql_segments:
        warnings.append(
            {
                "type": "动态 SQL",
                "message": f"识别到 {len(dynamic_sql_segments)} 段动态 SQL，静态分析结果可能不完整",
            }
        )
    if procedure_segments:
        proc_names = _unique_strings(seg.get("procedure_name", "") for seg in procedure_segments)
        warnings.append(
            {
                "type": "存储过程",
                "message": f"识别到 {len(proc_names)} 个过程/函数中的 {len(procedure_segments)} 段 DML：{', '.join(proc_names)}",
            }
        )
    for error in parse_errors:
        warnings.append({"type": "解析失败", "message": error.get("error", "")})
    for statement_index, analysis in enumerate(analyses, start=1):
        for item in analysis.get("columns", []) + analysis.get("insert_mappings", []):
            for warning in item.get("warnings", []):
                warnings.append(
                    {
                        "type": warning.get("type", "血缘提示"),
                        "message": warning.get("message", ""),
                        "statement_index": str(statement_index),
                    }
                )
    return _unique_warning_dicts(warnings)


def _unique_warning_dicts(items: list[dict[str, str]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[tuple[str, str], ...]] = set()
    for item in items:
        key = tuple(sorted((str(k), str(v)) for k, v in item.items()))
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


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
