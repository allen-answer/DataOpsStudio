"""LineageAnalysisReport —— 单/多脚本血缘分析的统一展示模型。

Phase 3 Track：用户提议把"单脚本血缘"和"多脚本分析"最终合并成"血缘分析工作台"。
本模块是第一步：建中间模型，让两边输出统一字段，前端用同一组组件。

设计原则：
1. 不修改既有字段——`to_lineage_report()` 只读 result，产出新字段 `report`
2. 单脚本 / 多脚本各自有 builder（结构相同，数据来源不同）
3. 任何字段缺失时返回空 list / None，调用方判空，不抛
4. 真实文件回归验证：a_cispnew_f3045.sql 单脚本仍出 88/39/delete_insert/dynamic_sql_count=0

LineageAnalysisReport schema：
{
  "scope":           "single" | "batch",
  "summary":         {input_count, output_count, process_step_count, ...},
  "inputs":          [{name, schema, basename, role, groups}, ...],
  "outputs":         [{name, schema, basename, role, refresh_mode, titles, groups}, ...],
  "process_steps":   [{file_name?, procedure_name, kind, segment_index, dml_keyword,
                       line_start, line_end, preceding_comment, parse_status}, ...],
  "table_edges":     [{source_table, target_table, ...}, ...],
  "column_edges":    [{source_table, source_column, target_table, target_column, ...}, ...],
  "semantic_lineage": dict | None,
  "impact_analysis": {downstream: {table: [tbl,...]}} | None,
  "risks":           [{level, type, message, file_name?}, ...],
  "files":           [{file_name, status, input_count, output_count, error?}, ...],
  "exports":         None,
}
"""
from __future__ import annotations

from typing import Any


def to_lineage_report(result: dict[str, Any], scope: str = "single") -> dict[str, Any]:
    """统一入口：根据 scope 派发到单/多脚本 builder。

    入参为 analyze_sql_lineage / analyze_lineage_batch 的完整 result dict。
    """
    if scope == "batch":
        return _build_batch_report(result)
    return _build_single_report(result)


# ─── Single script ────────────────────────────────────────────────────────────


def _build_single_report(result: dict[str, Any]) -> dict[str, Any]:
    """单脚本：从 analyze_sql_lineage 的 result 抽出统一报告。"""
    table_roles = result.get("table_roles", []) or []
    target_summary = result.get("target_summary", []) or []
    semantic = result.get("semantic_lineage", {}) or {}

    # 输出表名集合（用 lower 去重，保留原 case）—— 单脚本"输出"= target_summary 里的目标表
    output_names = {(t.get("target_table", "") or "").lower() for t in target_summary}

    # 输入：table_roles 中所有非纯 target 的（含 source_fact / dimension / reference / config / ...）
    inputs: list[dict[str, Any]] = []
    outputs: list[dict[str, Any]] = []
    for role_entry in table_roles:
        name = role_entry.get("table", "") or ""
        if not name:
            continue
        schema, basename = _split_schema_basename(name)
        roles = role_entry.get("roles", []) or []
        primary_role = role_entry.get("primary_role", "") or ""
        is_output = name.lower() in output_names
        groups = _groups_for_table(name, semantic.get("business_groups", []))
        if is_output:
            target = next(
                (t for t in target_summary if (t.get("target_table", "") or "").lower() == name.lower()),
                {},
            )
            outputs.append({
                "name": name,
                "schema": schema,
                "basename": basename,
                "primary_role": primary_role,
                "roles": roles,
                "refresh_mode": target.get("refresh_mode"),
                "titles": target.get("titles", []) or [],
                "groups": groups,
                "counts": {
                    "insert": target.get("insert_count", 0),
                    "update": target.get("update_count", 0),
                    "merge":  target.get("merge_count", 0),
                    "delete": target.get("delete_count", 0),
                    "truncate": target.get("truncate_count", 0),
                },
            })
        else:
            inputs.append({
                "name": name,
                "schema": schema,
                "basename": basename,
                "primary_role": primary_role,
                "roles": roles,
                "groups": groups,
            })

    process_steps = _process_steps_from_segments(result.get("procedure_segments", []) or [])
    table_edges = list(result.get("graph_edges", []) or [])
    column_edges = _column_edges_from_insert_mappings(result.get("insert_mappings", []) or [])
    risks = _normalize_risks(semantic.get("risks", []) or [])

    has_parse_error = bool(result.get("parse_errors"))
    files = [{
        "file_name": "",
        "status": "失败" if has_parse_error else "成功",
        "input_count": len(inputs),
        "output_count": len(outputs),
        "error": (result.get("parse_errors") or [{}])[0].get("error", "") if has_parse_error else "",
    }]

    return {
        "scope": "single",
        "summary": {
            "input_count": len(inputs),
            "output_count": len(outputs),
            "process_step_count": len(process_steps),
            "table_edge_count": len(table_edges),
            "column_edge_count": len(column_edges),
            "risk_count": len(risks),
            "file_count": 1,
            "success_count": 0 if has_parse_error else 1,
            "dynamic_sql_count": result.get("dynamic_sql_count", 0),
        },
        "inputs": inputs,
        "outputs": outputs,
        "process_steps": process_steps,
        "table_edges": table_edges,
        "column_edges": column_edges,
        "semantic_lineage": semantic or None,
        "impact_analysis": _impact_from_edges(table_edges),
        "risks": risks,
        "files": files,
        "exports": None,
    }


# ─── Batch ────────────────────────────────────────────────────────────────────


def _build_batch_report(result: dict[str, Any]) -> dict[str, Any]:
    """多脚本：从 analyze_lineage_batch 的 result 抽出统一报告。

    没有 procedure_segments / semantic_lineage —— batch 走的是文件粒度聚合，
    process_steps 暂为空（后续可从每个文件的子 result 累加）。
    """
    files_in = result.get("files", []) or []
    table_edges = list(result.get("table_edges", []) or [])
    field_mappings = result.get("field_mappings", []) or []
    summary_in = result.get("summary", {}) or {}

    # 输入 = 所有 read_tables 去重后没出现在 write_tables 的；输出 = 任意文件的 write_tables 去重
    write_set: set[str] = set()
    read_set: set[str] = set()
    for f in files_in:
        for t in f.get("write_tables", []) or []:
            write_set.add(t.lower())
        for t in f.get("read_tables", []) or []:
            read_set.add(t.lower())

    inputs: list[dict[str, Any]] = []
    outputs: list[dict[str, Any]] = []
    seen_in: set[str] = set()
    seen_out: set[str] = set()
    for f in files_in:
        for t in f.get("read_tables", []) or []:
            k = t.lower()
            if k in write_set or k in seen_in:
                continue
            seen_in.add(k)
            schema, basename = _split_schema_basename(t)
            inputs.append({
                "name": t, "schema": schema, "basename": basename,
                "primary_role": "source_fact", "roles": ["source_fact"], "groups": [],
            })
        for t in f.get("write_tables", []) or []:
            k = t.lower()
            if k in seen_out:
                continue
            seen_out.add(k)
            schema, basename = _split_schema_basename(t)
            # 既出现在 read 又出现在 write —— intermediate
            primary = "intermediate" if k in read_set else "target"
            outputs.append({
                "name": t, "schema": schema, "basename": basename,
                "primary_role": primary, "roles": [primary], "groups": [],
                "refresh_mode": None, "titles": [],
                "counts": {"insert": 0, "update": 0, "merge": 0, "delete": 0, "truncate": 0},
            })

    column_edges = _column_edges_from_field_mappings(field_mappings)
    risks = _risks_from_warnings(result.get("warnings", []) or [])

    files_out = [
        {
            "file_name": f.get("file_name", ""),
            "status": f.get("status", ""),
            "input_count": len(f.get("read_tables", []) or []),
            "output_count": len(f.get("write_tables", []) or []),
            "error": f.get("error", ""),
        }
        for f in files_in
    ]

    return {
        "scope": "batch",
        "summary": {
            "input_count": len(inputs),
            "output_count": len(outputs),
            "process_step_count": 0,  # batch 暂未做 step 累加
            "table_edge_count": len(table_edges),
            "column_edge_count": len(column_edges),
            "risk_count": len(risks),
            "file_count": summary_in.get("files", len(files_in)),
            "success_count": summary_in.get("success_files", 0),
            "dynamic_sql_count": 0,
        },
        "inputs": inputs,
        "outputs": outputs,
        "process_steps": [],
        "table_edges": table_edges,
        "column_edges": column_edges,
        "semantic_lineage": None,
        "impact_analysis": {"downstream": result.get("impact_analysis", {}) or {}},
        "risks": risks,
        "files": files_out,
        "exports": None,
    }


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _split_schema_basename(name: str) -> tuple[str, str]:
    """`a.b.c` → ('a.b', 'c')；`tbl` → ('', 'tbl')；剥 `@dblink`。"""
    if not name:
        return "", ""
    cleaned = name.split("@", 1)[0]
    if "." in cleaned:
        schema, _, basename = cleaned.rpartition(".")
        return schema, basename
    return "", cleaned


def _groups_for_table(name: str, business_groups: list[dict[str, Any]]) -> list[str]:
    """semantic_lineage.business_groups 中包含此表的所有组名（保序）。"""
    out: list[str] = []
    for g in business_groups:
        tables = [t.lower() for t in (g.get("tables", []) or [])]
        if name.lower() in tables:
            out.append(g.get("name", ""))
    return [n for n in out if n]


def _process_steps_from_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """procedure_segments → process_steps（保留 dml_keyword 给前端着色用）。"""
    import re
    steps: list[dict[str, Any]] = []
    for seg in segments:
        sql = seg.get("sql", "") or ""
        cleaned = re.sub(r"/\*(?:[^*]|\*(?!/))*\*/", " ", sql, flags=re.DOTALL)
        cleaned = re.sub(r"--[^\n]*", " ", cleaned).strip().upper()
        kw = ""
        for candidate in ("INSERT", "UPDATE", "DELETE", "MERGE", "TRUNCATE", "SELECT", "WITH", "REPLACE", "CREATE"):
            if cleaned.startswith(candidate):
                kw = candidate
                break
        steps.append({
            "file_name": seg.get("file_name", "") or "",
            "procedure_name": seg.get("procedure_name", "") or "",
            "kind": seg.get("procedure_kind", "") or "",
            "segment_index": seg.get("segment_index", ""),
            "dml_keyword": kw,
            "line_start": seg.get("line_start"),
            "line_end": seg.get("line_end"),
            "preceding_comment": seg.get("preceding_comment", "") or "",
            "parse_status": seg.get("parse_status", "unknown") or "unknown",
        })
    return steps


def _column_edges_from_insert_mappings(insert_mappings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """单脚本的 insert_mappings → column_edges 的扁平展开。

    一条 insert_mapping 可能有多个 source_columns —— 每个产出一条 column_edge。
    """
    edges: list[dict[str, Any]] = []
    for m in insert_mappings:
        target_table = m.get("target_table", "") or ""
        target_column = m.get("target_column", "") or ""
        if not target_table or not target_column:
            continue
        sources = m.get("source_columns", []) or []
        if not sources:
            edges.append({
                "source_table": "", "source_column": "",
                "target_table": target_table, "target_column": target_column,
                "transform": m.get("expression", "") or m.get("transform", ""),
                "confidence": m.get("confidence", "high"),
                "statement_index": m.get("statement_index", ""),
            })
            continue
        for src in sources:
            # source_columns 可能是 ["t.col"] 或 ["col"] 形式；不强行拆 schema/table，前端再处理
            edges.append({
                "source_table": "", "source_column": src,
                "target_table": target_table, "target_column": target_column,
                "transform": m.get("expression", "") or m.get("transform", ""),
                "confidence": m.get("confidence", "high"),
                "statement_index": m.get("statement_index", ""),
            })
    return edges


def _column_edges_from_field_mappings(field_mappings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """多脚本：field_mappings 里每条已经是 1 to 1 的 column 关系。"""
    edges: list[dict[str, Any]] = []
    for m in field_mappings:
        edges.append({
            "source_table": (m.get("source_tables") or [""])[0],
            "source_column": (m.get("source_columns") or [""])[0],
            "target_table": m.get("target_table", "") or "",
            "target_column": m.get("target_column", "") or "",
            "transform": m.get("expression", "") or m.get("transform", ""),
            "confidence": m.get("confidence", "high"),
            "file_name": m.get("file_name", "") or "",
            "statement_index": m.get("statement_index", ""),
        })
    return edges


def _normalize_risks(risks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """risks 可能来自 semantic_lineage —— 只保留 level/type/message 三元组。"""
    out: list[dict[str, Any]] = []
    for r in risks:
        out.append({
            "level": r.get("level", "low") or "low",
            "type": r.get("type", "") or "",
            "message": r.get("message", "") or "",
        })
    return out


def _risks_from_warnings(warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """多脚本的 warnings 转 risks。warning type 映射到 level。"""
    LEVEL_MAP = {
        "解析失败": "high",
        "动态 SQL": "medium",
        "环依赖": "high",
        "写冲突": "high",
    }
    out: list[dict[str, Any]] = []
    for w in warnings:
        wtype = w.get("type", "") or ""
        out.append({
            "level": LEVEL_MAP.get(wtype, "low"),
            "type": wtype,
            "message": w.get("message", "") or "",
            "file_name": w.get("file_name", "") or "",
        })
    return out


def _impact_from_edges(table_edges: list[dict[str, Any]]) -> dict[str, Any]:
    """单脚本：从 graph_edges 用 BFS 推每张表的下游传递闭包。"""
    adj: dict[str, list[str]] = {}
    for e in table_edges:
        s = (e.get("source_table") or e.get("source") or "").lower()
        t = (e.get("target_table") or e.get("target") or "").lower()
        if not s or not t:
            continue
        adj.setdefault(s, [])
        if t not in adj[s]:
            adj[s].append(t)
    downstream: dict[str, list[str]] = {}
    for start in list(adj.keys()):
        seen = set([start])
        queue = list(adj[start])
        i = 0
        while i < len(queue):
            cur = queue[i]
            i += 1
            seen.add(cur)
            for nxt in adj.get(cur, []):
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        if queue:
            downstream[start] = queue
    return {"downstream": downstream}
