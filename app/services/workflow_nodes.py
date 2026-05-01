"""Workflow node runners. A runner takes the resolved (variable-interpolated)
node config plus the live variables dict and returns a JSON-serializable output.

To register a new node type:
    1. Add a value to WorkflowNodeType in app/models.py
    2. Implement a runner function here with signature (config, variables) -> dict
    3. Register it in NODE_RUNNERS below
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable
from urllib.parse import urlparse

from app.models import WorkflowNodeType


NodeRunner = Callable[[dict[str, Any], dict[str, str]], dict[str, Any]]


def run_compare_node(config: dict[str, Any], variables: dict[str, str]) -> dict[str, Any]:
    """Run an existing CompareTask by id and return its CompareResult."""
    # Imported lazily so unit tests for the engine don't drag the whole
    # compare runtime (DB drivers, exporter, etc.) into the import graph.
    from app.services.runner import run_task

    task_id = str(config.get("task_id") or "").strip()
    if not task_id:
        raise ValueError("compare node requires config.task_id")
    result = run_task(task_id)
    return result.model_dump(mode="json")


def run_lineage_node(config: dict[str, Any], variables: dict[str, str]) -> dict[str, Any]:
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


_HTTP_RESPONSE_BYTE_CAP = 256 * 1024     # 256 KB — enough for webhook responses, blocks log dumps


def run_http_node(config: dict[str, Any], variables: dict[str, str]) -> dict[str, Any]:
    """Issue an HTTP request and return { status, body, headers }.

    config: { url: required, method? (default GET), headers? (dict),
              body? (string), timeout_seconds? (default 30),
              expect_status? (int — fail node if response status differs) }
    """
    url = str(config.get("url") or "").strip()
    if not url:
        raise ValueError("http node requires config.url")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"http node only supports http(s) urls, got {parsed.scheme!r}")
    method = str(config.get("method") or "GET").upper()
    headers = config.get("headers") or {}
    if not isinstance(headers, dict):
        raise ValueError("http node config.headers must be an object")
    body_text = config.get("body")
    body_bytes = body_text.encode("utf-8") if isinstance(body_text, str) and body_text else None
    timeout = float(config.get("timeout_seconds") or 30)

    request = urllib.request.Request(
        url,
        data=body_bytes,
        method=method,
        headers={str(k): str(v) for k, v in headers.items()},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(_HTTP_RESPONSE_BYTE_CAP + 1)
            truncated = len(raw) > _HTTP_RESPONSE_BYTE_CAP
            body = raw[:_HTTP_RESPONSE_BYTE_CAP].decode("utf-8", errors="replace")
            status = response.status
            response_headers = dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        # Non-2xx still reaches us as HTTPError. Surface body so users can debug.
        body_bytes_err = exc.read(_HTTP_RESPONSE_BYTE_CAP) if hasattr(exc, "read") else b""
        return {
            "status": exc.code,
            "body": body_bytes_err.decode("utf-8", errors="replace"),
            "headers": dict(exc.headers.items()) if exc.headers else {},
            "error": str(exc),
        }

    expect_status = config.get("expect_status")
    if expect_status is not None and int(expect_status) != status:
        raise ValueError(f"http node expected status {expect_status}, got {status}")

    parsed_json: Any = None
    if body and body.lstrip().startswith(("{", "[")):
        try:
            parsed_json = json.loads(body)
        except (ValueError, TypeError):
            parsed_json = None

    return {
        "status": status,
        "body": body,
        "json": parsed_json,
        "headers": response_headers,
        "truncated": truncated,
    }


def run_excel_export_node(config: dict[str, Any], variables: dict[str, str]) -> dict[str, Any]:
    """Build a multi-sheet Excel report from upstream compare/lineage outputs.

    First-version behavior: validate the config and emit a stub result
    describing what *would* have been written. Real Excel writing reads
    upstream node outputs (referenced via ${nodes.x.y} in user config)
    and lays them into separate sheets. Wiring that into the existing
    exporter machinery lands in a follow-up slice.
    """
    filename = str(config.get("filename") or "").strip() or "export.xlsx"
    sheets = config.get("sheets") or []
    if not isinstance(sheets, list):
        raise ValueError("excel_export node config.sheets must be a list")
    enabled = [s for s in sheets if s.get("enabled", True)]
    if not enabled:
        raise ValueError("excel_export node requires at least one enabled sheet")
    # Variables / upstream node refs are already substituted by the engine
    # before this runner sees `config`, so filename + sheet_name come in
    # already-resolved.
    return {
        "filename": filename,
        "sheet_count": len(enabled),
        "sheets": [
            {
                "name": s.get("sheet_name") or s.get("id") or f"Sheet{i+1}",
                "source": s.get("source"),
                "max_rows": s.get("max_rows"),
                "rows_written": 0,   # stub — real run would populate from upstream output
            }
            for i, s in enumerate(enabled)
        ],
        "_stub": True,
    }


NODE_RUNNERS: dict[WorkflowNodeType, NodeRunner] = {
    WorkflowNodeType.COMPARE:      run_compare_node,
    WorkflowNodeType.LINEAGE:      run_lineage_node,
    WorkflowNodeType.HTTP:         run_http_node,
    WorkflowNodeType.EXCEL_EXPORT: run_excel_export_node,
}
