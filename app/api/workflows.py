"""作业流定义 CRUD + 同步 / 异步执行 + 该作业流的运行历史列表。"""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException

from app.api._shared import coerce_string_dict, ensure_workflow_node_targets
from app.models import (
    JobInfo,
    OkResponse,
    Workflow,
    WorkflowCreate,
    WorkflowRun,
    WorkflowRunSummary,
)
from app.services.jobs import submit_workflow_run
from app.services.repositories import workflow_store
from app.services.workflow_engine import run_workflow
from app.services.workflow_history import list_workflow_runs, persist_workflow_run


router = APIRouter()


@router.get("/api/workflows", response_model=list[Workflow])
def list_workflows():
    return workflow_store.list()


@router.post("/api/workflows", response_model=Workflow)
def create_workflow(payload: WorkflowCreate):
    ensure_workflow_node_targets(payload)
    return workflow_store.create(payload)


@router.put("/api/workflows/{workflow_id}", response_model=Workflow)
def update_workflow(workflow_id: str, payload: WorkflowCreate):
    ensure_workflow_node_targets(payload)
    try:
        return workflow_store.update(workflow_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Workflow not found") from exc


@router.delete("/api/workflows/{workflow_id}", response_model=OkResponse)
def delete_workflow(workflow_id: str):
    try:
        workflow_store.delete(workflow_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Workflow not found") from exc
    return {"ok": True}


@router.post("/api/workflows/{workflow_id}/run", response_model=WorkflowRun)
def run_workflow_api(workflow_id: str, payload: dict[str, object] | None = Body(None)):
    workflow = workflow_store.get(workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    variables = coerce_string_dict((payload or {}).get("variables"))
    try:
        run = run_workflow(workflow, variables)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    persist_workflow_run(run)
    return run


@router.post("/api/workflows/{workflow_id}/run-async", response_model=JobInfo)
def run_workflow_async_api(workflow_id: str, payload: dict[str, object] | None = Body(None)):
    if workflow_store.get(workflow_id) is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    payload = payload or {}
    variables = coerce_string_dict(payload.get("variables"))
    return submit_workflow_run(workflow_id, variables, max_retries=payload.get("max_retries"))


@router.get("/api/workflows/{workflow_id}/runs", response_model=list[WorkflowRunSummary])
def list_workflow_runs_api(workflow_id: str, limit: int = 50):
    if workflow_store.get(workflow_id) is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return list_workflow_runs(workflow_id, limit=limit)
