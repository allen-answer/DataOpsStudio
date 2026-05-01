from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.lineage._common import is_alias_reference as _is_alias_reference
from app.lineage._common import normalize_table_name as _normalize_table_name
from app.lineage._common import raw_sql_aliases as _raw_sql_aliases
from app.lineage._common import unique_strings as _unique_strings
from app.lineage.analyzer import analyze_sql_lineage


@dataclass(frozen=True)
class ScriptInput:
    file_name: str
    sql: str


def analyze_lineage_batch(
    scripts: list[ScriptInput],
    dialect: str | None = None,
    schema: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    table_edges: list[dict[str, Any]] = []
    field_mappings: list[dict[str, Any]] = []
    warnings: list[dict[str, str]] = []

    for script in scripts:
        file_warnings = _script_warnings(script.sql)
        try:
            result = analyze_sql_lineage(script.sql, dialect, schema)
        except Exception as exc:
            files.append(
                {
                    "file_name": script.file_name,
                    "statement_count": 0,
                    "read_tables": [],
                    "write_tables": [],
                    "variables": [],
                    "status": "失败",
                    "error": str(exc),
                    "warnings": file_warnings,
                }
            )
            warnings.extend({"file_name": script.file_name, **item} for item in file_warnings)
            warnings.append({"file_name": script.file_name, "type": "解析失败", "message": str(exc)})
            continue

        aliases = set(result.get("aliases", [])) | _raw_sql_aliases(script.sql)
        read_tables = _unique_strings(
            item["table"] for item in result.get("tables", []) if not _is_alias_reference(item["table"], aliases)
        )
        write_tables = _unique_strings(item.get("target_table", "") for item in result.get("insert_mappings", []))
        variables = _unique_strings(item["name"] for item in result.get("variables", []))
        mappings = result.get("insert_mappings", [])
        file_warnings.extend(_lineage_warnings(result))

        files.append(
            {
                "file_name": script.file_name,
                "statement_count": result.get("statement_count", 0),
                "read_tables": read_tables,
                "write_tables": write_tables,
                "variables": variables,
                "status": "成功",
                "error": "",
                "warnings": file_warnings,
            }
        )
        warnings.extend({"file_name": script.file_name, **item} for item in file_warnings)

        for mapping in mappings:
            field_mappings.append({"file_name": script.file_name, **mapping})
            for source_table in mapping.get("source_tables", []):
                if _is_alias_reference(source_table, aliases):
                    continue
                table_edges.append(
                    {
                        "source_table": source_table,
                        "target_table": mapping.get("target_table", ""),
                        "edge_type": "字段来源",
                        "file_name": script.file_name,
                        "statement_index": mapping.get("statement_index", ""),
                    }
                )
        field_source_keys = {
            _normalize_table_name(source_table)
            for mapping in mappings
            for source_table in mapping.get("source_tables", [])
            if not _is_alias_reference(source_table, aliases)
        }
        for target_table in write_tables:
            for read_table in read_tables:
                if _normalize_table_name(read_table) in field_source_keys or _is_alias_reference(read_table, aliases):
                    continue
                table_edges.append(
                    {
                        "source_table": read_table,
                        "target_table": target_table,
                        "edge_type": "条件依赖",
                        "file_name": script.file_name,
                        "statement_index": "",
                    }
                )

    table_edges = _unique_table_edges(table_edges)
    table_groups = _table_groups(table_edges)
    script_edges = _script_edges(files)
    warnings.extend(_global_warnings(files))

    return {
        "file_count": len(scripts),
        "files": files,
        "table_edges": table_edges,
        "table_groups": table_groups,
        "script_edges": script_edges,
        "field_mappings": field_mappings,
        "impact_analysis": _impact_analysis(table_edges),
        "warnings": warnings,
        "summary": {
            "files": len(scripts),
            "success_files": sum(1 for item in files if item["status"] == "成功"),
            "failed_files": sum(1 for item in files if item["status"] != "成功"),
            "read_tables": len(_unique_strings(table for item in files for table in item["read_tables"])),
            "write_tables": len(_unique_strings(table for item in files for table in item["write_tables"])),
            "table_edges": len(table_edges),
            "script_edges": len(script_edges),
            "warnings": len(warnings),
        },
    }


def _impact_analysis(table_edges: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Maps each table to all downstream tables it transitively affects."""
    adjacency: dict[str, list[str]] = {}
    name_map: dict[str, str] = {}

    for edge in table_edges:
        src = edge.get("source_table", "")
        tgt = edge.get("target_table", "")
        if not src or not tgt:
            continue
        src_key = _normalize_table_name(src)
        tgt_key = _normalize_table_name(tgt)
        name_map.setdefault(src_key, src)
        name_map.setdefault(tgt_key, tgt)
        if tgt_key not in adjacency.setdefault(src_key, []):
            adjacency[src_key].append(tgt_key)

    result: dict[str, list[str]] = {}
    for start_key in adjacency:
        queue: list[str] = list(adjacency[start_key])
        seen: set[str] = {start_key} | set(queue)
        i = 0
        while i < len(queue):
            current = queue[i]
            i += 1
            for next_key in adjacency.get(current, []):
                if next_key not in seen:
                    seen.add(next_key)
                    queue.append(next_key)
        visited = queue  # queue IS the ordered BFS result
        if visited:
            result[name_map.get(start_key, start_key)] = [name_map.get(k, k) for k in visited]

    return result


def _script_edges(files: list[dict[str, Any]]) -> list[dict[str, str]]:
    edges: list[dict[str, str]] = []
    for producer in files:
        for table in producer["write_tables"]:
            for consumer in files:
                if producer["file_name"] == consumer["file_name"]:
                    continue
                if table in consumer["read_tables"]:
                    edges.append(
                        {
                            "producer_file": producer["file_name"],
                            "consumer_file": consumer["file_name"],
                            "table": table,
                        }
                    )
    return _unique_dicts(edges)


def _global_warnings(files: list[dict[str, Any]]) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    writers: dict[str, list[str]] = {}
    readers: dict[str, list[str]] = {}
    for item in files:
        for table in item["write_tables"]:
            writers.setdefault(table, []).append(item["file_name"])
        for table in item["read_tables"]:
            readers.setdefault(table, []).append(item["file_name"])

    for table, file_names in writers.items():
        if len(file_names) > 1:
            warnings.append(
                {
                    "file_name": "全局",
                    "type": "多脚本写同一目标表",
                    "message": f"{table}: {', '.join(file_names)}",
                }
            )
        if table not in readers:
            warnings.append({"file_name": "全局", "type": "最终产物", "message": table})

    for table in readers:
        if table not in writers:
            warnings.append({"file_name": "全局", "type": "外部源表", "message": table})
    return warnings


def _script_warnings(sql: str) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    if re.search(r"\bselect\s+\*", sql, re.IGNORECASE):
        warnings.append({"type": "SELECT *", "message": "存在 SELECT *，字段级血缘无法静态展开真实表字段"})
    if re.search(r"\b(execute\s+immediate|sp_executesql|exec\s*\(|dbms_sql)\b", sql, re.IGNORECASE):
        warnings.append({"type": "动态 SQL", "message": "存在疑似动态 SQL，静态血缘只能识别可解析的字面 SQL"})
    return warnings


def _lineage_warnings(result: dict[str, Any]) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    if not result.get("insert_mappings") and result.get("statement_count"):
        warnings.append({"type": "无落表映射", "message": "未识别到 INSERT INTO ... SELECT ... 落表字段映射"})
    return warnings


def _unique_table_edges(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for edge in edges:
        source_table = edge.get("source_table", "")
        target_table = edge.get("target_table", "")
        if not source_table or not target_table:
            continue
        key = (
            _normalize_table_name(source_table),
            _normalize_table_name(target_table),
            str(edge.get("edge_type", "")),
            str(edge.get("file_name", "")),
            str(edge.get("statement_index", "")),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(edge)
    return result


def _table_groups(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    by_target: dict[str, dict[str, Any]] = {}
    for edge in edges:
        target_table = edge["target_table"]
        target_key = _normalize_table_name(target_table)
        group = by_target.get(target_key)
        if group is None:
            group = {
                "target_table": target_table,
                "source_tables": [],
                "dependency_tables": [],
                "files": [],
                "_source_keys": set(),
                "_dependency_keys": set(),
                "_file_keys": set(),
            }
            by_target[target_key] = group
            groups.append(group)

        source_key = _normalize_table_name(edge["source_table"])
        if edge.get("edge_type") == "条件依赖":
            if source_key not in group["_source_keys"] and source_key not in group["_dependency_keys"]:
                group["_dependency_keys"].add(source_key)
                group["dependency_tables"].append(edge["source_table"])
        elif source_key not in group["_source_keys"]:
            group["_source_keys"].add(source_key)
            group["source_tables"].append(edge["source_table"])
            if source_key in group["_dependency_keys"]:
                group["_dependency_keys"].remove(source_key)
                group["dependency_tables"] = [
                    table for table in group["dependency_tables"] if _normalize_table_name(table) != source_key
                ]

        file_name = edge.get("file_name", "")
        if file_name and file_name not in group["_file_keys"]:
            group["_file_keys"].add(file_name)
            group["files"].append(file_name)

    for group in groups:
        group.pop("_source_keys", None)
        group.pop("_dependency_keys", None)
        group.pop("_file_keys", None)
    return groups


def _unique_dicts(items: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[tuple[str, str], ...]] = set()
    for item in items:
        key = tuple(sorted((str(k), str(v)) for k, v in item.items()))
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result
