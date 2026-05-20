"""跨多个 api 子模块共用的校验 helper。

放在私有模块（`_shared`）下表明它不是对外 API 的一部分；其它子模块从这里
import 而不是循环 import 业务模块。
"""
from __future__ import annotations

import re

from fastapi import HTTPException

from app.api._authz import can_access_project
from app.models import CompareTaskCreate, SourceKind, User, Workflow, WorkflowCreate
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
            mode = str(node.config.get("input_mode") or "").strip().lower()
            script_path = str(node.config.get("script_path") or node.config.get("file_path") or "").strip()
            sql = str(node.config.get("sql") or "").strip()
            if not mode:
                mode = "uploaded_file" if script_path else "inline_sql"
            if mode == "inline_sql":
                if not sql:
                    raise HTTPException(status_code=400, detail=f"node {node.id}: lineage requires config.sql")
            elif mode in {"uploaded_file", "uploaded_zip", "file", "zip"}:
                if not script_path:
                    raise HTTPException(status_code=400, detail=f"node {node.id}: lineage requires config.script_path")
            else:
                raise HTTPException(status_code=400, detail=f"node {node.id}: unsupported lineage input_mode {mode}")
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


# ─── 引用资源授权（见 docs/PROJECT_AUTHORIZATION.md「引用资源授权」）───────────
# 外壳资源（task / workflow）的项目权限在 endpoint 层用 _authz.require_project_access
# 校验；下面这组函数补上「内部引用资源」的校验 —— task 引用的 datasource、
# workflow compare 节点引用的 task —— 防止「有权改外壳 → 间接引用无权项目的
# 资源」这种越权（外壳过了校验，但内部引用没过）。


def ensure_datasources_for_kind_authorized(
    payload: CompareTaskCreate, current_user: User
) -> None:
    """在 ensure_datasources_for_kind 存在性校验之上，再校验：
    - 当前用户能访问被引用 datasource 所属项目（否则 403）
    - task 归属某项目时，datasource 必须同项目或为全局资源（否则 403）—— 防止
      ProjectA 的对比任务引用 ProjectB 的数据源，被 ProjectA 成员间接 run。

    create_task / update_task 用这个替代裸 ensure_datasources_for_kind。
    """
    ensure_datasources_for_kind(payload)  # 先做存在性校验（缺失 → 400）
    task_project = payload.project_id or ""
    sides: list[tuple[str, str]] = []
    if payload.source_kind == SourceKind.SQL:
        sides.append(("source", payload.source_id))
    if payload.target_kind == SourceKind.SQL:
        sides.append(("target", payload.target_id))
    for side, datasource_id in sides:
        datasource = datasource_store.get(datasource_id)
        if datasource is None:
            continue  # 存在性已由上面保证；防御性跳过
        ds_project = datasource.project_id or ""
        if not can_access_project(current_user, ds_project):
            raise HTTPException(
                status_code=403,
                detail=f"无权引用 {side} 数据源 —— 它属于你无权访问的项目",
            )
        # task 归属某项目时，datasource 必须同项目或全局（结构一致性，对所有
        # 角色生效 —— 否则 admin 建的跨项目引用会成为他人越权的跳板）。
        if task_project and ds_project and ds_project != task_project:
            raise HTTPException(
                status_code=403,
                detail=f"{side} 数据源属于其它项目，不能被本项目的对比任务引用",
            )


def authorize_workflow_compare_tasks(
    payload: WorkflowCreate | Workflow, current_user: User
) -> None:
    """校验 workflow 里 compare 节点引用的 task —— 只做引用授权，不做结构校验：
    - 当前用户能访问被引用 task 所属项目（否则 403）
    - workflow 归属某项目时，task 必须同项目或为全局 task（否则 403）

    run workflow 走这个轻量版本：结构在 create 时已校验，run 时不重复校验，
    也不因 task 已被删而 400（那留给执行层处理）。
    """
    workflow_project = payload.project_id or ""
    for node in payload.nodes:
        if node.type.value != "compare":
            continue
        task_id = str(node.config.get("task_id") or "").strip()
        if not task_id:
            continue
        task = task_store.get(task_id)
        if task is None:
            continue  # 存在性不在这里管
        task_project = task.project_id or ""
        if not can_access_project(current_user, task_project):
            raise HTTPException(
                status_code=403,
                detail=f"node {node.id}: 无权引用 task {task_id} —— 它属于你无权访问的项目",
            )
        if workflow_project and task_project and task_project != workflow_project:
            raise HTTPException(
                status_code=403,
                detail=f"node {node.id}: compare task 属于其它项目，不能被本项目的作业流引用",
            )


def ensure_workflow_node_targets_authorized(
    payload: WorkflowCreate, current_user: User
) -> None:
    """结构 + 存在性校验（ensure_workflow_node_targets）+ compare 节点引用 task
    授权校验。create_workflow / update_workflow / 模板实例化 / 存模板用这个。
    """
    ensure_workflow_node_targets(payload)
    authorize_workflow_compare_tasks(payload, current_user)
