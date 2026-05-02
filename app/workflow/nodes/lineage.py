"""lineage 节点：单 SQL 血缘分析包装。"""
from __future__ import annotations

from typing import Any


def run_lineage_node(config: dict[str, Any], variables: dict[str, str], **_: Any) -> dict[str, Any]:
    """Analyze a SQL string with the existing lineage_service.

    config: { sql: required, dialect?, schema?, schema_* (passthrough) }
    output: the analyze_json result dict — sources/targets/edges/warnings/etc.
    """
    from app.services.lineage_service import analyze_json

    sql = str(config.get("sql") or "").strip()
    if not sql:
        raise ValueError("lineage node requires config.sql")
    payload = {
        "sql": sql,
        "dialect": str(config.get("dialect") or ""),
        "schema": str(config.get("schema") or ""),
        "schema_datasource_id": str(config.get("schema_datasource_id") or ""),
        "schema_name": str(config.get("schema_name") or ""),
        "schema_table_filter": str(config.get("schema_table_filter") or ""),
        "schema_only_sql_tables": str(config.get("schema_only_sql_tables") or ""),
        "schema_dialect": str(config.get("schema_dialect") or ""),
    }
    return analyze_json(payload)
