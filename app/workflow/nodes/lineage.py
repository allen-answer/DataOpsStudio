"""Workflow lineage node runner."""
from __future__ import annotations

import re
from typing import Any

_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def run_lineage_node(config: dict[str, Any], variables: dict[str, str], **_: Any) -> dict[str, Any]:
    """Analyze inline SQL or a persisted SQL/TXT/ZIP upload.

    Workflow configs are durable JSON, so uploaded files are represented by
    `script_path` returned from /api/uploads/lineage-script instead of a
    browser File object.
    """
    from app.services.lineage_service import analyze_json, analyze_stored_script

    mode = str(config.get("input_mode") or "").strip().lower()
    script_path = str(config.get("script_path") or config.get("file_path") or "").strip()
    sql = str(config.get("sql") or "").strip()
    if not mode:
        mode = "uploaded_file" if script_path else "inline_sql"

    payload = _payload(config)
    if mode in {"uploaded_file", "uploaded_zip", "file", "zip"}:
        if not script_path:
            raise ValueError("lineage node requires config.script_path")
        payload["script_path"] = script_path
        payload["script_filename"] = str(config.get("script_filename") or "")
        return _normalize_output(analyze_stored_script(payload))

    if mode != "inline_sql":
        raise ValueError(f"unsupported lineage input_mode: {mode}")
    if not sql:
        raise ValueError("lineage node requires config.sql")
    payload["sql"] = _interpolate_sql(sql, variables)
    return _normalize_output(analyze_json(payload))


def _payload(config: dict[str, Any]) -> dict[str, str]:
    return {
        "dialect": str(config.get("dialect") or ""),
        "schema": str(config.get("schema") or ""),
        "schema_datasource_id": str(config.get("schema_datasource_id") or ""),
        "schema_name": str(config.get("schema_name") or ""),
        "schema_table_filter": str(config.get("schema_table_filter") or ""),
        "schema_only_sql_tables": str(config.get("schema_only_sql_tables") or ""),
        "schema_dialect": str(config.get("schema_dialect") or ""),
        "ai_enabled": str(config.get("ai_enabled") or ""),
    }


def _interpolate_sql(sql: str, variables: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in variables:
            raise ValueError(f"unresolved variable in lineage sql: {name}")
        return str(variables[name])

    return _VAR_PATTERN.sub(replace, sql)


def _normalize_output(result: dict[str, Any]) -> dict[str, Any]:
    """Expose stable workflow datasets for both single and batch lineage."""
    if "graph_edges" in result and "edges" not in result:
        result["edges"] = result.get("graph_edges") or []
    if "graph_groups" in result and "groups" not in result:
        result["groups"] = result.get("graph_groups") or []
    if "table_edges" in result and "edges" not in result:
        result["edges"] = result.get("table_edges") or []
    if "table_groups" in result and "groups" not in result:
        result["groups"] = result.get("table_groups") or []
    if "field_mappings" in result and "insert_mappings" not in result:
        result["insert_mappings"] = result.get("field_mappings") or []
    if "sources" not in result or "targets" not in result:
        sources: list[str] = []
        targets: list[str] = []
        for edge in result.get("table_edges") or result.get("edges") or []:
            if not isinstance(edge, dict):
                continue
            src = str(edge.get("source_table") or edge.get("source") or "").strip()
            tgt = str(edge.get("target_table") or edge.get("target") or "").strip()
            if src and src not in sources:
                sources.append(src)
            if tgt and tgt not in targets:
                targets.append(tgt)
        result.setdefault("sources", sources)
        result.setdefault("targets", targets)
    return result
