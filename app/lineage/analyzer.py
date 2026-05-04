"""单 SQL 血缘分析的入口与编排。

`analyze_sql_lineage(sql, dialect, schema)` 是对外 API。它做的事：
  1. 把脚本里的动态 SQL 段、PROCEDURE 段先用正则 / token 平衡抽出来
  2. 主体 + 各 segment 一起送 sqlglot 解析（segment 容错，主体失败仍试 segment）
  3. 把每个 statement 拆成可分析的 DML/DQL 节点（_analysis_statements）
  4. 每个分析节点跑 `_analyze_statement` —— 这是单 statement 的血缘提取
  5. 把所有 statement 结果聚合成"表 / 列 / 映射 / 子句 / 警告"汇总

5 个抽出来的子模块（helpers / tables / columns / dml / clauses）做实际工作；
本文件只做编排。
"""
from __future__ import annotations

from typing import Any

from app.lineage._common import raw_sql_aliases as _raw_sql_aliases
from app.lineage._common import unique_strings as _unique_strings
from app.lineage.aggregation import (
    aggregate_target_summary as _aggregate_target_summary,
    collect_target_operations as _collect_target_operations,
    extract_statement_title as _extract_statement_title,
)
from app.lineage.clauses import filters, group_by, joins, unions
from app.lineage.roles import identify_table_roles as _identify_table_roles
from app.lineage.semantic import build_semantic_lineage as _build_semantic_lineage
from app.lineage.columns import (
    derived_column_map, derived_table_map, select_columns,
)
from app.lineage.dialects import resolve_dialect as _resolve_dialect
from app.lineage.dml import insert_mappings
from app.lineage.graph import graph_edges as _graph_edges
from app.lineage.graph import graph_groups as _graph_groups
from app.lineage.preprocess import normalize_for_parsing as _normalize_for_parsing
from app.lineage.report import to_lineage_report as _to_lineage_report
from app.lineage.helpers import (
    analysis_statements, exp, normalize_schema, sql, statement_indexed_items,
    unique_analysis_statements, unique_items, unique_parsed_statements,
    variables_in_expression,
)
# Phase 9 Day 2：出口处包 model 校验。导入 app.models.lineage 是允许的（schema
# 包不会反向 import lineage 包，避免循环）。
from app.models.lineage import LineageReport as _LineageReport
from app.lineage.segments import (
    extract_dynamic_sql_segments as _extract_dynamic_sql_segments,
    extract_procedure_segments as _extract_procedure_segments,
    parse_lineage_statements as _parse_lineage_statements,
    parse_segments as _parse_segments,
)
from app.lineage.tables import alias_names, physical_tables, table_alias_map
from app.lineage.variables import script_variables as _script_variables
from app.lineage.warnings import analysis_warnings as _analysis_warnings


def analyze_sql_lineage(sql_text: str, dialect: str | None = None, schema: dict[str, list[str]] | None = None) -> dict[str, Any]:
    try:
        import sqlglot
    except ModuleNotFoundError as exc:
        raise RuntimeError("sqlglot is not installed. Please install sqlglot to use SQL lineage analysis.") from exc

    dialect = _resolve_dialect(dialect)
    # script_variables 必须在 normalize 之前抽：normalize 会把 `${name}` → `:name`，
    # 之后就识别不出原始模板变量名了。变量记录里仍保留原始名（前端展示用）。
    script_vars = _script_variables(sql_text)
    # 全角标点 → 半角；`${var}` → `:var`。string / 注释段不动。
    sql_text = _normalize_for_parsing(sql_text)
    dynamic_sql_segments = _extract_dynamic_sql_segments(sql_text)
    dynamic_sqls = [s["sql"] for s in dynamic_sql_segments]
    procedure_segments = _extract_procedure_segments(sql_text)
    procedure_sqls = [s["sql"] for s in procedure_segments]
    # 给每条 procedure_segments 标 parse_status：单独喂 sqlglot 看能不能解析。
    # 这是 step-level lineage 的基础——前端可以高亮"这一段无法静态解析"。
    for seg in procedure_segments:
        try:
            parsed = sqlglot.parse(seg["sql"], read=dialect or None)
        except Exception:
            seg["parse_status"] = "unsupported"
            continue
        seg["parse_status"] = "parsed" if parsed and any(p is not None for p in parsed) else "unsupported"
    try:
        primary_statements = _parse_lineage_statements(sqlglot, sql_text, dialect)
    except Exception:
        # If the script's outer shell cannot be parsed (e.g. PL/SQL control flow),
        # we still want to analyze procedure-body / dynamic segments that parse cleanly.
        if not (procedure_sqls or dynamic_sqls):
            raise
        primary_statements = []
    statements = unique_parsed_statements(
        primary_statements
        + _parse_segments(sqlglot, dynamic_sqls, dialect, ignore_errors=True)
        + _parse_segments(sqlglot, procedure_sqls, dialect, ignore_errors=True)
    )
    deduped_statements = unique_analysis_statements([
        statement
        for parsed_statement in statements
        if parsed_statement is not None
        for statement in analysis_statements(parsed_statement)
    ])
    normalized_schema = normalize_schema(schema or {})
    parse_errors: list[dict[str, str]] = []
    analyses: list[dict[str, Any]] = []
    for statement in deduped_statements:
        try:
            analyses.append(_analyze_statement(statement, script_vars, normalized_schema))
        except Exception as exc:
            parse_errors.append({"sql": sql(statement), "error": str(exc)})
    edges = _graph_edges(analyses)
    groups = _graph_groups(edges, analyses)
    warnings = _analysis_warnings(analyses, dynamic_sql_segments, parse_errors, procedure_segments)
    # target_summary 走 statements（含 DELETE/TRUNCATE）；deduped_statements 已被
    # analysis_statements() 过滤掉非 SELECT/INSERT/UPDATE/MERGE，会漏掉删表 / 截断。
    target_summary = _aggregate_target_summary(
        _collect_target_operations([s for s in statements if s is not None])
    )
    flat_tables = unique_items(item for analysis in analyses for item in analysis["tables"])
    flat_insert_mappings = statement_indexed_items(analyses, "insert_mappings")
    table_roles = _identify_table_roles(flat_tables, target_summary, flat_insert_mappings)
    base_result = {
        "statement_count": len(analyses),
        "tables": flat_tables,
        "columns": [column for analysis in analyses for column in analysis["columns"]],
        "insert_mappings": flat_insert_mappings,
        "target_summary": target_summary,
        "table_roles": table_roles,
        "joins": [join for analysis in analyses for join in analysis["joins"]],
        "filters": [item for analysis in analyses for item in analysis["filters"]],
        "group_by": [item for analysis in analyses for item in analysis["group_by"]],
        "unions": [item for analysis in analyses for item in analysis["unions"]],
        "variables": script_vars,
        "aliases": _unique_strings(alias for analysis in analyses for alias in analysis.get("aliases", [])),
        "dynamic_sql_count": len(dynamic_sql_segments),
        "dynamic_sql_segments": dynamic_sql_segments,
        "procedure_segments": procedure_segments,
        "graph_edges": edges,
        "graph_groups": groups,
        "parse_errors": parse_errors,
        "warnings": warnings,
        "statements": analyses,
    }
    base_result["semantic_lineage"] = _build_semantic_lineage(base_result)
    # Phase 3：附加统一展示模型 LineageAnalysisReport（不修改原字段，前端 / 第三方
    # 可选消费 result.report 渲染同一组组件）
    base_result["report"] = _to_lineage_report(base_result, scope="single")
    # Phase 9 Day 2：出口处包 LineageReport —— typed 字段（target_summary）走 model
    # 校验，未建模字段（envelope extra="allow"）原样透传。ai_inferred /
    # ai_enrichment 不在 LineageReport 字段表里 —— 它们由 lineage_service._attach_*
    # 后续注入，envelope 不预留 Optional 字段（避免 dump 出 None 噪声键）。
    return _LineageReport.model_validate(base_result).model_dump(by_alias=True)


def _analyze_statement(
    statement: Any,
    script_vars: list[dict[str, str]] | None = None,
    schema: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """单个 statement 的血缘分析。返回 tables / columns / joins / insert_mappings
    / filters / group_by / unions / variables 的统一结构，让上层不用关心
    statement 类型差异。"""
    e = exp()
    alias_map = table_alias_map(statement)
    subquery_tables = derived_table_map(statement)
    subquery_map = derived_column_map(statement, alias_map)
    selects = list(statement.find_all(e.Select))
    aliases = sorted(alias_names(statement) | _raw_sql_aliases(sql(statement)))
    return {
        "type": statement.key.upper(),
        "aliases": aliases,
        "tables": physical_tables(statement),
        "columns": [
            column
            for select_index, select in enumerate(selects, start=1)
            for column in select_columns(
                select,
                alias_map,
                subquery_map,
                subquery_tables,
                select_index,
                script_vars or [],
                schema or {},
            )
        ],
        "joins": [
            join
            for select_index, select in enumerate(selects, start=1)
            for join in joins(select, select_index)
        ],
        "insert_mappings": insert_mappings(statement, alias_map, subquery_map, subquery_tables, script_vars or [], schema or {}),
        "filters": [
            filter_item
            for select_index, select in enumerate(selects, start=1)
            for filter_item in filters(select, select_index)
        ],
        "group_by": [
            group_item
            for select_index, select in enumerate(selects, start=1)
            for group_item in group_by(select, select_index)
        ],
        "unions": unions(statement),
        "variables": variables_in_expression(statement, script_vars or []),
        "sql": sql(statement),
        "title": _extract_statement_title(statement),
    }
