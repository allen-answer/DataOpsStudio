"""作业流运行历史：list / get / delete / rerun。"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException

from app.api._shared import coerce_string_dict
from app.models import (
    JobInfo,
    NodeRunStatus,
    OkResponse,
    WorkflowRun,
    WorkflowRunSummary,
)
from app.services.auth import get_current_user, require_role
from app.services.jobs import submit_workflow_run
from app.services.openlineage_emitter import build_workflow_run_events, emit_workflow_run_openlineage
from app.services.repositories import workflow_store
from app.services.workflow_engine import transitive_ancestors
from app.services.workflow_history import (
    delete_workflow_run, get_workflow_run, list_workflow_runs, persist_workflow_run,
)


# router 级 default：viewer 读历史；rerun / delete / emit 升级 editor。
router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get("/api/workflow-runs", response_model=list[WorkflowRunSummary])
def list_all_workflow_runs_api(limit: int = 200):
    return list_workflow_runs("", limit=limit)


@router.get("/api/workflow-runs/{run_id}", response_model=WorkflowRun)
def get_workflow_run_api(run_id: str):
    run = get_workflow_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    return run


@router.get("/api/workflow-runs/{run_id}/openlineage")
def get_workflow_run_openlineage_api(run_id: str):
    run_data = get_workflow_run(run_id)
    if run_data is None:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    run = WorkflowRun.model_validate(run_data)
    workflow = workflow_store.get(run.workflow_id)
    return {"events": build_workflow_run_events(run, workflow)}


@router.post("/api/workflow-runs/{run_id}/openlineage/emit")
def emit_workflow_run_openlineage_api(run_id: str, _: object = Depends(require_role("editor"))):
    run_data = get_workflow_run(run_id)
    if run_data is None:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    run = WorkflowRun.model_validate(run_data)
    workflow = workflow_store.get(run.workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    results = emit_workflow_run_openlineage(workflow, run, trigger="manual_reemit")
    run.integrations["openlineage"] = results
    persist_workflow_run(run)
    return {"ok": all(item.get("ok") for item in results) if results else False, "results": results}


@router.delete("/api/workflow-runs/{run_id}", response_model=OkResponse)
def delete_workflow_run_api(run_id: str, _: object = Depends(require_role("editor"))):
    try:
        delete_workflow_run(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Workflow run not found") from exc
    return {"ok": True}


@router.post("/api/workflow-runs/{run_id}/rerun", response_model=JobInfo)
def rerun_workflow_run_api(
    run_id: str,
    payload: dict[str, object] | None = Body(None),
    current: object = Depends(require_role("editor")),
):
    """从指定节点重跑：上次 run 的 from_node_id 及其所有传递下游重新执行；
    其他节点（必然是 from_node 的祖先 + 旁支）若上次 success 则复用 output.

    Body: {"from_node_id": str, "variables": {...}?}
    Variables 缺省沿用上次 run 的（剥掉 today/now 等内置时间变量）。
    """
    payload = payload or {}
    from_node_id = str(payload.get("from_node_id") or "").strip()
    if not from_node_id:
        raise HTTPException(status_code=400, detail="from_node_id 不能为空")

    previous_run_data = get_workflow_run(run_id)
    if previous_run_data is None:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    previous_run = WorkflowRun.model_validate(previous_run_data)

    workflow = workflow_store.get(previous_run.workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="对应的 Workflow 已不存在，无法重跑")

    node_ids = {node.id for node in workflow.nodes}
    if from_node_id not in node_ids:
        raise HTTPException(
            status_code=400,
            detail=f"from_node_id {from_node_id!r} 不在当前 workflow 节点列表里",
        )

    # 上一次 run 里 from_node 的所有祖先必须是 success——否则重跑会拿不到上游 output。
    ancestors = transitive_ancestors(workflow.nodes, from_node_id)
    previous_status = {nr.node_id: nr.status for nr in previous_run.nodes}
    bad_ancestors = [
        anc for anc in ancestors
        if previous_status.get(anc) != NodeRunStatus.SUCCESS
    ]
    if bad_ancestors:
        raise HTTPException(
            status_code=400,
            detail=(
                f"无法从 {from_node_id!r} 重跑：祖先节点 {bad_ancestors} 上次未成功 "
                f"（请从最早的失败节点重跑）"
            ),
        )

    payload_vars = payload.get("variables")
    if payload_vars is not None:
        variables = coerce_string_dict(payload_vars)
    else:
        # 沿用上次 run 的变量；剥掉每次跑都该重算的内置时间变量。
        builtin_keys = {"today", "now", "year", "month", "day"}
        variables = {k: v for k, v in previous_run.variables.items() if k not in builtin_keys}

    return submit_workflow_run(
        previous_run.workflow_id,
        variables,
        resume_from=previous_run,
        from_node_id=from_node_id,
        max_retries=payload.get("max_retries"),
        trigger="rerun",
        owner_user_id=getattr(current, "id", "") or "",
        project_id=workflow.project_id or "",
    )
