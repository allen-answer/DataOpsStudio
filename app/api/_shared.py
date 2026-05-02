"""跨多个 api 子模块共用的校验 helper。

放在私有模块（`_shared`）下表明它不是对外 API 的一部分；其它子模块从这里
import 而不是循环 import 业务模块。
"""
from __future__ import annotations

import re

from fastapi import HTTPException

from app.models import CompareTaskCreate, SourceKind, WorkflowCreate
from app.services.repositories import datasource_store, task_store
from app.services.workflow_engine import topological_order, validate_when_syntax
from app.utils.sql_guard import validate_readonly_sql


def ensure_datasources_for_kind(payload: CompareTaskCreate) -> None:
    """Datasource existence only matters for SQL-kind sides; Excel sides
    persist file paths and don't need a registered datasource."""
    if payload.source_kind == SourceKind.SQL and datasource_store.get(payload.source_id) is None:
        raise HTTPException(status_code=400, detail="source_id does not exist")
    if payload.target_kind == SourceKind.SQL and datasource_store.get(payload.target_id) is None:
        raise HTTPException(status_code=400, detail="target_id does not exist")


def ensure_workflow_node_targets(payload: WorkflowCreate) -> None:
    """Validate per-type config + the DAG is well-formed. Catching this at
    create time gives a clear 400 instead of a confusing failure mid-run."""
    for node in payload.nodes:
        # `when` 表达式语法预检：空字符串放行；非空但坏（如空 LHS：
        # ` == "prod"`）直接拒掉，不留到运行时才报错。
        try:
            validate_when_syntax(node.when or "")
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"node {node.id}: {exc}",
            ) from exc

        kind = node.type.value
        if kind == "params":
            params = node.config.get("parameters") or []
            if not isinstance(params, list):
                raise HTTPException(status_code=400, detail=f"node {node.id}: params 节点的 parameters 必须是数组")
            for p in params:
                if not isinstance(p, dict) or not str(p.get("name") or "").strip():
                    raise HTTPException(status_code=400, detail=f"node {node.id}: 每个参数必须有 name")
        elif kind == "compare":
            task_id = str(node.config.get("task_id") or "").strip()
            if not task_id:
                raise HTTPException(status_code=400, detail=f"node {node.id}: compare requires config.task_id")
            if task_store.get(task_id) is None:
                raise HTTPException(status_code=400, detail=f"node {node.id}: task {task_id} does not exist")
            # Override 必须是完整的 SELECT/WITH 查询，不是 WHERE 片段。
            # 把 ${...} 占位先替成 __var__ 再过一遍 sql_guard，提前拒掉
            # 「id=${user_id}」这种半句 SQL，避免运行时才报错。
            for override_field in ("source_sql_override", "target_sql_override"):
                override = node.config.get(override_field)
                if isinstance(override, str) and override.strip():
                    stripped = re.sub(r"\$\{[^}]+\}", "__var__", override)
                    try:
                        validate_readonly_sql(stripped)
                    except ValueError as exc:
                        raise HTTPException(
                            status_code=400,
                            detail=(
                                f"node {node.id}: {override_field} 必须是完整的 SELECT/WITH 查询，"
                                f"不能只填 WHERE 片段（{exc}）"
                            ),
                        ) from exc
        elif kind == "lineage":
            if not str(node.config.get("sql") or "").strip():
                raise HTTPException(status_code=400, detail=f"node {node.id}: lineage requires config.sql")
        elif kind == "http":
            url = str(node.config.get("url") or "").strip()
            if not url:
                raise HTTPException(status_code=400, detail=f"node {node.id}: http requires config.url")
            if not (url.startswith("http://") or url.startswith("https://")):
                raise HTTPException(status_code=400, detail=f"node {node.id}: http url must start with http:// or https://")
        elif kind == "excel_export":
            sheets = node.config.get("sheets") or []
            if not isinstance(sheets, list) or not [s for s in sheets if s.get("enabled", True)]:
                raise HTTPException(status_code=400, detail=f"node {node.id}: excel_export 至少需要一个启用的 Sheet")
    try:
        topological_order(payload.nodes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def coerce_string_dict(value: object | None) -> dict[str, str]:
    """Body 里 variables 字段：放宽接受 dict[str, Any]，强转每个值为 str。
    None / 非 dict 都视为空字典。"""
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items()}
