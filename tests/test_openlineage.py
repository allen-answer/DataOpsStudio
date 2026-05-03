from __future__ import annotations

import json

from fastapi.testclient import TestClient
import pytest

from app.models import (
    AssetKind,
    AssetRef,
    NodeRunStatus,
    Workflow,
    WorkflowNode,
    WorkflowNodeRun,
    WorkflowNodeType,
    WorkflowRun,
    WorkflowRunStatus,
)
from app.services.openlineage_emitter import build_workflow_run_events


@pytest.fixture
def client(isolated_storage):
    from main import app

    return TestClient(app)


def _run_with_artifact(status: WorkflowRunStatus = WorkflowRunStatus.SUCCESS) -> WorkflowRun:
    return WorkflowRun(
        run_id="run-openlineage-1",
        workflow_id="wf-openlineage",
        workflow_name="Daily Orders",
        status=status,
        variables={"biz_date": "2026-05-03", "password": "secret"},
        started_at="2026-05-03T02:00:00",
        finished_at="2026-05-03T02:00:05",
        elapsed_seconds=5,
        error="" if status == WorkflowRunStatus.SUCCESS else "node failed",
        nodes=[
            WorkflowNodeRun(
                node_id="export",
                type=WorkflowNodeType.EXCEL_EXPORT,
                name="Export",
                status=NodeRunStatus.SUCCESS if status == WorkflowRunStatus.SUCCESS else NodeRunStatus.FAILED,
                elapsed_seconds=1,
                output={
                    "artifacts": [
                        {
                            "id": "art-1",
                            "run_id": "run-openlineage-1",
                            "node_id": "export",
                            "type": "excel",
                            "name": "orders.xlsx",
                            "relative_path": "workflow_runs/run-openlineage-1/exports/orders.xlsx",
                            "size_bytes": 128,
                            "created_at": "2026-05-03T02:00:05",
                        }
                    ]
                },
            )
        ],
    )


def _workflow() -> Workflow:
    return Workflow(
        id="wf-openlineage",
        name="Daily Orders",
        description="Build daily order facts",
        owner="data-team",
        tags=["daily", "orders"],
        schedule_cron="0 2 * * *",
        project="warehouse",
        input_assets=[
            AssetRef(key="ods.orders", kind=AssetKind.TABLE, description="order source"),
        ],
        output_assets=[
            AssetRef(key="dwd.fact_orders", kind=AssetKind.TABLE, description="fact table"),
        ],
        nodes=[
            WorkflowNode(id="p", type=WorkflowNodeType.PARAMS),
            WorkflowNode(id="export", type=WorkflowNodeType.EXCEL_EXPORT, depends_on=["p"]),
        ],
    )


def test_build_workflow_run_events_maps_workflow_assets_and_artifacts():
    events = build_workflow_run_events(_run_with_artifact(), _workflow())

    assert [event["eventType"] for event in events] == ["START", "COMPLETE"]
    complete = events[-1]
    assert complete["eventTime"] == "2026-05-03T02:00:05Z"
    assert complete["job"]["namespace"] == "dataops-studio"
    assert complete["job"]["name"] == "Daily Orders"
    assert complete["run"]["runId"] == "run-openlineage-1"

    input_names = {dataset["name"] for dataset in complete["inputs"]}
    output_names = {dataset["name"] for dataset in complete["outputs"]}
    assert input_names == {"ods.orders"}
    assert "dwd.fact_orders" in output_names
    assert "workflow_runs/run-openlineage-1/exports/orders.xlsx" in output_names

    workflow_facet = complete["job"]["facets"]["dataops_workflow"]
    assert workflow_facet["owner"] == "data-team"
    assert workflow_facet["project"] == "warehouse"

    run_facet = complete["run"]["facets"]["dataops_workflow_run"]
    assert run_facet["status"] == "success"
    assert run_facet["variableKeys"] == ["biz_date", "password"]
    assert "secret" not in json.dumps(complete, ensure_ascii=False)


def test_build_workflow_run_events_uses_fail_for_failed_runs():
    events = build_workflow_run_events(_run_with_artifact(WorkflowRunStatus.FAILED), _workflow())

    assert [event["eventType"] for event in events] == ["START", "FAIL"]
    assert events[-1]["run"]["facets"]["dataops_workflow_run"]["error"] == "node failed"


def test_workflow_run_openlineage_endpoint(client, isolated_storage):
    workflow_payload = {
        "name": "api-openlineage",
        "input_assets": [{"key": "ods.users", "kind": "table", "description": "users"}],
        "output_assets": [{"key": "dwd.dim_users", "kind": "table", "description": "dim users"}],
        "nodes": [
            {
                "id": "p",
                "type": "params",
                "config": {"parameters": [{"name": "biz_date", "type": "fixed", "default": "2026-05-03"}]},
            }
        ],
    }
    workflow = client.post("/api/workflows", json=workflow_payload).json()
    run = client.post(f"/api/workflows/{workflow['id']}/run", json={"variables": {}}).json()

    response = client.get(f"/api/workflow-runs/{run['run_id']}/openlineage")

    assert response.status_code == 200, response.text
    events = response.json()["events"]
    assert [event["eventType"] for event in events] == ["START", "COMPLETE"]
    assert events[-1]["inputs"][0]["name"] == "ods.users"
    assert events[-1]["outputs"][0]["name"] == "dwd.dim_users"


def test_workflow_run_openlineage_endpoint_404(client, isolated_storage):
    response = client.get("/api/workflow-runs/missing/openlineage")

    assert response.status_code == 404
