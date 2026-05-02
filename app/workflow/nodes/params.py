"""params 节点：解析类型化参数，发出 {name: resolved_value} 作为节点输出。"""
from __future__ import annotations

from typing import Any


def run_params_node(config: dict[str, Any], variables: dict[str, str], **_: Any) -> dict[str, Any]:
    """Resolve a list of typed parameters and emit them as the node's output.

    Caller-supplied `variables` (passed to run_workflow) take precedence over
    each parameter's default — that's how runtime overrides bubble through.
    Downstream nodes can reference the resolved values via either
    ${nodes.<id>.<name>} or — if the node id is `params` — directly as a
    workflow variable when the engine merges them in (see run_workflow).
    """
    from datetime import date, timedelta

    today = date.today()
    out: dict[str, Any] = {}
    for param in config.get("parameters") or []:
        name = str(param.get("name") or "").strip()
        if not name:
            continue
        ptype = param.get("type") or "fixed"
        # Caller variable overrides the spec's default.
        if name in variables:
            out[name] = variables[name]
            continue
        if ptype == "fixed":
            out[name] = param.get("default", "")
        elif ptype == "date":
            out[name] = param.get("default") or today.isoformat()
        elif ptype == "relative_date":
            src = param.get("source", "today")
            if src == "today":           out[name] = today.isoformat()
            elif src == "yesterday":     out[name] = (today - timedelta(days=1)).isoformat()
            elif src == "last_month":    out[name] = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
            elif src == "now":
                from datetime import datetime
                out[name] = datetime.now().isoformat(timespec="seconds")
            else:
                out[name] = param.get("default", "")
        elif ptype == "multi_value":
            out[name] = param.get("default") or []
        elif ptype == "json":
            out[name] = param.get("default") or "{}"
        elif ptype == "sql_result":
            ds_id = str(param.get("datasource") or "").strip()
            sql = str(param.get("sql") or "").strip()
            if not ds_id or not sql:
                out[name] = []
                continue
            from app.dbclients.factory import fetch_rows
            from app.services.repositories import datasource_store
            ds = datasource_store.get(ds_id)
            if ds is None:
                raise ValueError(f"params node: datasource {ds_id!r} not found for parameter {name!r}")
            rows = fetch_rows(ds, sql, max_rows=10000)
            if rows:
                first_col = list(rows[0].keys())[0]
                out[name] = [row[first_col] for row in rows]
            else:
                out[name] = []
        else:
            out[name] = param.get("default", "")
    return out
