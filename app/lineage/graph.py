from __future__ import annotations

from typing import Any

from app.lineage._common import (
    is_alias_reference,
    normalize_table_name,
    unique_strings,
    weaker_confidence,
)


def graph_edges(analyses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    by_key: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for statement_index, analysis in enumerate(analyses, start=1):
        aliases = set(analysis.get("aliases", []))
        for mapping in analysis.get("insert_mappings", []):
            for source_table in mapping.get("source_tables", []):
                target_table = mapping.get("target_table", "")
                if is_alias_reference(source_table, aliases):
                    continue
                key = (
                    normalize_table_name(source_table),
                    normalize_table_name(target_table),
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
                edge["source_columns"] = unique_strings(edge["source_columns"] + mapping.get("source_columns", []))
                target_column = mapping.get("target_column", "")
                if target_column:
                    edge["target_columns"] = unique_strings(edge["target_columns"] + [target_column])
                edge["confidence"] = weaker_confidence(edge["confidence"], mapping.get("confidence", "high"))
                reason = mapping.get("expression") or mapping.get("transform", "")
                if reason:
                    edge["reason"] = "; ".join(unique_strings([part for part in [edge["reason"], reason] if part]))
    return edges


def graph_groups(edges: list[dict[str, Any]], analyses: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    by_target: dict[str, dict[str, Any]] = {}
    for edge in edges:
        target_table = edge["target_table"]
        target_key = normalize_table_name(target_table)
        group = by_target.get(target_key)
        if group is None:
            group = {"target_table": target_table, "source_tables": [], "dependency_tables": [], "_source_keys": set(), "_dependency_keys": set()}
            by_target[target_key] = group
            groups.append(group)
        source_key = normalize_table_name(edge["source_table"])
        if source_key in group["_source_keys"]:
            continue
        group["_source_keys"].add(source_key)
        group["source_tables"].append(edge["source_table"])

    for analysis in analyses or []:
        aliases = set(analysis.get("aliases", []))
        target_tables = unique_strings(mapping.get("target_table", "") for mapping in analysis.get("insert_mappings", []))
        field_source_keys = {
            normalize_table_name(source_table)
            for mapping in analysis.get("insert_mappings", [])
            for source_table in mapping.get("source_tables", [])
            if not is_alias_reference(source_table, aliases)
        }
        dependency_tables = [
            table["table"]
            for table in analysis.get("tables", [])
            if normalize_table_name(table["table"]) not in field_source_keys
            and not is_alias_reference(table["table"], aliases)
        ]
        for target_table in target_tables:
            target_key = normalize_table_name(target_table)
            group = by_target.get(target_key)
            if group is None:
                group = {"target_table": target_table, "source_tables": [], "dependency_tables": [], "_source_keys": set(), "_dependency_keys": set()}
                by_target[target_key] = group
                groups.append(group)
            for dependency_table in dependency_tables:
                dependency_key = normalize_table_name(dependency_table)
                if dependency_key in group["_source_keys"] or dependency_key in group["_dependency_keys"]:
                    continue
                group["_dependency_keys"].add(dependency_key)
                group["dependency_tables"].append(dependency_table)

    for group in groups:
        group.pop("_source_keys", None)
        group.pop("_dependency_keys", None)
    return groups
