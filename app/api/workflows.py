"""作业流定义 CRUD + 同步 / 异步执行 + 该作业流的运行历史列表。"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api._shared import coerce_string_dict, ensure_workflow_node_targets
from app.models import (
    JobInfo,
    OkResponse,
    Workflow,
    WorkflowCreate,
    WorkflowRun,
    WorkflowRunSummary,
    WorkflowTemplate,
    WorkflowTemplateCreate,
    WorkflowTemplateInstantiate,
)
from app.services.auth import get_current_user, require_role
from app.services.jobs import submit_workflow_run
from app.services.notifier import notify_workflow_run
from app.services.openlineage_emitter import emit_workflow_run_openlineage
from app.services.repositories import workflow_store, workflow_template_store
from app.services.workflow_engine import run_workflow
from app.services.workflow_history import list_workflow_runs, persist_workflow_run


# router 级 default：viewer 也要登录。mutation / run / template 写操作单独升级 editor。
router = APIRouter(dependencies=[Depends(get_current_user)])


class WorkflowTemplateMeta(BaseModel):
    name: str = Field("", min_length=0)
    description: str = ""
    category: str = ""
    tags: list[str] = Field(default_factory=list)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _workflow_to_create(workflow: Workflow) -> WorkflowCreate:
    return WorkflowCreate.model_validate(workflow.model_dump(exclude={"id"}))


def _template_payload(payload: WorkflowTemplateCreate) -> WorkflowTemplateCreate:
    ensure_workflow_node_targets(payload.workflow)
    now = _now_iso()
    return payload.model_copy(update={
        "tags": [tag for tag in payload.tags if tag],
        "created_at": payload.created_at or now,
        "updated_at": now,
    })


@router.get("/api/workflows", response_model=list[Workflow])
def list_workflows(project_id: str = ""):
    items = workflow_store.list()
    if project_id:
        items = [w for w in items if w.project_id == project_id or not w.project_id]
    return items


@router.post("/api/workflows", response_model=Workflow)
def create_workflow(payload: WorkflowCreate, _: object = Depends(require_role("editor"))):
    ensure_workflow_node_targets(payload)
    return workflow_store.create(payload)


@router.put("/api/workflows/{workflow_id}", response_model=Workflow)
def update_workflow(workflow_id: str, payload: WorkflowCreate, _: object = Depends(require_role("editor"))):
    ensure_workflow_node_targets(payload)
    try:
        return workflow_store.update(workflow_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Workflow not found") from exc


@router.delete("/api/workflows/{workflow_id}", response_model=OkResponse)
def delete_workflow(workflow_id: str, _: object = Depends(require_role("editor"))):
    try:
        workflow_store.delete(workflow_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Workflow not found") from exc
    return {"ok": True}


@router.get("/api/workflow-templates", response_model=list[WorkflowTemplate])
def list_workflow_templates():
    return workflow_template_store.list()


@router.post("/api/workflow-templates", response_model=WorkflowTemplate)
def create_workflow_template(payload: WorkflowTemplateCreate, _: object = Depends(require_role("editor"))):
    return workflow_template_store.create(_template_payload(payload))


@router.delete("/api/workflow-templates/{template_id}", response_model=OkResponse)
def delete_workflow_template(template_id: str, _: object = Depends(require_role("editor"))):
    try:
        workflow_template_store.delete(template_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Workflow template not found") from exc
    return {"ok": True}


@router.post("/api/workflow-templates/{template_id}/instantiate", response_model=Workflow)
def instantiate_workflow_template(
    template_id: str,
    payload: WorkflowTemplateInstantiate | None = Body(None),
    _: object = Depends(require_role("editor")),
):
    template = workflow_template_store.get(template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Workflow template not found")
    payload = payload or WorkflowTemplateInstantiate()
    workflow_payload = template.workflow.model_copy(deep=True)
    update = {
        "name": payload.name or template.workflow.name,
        "project": payload.project or template.workflow.project,
        "owner": payload.owner or template.workflow.owner,
        "status": payload.status,
    }
    workflow_payload = workflow_payload.model_copy(update=update)
    ensure_workflow_node_targets(workflow_payload)
    return workflow_store.create(workflow_payload)


@router.post("/api/workflows/{workflow_id}/template", response_model=WorkflowTemplate)
def save_workflow_as_template(
    workflow_id: str,
    payload: WorkflowTemplateMeta | None = Body(None),
    _: object = Depends(require_role("editor")),
):
    workflow = workflow_store.get(workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    payload = payload or WorkflowTemplateMeta()
    template = WorkflowTemplateCreate(
        name=payload.name or workflow.name,
        description=payload.description or workflow.description,
        category=payload.category or workflow.project,
        tags=payload.tags or workflow.tags,
        workflow=_workflow_to_create(workflow),
    )
    return workflow_template_store.create(_template_payload(template))


@router.post("/api/workflows/{workflow_id}/run", response_model=WorkflowRun)
def run_workflow_api(
    workflow_id: str,
    payload: dict[str, object] | None = Body(None),
    _: object = Depends(require_role("editor")),
):
    workflow = workflow_store.get(workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    variables = coerce_string_dict((payload or {}).get("variables"))
    try:
        run = run_workflow(workflow, variables)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    notify_workflow_run(workflow, run, trigger="manual")
    emit_results = emit_workflow_run_openlineage(workflow, run, trigger="manual")
    if emit_results:
        run.integrations["openlineage"] = emit_results
    persist_workflow_run(run)
    return run


@router.post("/api/workflows/{workflow_id}/run-async", response_model=JobInfo)
def run_workflow_async_api(
    workflow_id: str,
    payload: dict[str, object] | None = Body(None),
    _: object = Depends(require_role("editor")),
):
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
