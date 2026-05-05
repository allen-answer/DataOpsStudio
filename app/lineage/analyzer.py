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

import re
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
    # S5：cursor source tracking —— `FOR rec IN (SELECT FROM tabA) LOOP INSERT
    # INTO tabB VALUES (rec.col); END LOOP;` 的 INSERT 没 source_tables（VALUES
    # 不是 SELECT），_graph_edges 看不到 tabA → tabB 边。这里给 procedure_segment
    # 的 cursor_sources 补 supplemental 边，confidence=medium 区分静态推断
    edges.extend(_cursor_supplemental_edges(procedure_segments, edges))
    # S5 PR4：UDF source tracking —— `INSERT INTO tabC VALUES (pkg.fn())` 调用
    # 用户定义函数；函数体内的 SELECT 能看到 ods.txn，但 INSERT 自己 source_tables
    # 是空。这里把"被调用 UDF 读的表"补成 supplemental 边到 INSERT target。
    edges.extend(_udf_supplemental_edges(procedure_segments, analyses, edges))
    # S5 PR9：BULK COLLECT + FORALL pattern —— 当 procedure 段里有
    # `SELECT ... BULK COLLECT INTO v FROM tabA;` 然后
    # `FORALL i ... INSERT INTO tabB VALUES (v(i).col, ...);` 这种 array
    # 中转模式时，补 tabA → tabB 边（confidence=medium / edge_type=BULK_COLLECT）
    edges.extend(_bulk_collect_supplemental_edges(procedure_segments, edges))
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


# S5：cursor source tracking ——————————————————————————————————————————————

_RE_CURSOR_DML_TARGET = re.compile(
    r"\b(?:INSERT\s+(?:OVERWRITE\s+)?(?:INTO\s+|TABLE\s+)?|REPLACE\s+INTO\s+|UPDATE\s+|MERGE\s+INTO\s+|DELETE\s+FROM\s+)"
    r"([\w$#.\"`\[\]]+)",
    flags=re.IGNORECASE,
)
# S5 PR11：被 _RE_CURSOR_DML_TARGET 误捕的 SQL 关键字 —— 比如 MERGE 里的
# `WHEN MATCHED THEN UPDATE SET col=...` 会让 SET 被当成 UPDATE target，
# 进而生成 `ods.orders → SET` 这种垃圾边。
_DML_TARGET_KEYWORD_BLACKLIST = frozenset({
    "SET", "VALUES", "NULL", "TABLE", "FROM", "WHERE", "USING", "WHEN",
    "MATCHED", "THEN", "ELSE", "ON", "AS",
})


def _is_valid_dml_target(name: str) -> bool:
    if not name:
        return False
    return name.upper() not in _DML_TARGET_KEYWORD_BLACKLIST


def _cursor_supplemental_edges(
    procedure_segments: list[dict[str, Any]],
    existing_edges: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """给 cursor FOR loop 段补 source → target 边。

    示例：
        FOR rec IN (SELECT id FROM ods.batches) LOOP
          INSERT INTO dwd.fact (id) VALUES (rec.id);
        END LOOP;

    INSERT 的 VALUES 不是 SELECT，flat_insert_mappings 拿不到 source_tables，
    `_graph_edges` 里就没 ods.batches → dwd.fact 这条边。这里补出来。

    为避免重复（如果用户写的 cursor SELECT 本身被 sqlglot 解析出来再被 INSERT-
    SELECT 形式消费），先看 existing_edges 是否已经包含同 (source, target)。
    confidence 标 medium 区分是 cursor-inferred 还是 SELECT-derived。
    """
    out: list[dict[str, Any]] = []
    if not procedure_segments:
        return out
    existing: set[tuple[str, str]] = {
        (str(e.get("source_table") or "").lower(), str(e.get("target_table") or "").lower())
        for e in existing_edges
    }
    for seg in procedure_segments:
        sources = seg.get("cursor_sources") or []
        if not sources:
            continue
        seg_sql = str(seg.get("sql") or "")
        if not seg_sql:
            continue
        # 段里可能有多个 INSERT/UPDATE/MERGE —— 全部抽出来
        targets: list[str] = []
        seen_t: set[str] = set()
        for m in _RE_CURSOR_DML_TARGET.finditer(seg_sql):
            tname = m.group(1).strip().strip('"`[]')
            key = tname.lower()
            if not _is_valid_dml_target(tname) or key in seen_t:
                continue
            seen_t.add(key)
            targets.append(tname)
        if not targets:
            continue
        for src in sources:
            for tgt in targets:
                key = (src.lower(), tgt.lower())
                if key in existing:
                    continue
                existing.add(key)
                out.append({
                    "source_table": src,
                    "target_table": tgt,
                    "statement_index": 0,
                    "edge_type": "CURSOR_LOOP_INSERT",
                    "source_columns": [],
                    "target_columns": [],
                    "confidence": "medium",
                    "reason": f"cursor FOR loop ({seg.get('procedure_name') or 'anonymous'})",
                })
    return out


# S5 PR4：UDF source tracking ——————————————————————————————————————————

# 用 segments 模块里的辅助函数提取 SELECT 段里的源表
from app.lineage.segments import _extract_cursor_select_tables as _extract_select_tables  # noqa: E402

_RE_FUNCTION_REF_FROM_NAME = lambda fn: re.compile(
    rf"\b{re.escape(fn)}\b(?!\s*(?:IS|AS|RETURN)\b)",
    flags=re.IGNORECASE,
)


def _udf_supplemental_edges(
    procedure_segments: list[dict[str, Any]],
    analyses: list[dict[str, Any]],
    existing_edges: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """给调用 UDF 的 DML 补 source → target 边。

    示例：
        CREATE FUNCTION pkg.fn RETURN NUMBER IS BEGIN
          SELECT max(amt) INTO v FROM ods.txn; RETURN v;
        END;

        INSERT INTO dwd.summary (max_amt) VALUES (pkg.fn);

    INSERT 自身的 source_tables 是空。但 pkg.fn 函数体内读了 ods.txn —— 这条
    "通过 UDF 间接读"的依赖应被表达为 ods.txn → dwd.summary 边（confidence=
    medium / edge_type=UDF_CALL）。
    """
    if not procedure_segments or not analyses:
        return []

    # 1. 从 procedure_segments 的 FUNCTION 段提取 udf_reads 映射
    udf_reads: dict[str, list[str]] = {}
    for seg in procedure_segments:
        kind = (seg.get("procedure_kind") or "").upper()
        if kind != "FUNCTION":
            continue
        fn_name = (seg.get("procedure_name") or "").strip()
        if not fn_name:
            continue
        seg_sql = str(seg.get("sql") or "")
        if not seg_sql:
            continue
        # 函数体段通常是 SELECT INTO；用同一个 cursor 抽源表的 helper
        tables = _extract_select_tables(seg_sql)
        if not tables:
            continue
        bucket = udf_reads.setdefault(fn_name.lower(), [])
        for t in tables:
            if t not in bucket:
                bucket.append(t)

    if not udf_reads:
        return []

    # 2. 扫每个 statement SQL，看引用了哪些已知 UDF + DML target 是什么
    out: list[dict[str, Any]] = []
    existing: set[tuple[str, str]] = {
        (str(e.get("source_table") or "").lower(), str(e.get("target_table") or "").lower())
        for e in existing_edges
    }
    for stmt_idx, analysis in enumerate(analyses, start=1):
        stmt_sql = str(analysis.get("sql") or "")
        if not stmt_sql:
            continue
        # 找该 statement 的 DML target（INSERT/UPDATE/MERGE/DELETE）
        target_match = _RE_CURSOR_DML_TARGET.search(stmt_sql)
        if not target_match:
            continue
        target = target_match.group(1).strip().strip('"`[]')
        if not _is_valid_dml_target(target):
            continue
        # 遍历每个已知 UDF，看 statement 文本里是否引用
        for fn_name, src_tables in udf_reads.items():
            # 跳过 statement 本身就是 UDF 定义的情况
            if stmt_sql.upper().lstrip().startswith(("CREATE OR REPLACE FUNCTION", "CREATE FUNCTION")):
                continue
            pattern = _RE_FUNCTION_REF_FROM_NAME(fn_name)
            if not pattern.search(stmt_sql):
                continue
            for src in src_tables:
                key = (src.lower(), target.lower())
                if key in existing:
                    continue
                existing.add(key)
                out.append({
                    "source_table": src,
                    "target_table": target,
                    "statement_index": stmt_idx,
                    "edge_type": "UDF_CALL",
                    "source_columns": [],
                    "target_columns": [],
                    "confidence": "medium",
                    "reason": f"UDF read ({fn_name})",
                })
    return out


# S5 PR9：BULK COLLECT + FORALL ──────────────────────────────────────────

# `SELECT ... BULK COLLECT INTO v1, v2, ... FROM ods.orders` —— 抽 var 列表（到 FROM）
_RE_BULK_COLLECT = re.compile(
    r"\bBULK\s+COLLECT\s+INTO\s+([\w$#,\s]+?)\s+FROM\b",
    flags=re.IGNORECASE,
)
# `SELECT ... FROM tabA, tabB JOIN tabC ...` —— 复用 cursor 抽源表逻辑


def _bulk_collect_supplemental_edges(
    procedure_segments: list[dict[str, Any]],
    existing_edges: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Oracle BULK COLLECT + FORALL 模式：

        SELECT col1 BULK COLLECT INTO v_data FROM ods.orders;
        FORALL i IN 1..v_data.COUNT
          INSERT INTO dwd.fact (id) VALUES (v_data(i).col1);

    BULK COLLECT 把数据放入数组，FORALL 再批量插入。INSERT 的 VALUES 引用
    数组元素 v_data(i)，没 source_tables。这里建立 var → source_tables
    映射，看 INSERT/UPDATE/MERGE 段是否引用同名变量，补 supplemental 边。
    """
    if not procedure_segments:
        return []

    from app.lineage.segments import _extract_cursor_select_tables as _extract_tables

    # 1. 找所有 BULK COLLECT INTO <var,...> 段，抽 var → source_tables
    bulk_vars: dict[str, list[str]] = {}
    for seg in procedure_segments:
        seg_sql = str(seg.get("sql") or "")
        m = _RE_BULK_COLLECT.search(seg_sql)
        if not m:
            continue
        # 多变量 INTO v1, v2, v3 都拆开
        var_list = [v.strip().lower() for v in m.group(1).split(",")]
        tables = _extract_tables(seg_sql)
        if not tables:
            continue
        for var_name in var_list:
            if not var_name:
                continue
            bucket = bulk_vars.setdefault(var_name, [])
            for t in tables:
                if t not in bucket:
                    bucket.append(t)

    if not bulk_vars:
        return []

    # 2. 扫每段找 INSERT/UPDATE/MERGE/DELETE target + 引用了哪些 bulk var
    out: list[dict[str, Any]] = []
    existing: set[tuple[str, str]] = {
        (str(e.get("source_table") or "").lower(), str(e.get("target_table") or "").lower())
        for e in existing_edges
    }
    for seg in procedure_segments:
        seg_sql = str(seg.get("sql") or "")
        # 跳过 BULK COLLECT 自身段
        if _RE_BULK_COLLECT.search(seg_sql):
            continue
        # 找 DML target
        target_match = _RE_CURSOR_DML_TARGET.search(seg_sql)
        if not target_match:
            continue
        target = target_match.group(1).strip().strip('"`[]')
        if not _is_valid_dml_target(target):
            continue
        # 检查段引用了哪些 bulk var
        for var_name, src_tables in bulk_vars.items():
            # 匹配 v_data(i) 或 v_data(...)（带任意下标）
            ref_pattern = re.compile(rf"\b{re.escape(var_name)}\s*\(", flags=re.IGNORECASE)
            if not ref_pattern.search(seg_sql):
                continue
            for src in src_tables:
                key = (src.lower(), target.lower())
                if key in existing:
                    continue
                existing.add(key)
                out.append({
                    "source_table": src,
                    "target_table": target,
                    "statement_index": 0,
                    "edge_type": "BULK_COLLECT",
                    "source_columns": [],
                    "target_columns": [],
                    "confidence": "medium",
                    "reason": f"BULK COLLECT + FORALL ({var_name})",
                })
    return out


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
