from __future__ import annotations

import json
import time

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
from app.services.openlineage_emitter import build_workflow_run_events, emit_workflow_run_openlineage


class _FakeResponse:
    def __init__(self, status: int = 200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


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


def _wait_for_terminal(job_id: str, timeout: float = 2.0) -> dict:
    from app.services import jobs

    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        last = jobs.get_job(job_id)
        if last["status"] in {"success", "failed", "cancelled"}:
            return last
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} did not finish, last={last}")


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


def test_emit_workflow_run_openlineage_posts_each_event(monkeypatch):
    sent: list[dict[str, object]] = []

    def fake_urlopen(request, timeout):
        sent.append({
            "url": request.full_url,
            "timeout": timeout,
            "payload": json.loads(request.data.decode("utf-8")),
        })
        return _FakeResponse()

    monkeypatch.setattr("app.services.openlineage_emitter.urllib.request.urlopen", fake_urlopen)
    workflow = _workflow().model_copy(update={
        "notifications": [{
            "type": "openlineage",
            "url": "http://lineage.local/events",
            "events": ["all"],
            "namespace": "warehouse",
            "timeout_seconds": 3,
        }]
    })

    result = emit_workflow_run_openlineage(workflow, _run_with_artifact(), trigger="manual", job_id="job-1")

    assert [item["event_type"] for item in result] == ["START", "COMPLETE"]
    assert all(item["ok"] for item in result)
    assert [item["payload"]["eventType"] for item in sent] == ["START", "COMPLETE"]
    assert {item["payload"]["job"]["namespace"] for item in sent} == {"warehouse"}
    assert {item["url"] for item in sent} == {"http://lineage.local/events"}
    assert {item["timeout"] for item in sent} == {3}


def test_emit_workflow_run_openlineage_failed_run_sends_fail(monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(
        "app.services.openlineage_emitter.urllib.request.urlopen",
        lambda request, timeout: sent.append(json.loads(request.data.decode("utf-8"))["eventType"]) or _FakeResponse(),
    )
    workflow = _workflow().model_copy(update={
        "notifications": [{"type": "openlineage_webhook", "url": "http://lineage.local/events"}]
    })

    result = emit_workflow_run_openlineage(workflow, _run_with_artifact(WorkflowRunStatus.FAILED))

    assert [item["event_type"] for item in result] == ["START", "FAIL"]
    assert sent == ["START", "FAIL"]


def test_emit_workflow_run_openlineage_respects_event_filter(monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(
        "app.services.openlineage_emitter.urllib.request.urlopen",
        lambda request, timeout: sent.append(json.loads(request.data.decode("utf-8"))["eventType"]) or _FakeResponse(),
    )
    workflow = _workflow().model_copy(update={
        "notifications": [{
            "type": "openlineage",
            "url": "http://lineage.local/events",
            "events": ["FAIL"],
        }]
    })

    result = emit_workflow_run_openlineage(workflow, _run_with_artifact(WorkflowRunStatus.FAILED))

    assert [item["event_type"] for item in result] == ["FAIL"]
    assert sent == ["FAIL"]


def test_emit_workflow_run_openlineage_missing_url_is_non_fatal():
    workflow = _workflow().model_copy(update={
        "notifications": [{"type": "openlineage", "events": ["COMPLETE"]}]
    })

    result = emit_workflow_run_openlineage(workflow, _run_with_artifact())

    assert result == [{"type": "openlineage", "target": "", "event_type": "COMPLETE", "ok": False, "error": "missing url"}]


def test_emit_workflow_run_openlineage_http_error_is_non_fatal(monkeypatch):
    monkeypatch.setattr(
        "app.services.openlineage_emitter.urllib.request.urlopen",
        lambda request, timeout: _FakeResponse(status=500),
    )
    workflow = _workflow().model_copy(update={
        "notifications": [{"type": "openlineage", "url": "http://lineage.local/events", "events": ["START"]}]
    })

    result = emit_workflow_run_openlineage(workflow, _run_with_artifact())

    assert result == [{
        "type": "openlineage",
        "target": "http://lineage.local/events",
        "event_type": "START",
        "ok": False,
        "error": "http 500",
    }]


def test_emit_workflow_run_openlineage_urlopen_exception_is_non_fatal(monkeypatch):
    def fail_urlopen(_request, timeout=None):
        raise OSError("collector down")

    monkeypatch.setattr("app.services.openlineage_emitter.urllib.request.urlopen", fail_urlopen)
    workflow = _workflow().model_copy(update={
        "notifications": [{"type": "openlineage", "url": "http://lineage.local/events", "events": ["START"]}]
    })

    result = emit_workflow_run_openlineage(workflow, _run_with_artifact())

    assert result == [{
        "type": "openlineage",
        "target": "http://lineage.local/events",
        "event_type": "START",
        "ok": False,
        "error": "collector down",
    }]


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


def test_sync_workflow_run_calls_openlineage_emitter(client, isolated_storage, monkeypatch):
    calls: list[dict[str, object]] = []

    def fake_emit(workflow, run, *, trigger="", job_id=""):
        calls.append({"workflow_id": workflow.id, "run_id": run.run_id, "trigger": trigger, "job_id": job_id})
        return []

    monkeypatch.setattr("app.api.workflows.emit_workflow_run_openlineage", fake_emit)
    workflow = client.post("/api/workflows", json={
        "name": "sync-openlineage",
        "nodes": [{"id": "p", "type": "params", "config": {"parameters": [{"name": "x", "default": "1"}]}}],
    }).json()
    run = client.post(f"/api/workflows/{workflow['id']}/run", json={"variables": {}}).json()

    assert calls == [{
        "workflow_id": workflow["id"],
        "run_id": run["run_id"],
        "trigger": "manual",
        "job_id": "",
    }]


def test_async_workflow_run_calls_openlineage_emitter(client, isolated_storage, monkeypatch):
    calls: list[dict[str, object]] = []

    def fake_emit(workflow, run, *, trigger="", job_id=""):
        calls.append({"workflow_id": workflow.id, "run_id": run.run_id, "trigger": trigger, "job_id": job_id})
        return []

    monkeypatch.setattr("app.services.jobs.emit_workflow_run_openlineage", fake_emit)
    workflow = client.post("/api/workflows", json={
        "name": "async-openlineage",
        "nodes": [{"id": "p", "type": "params", "config": {"parameters": [{"name": "x", "default": "1"}]}}],
    }).json()
    job = client.post(f"/api/workflows/{workflow['id']}/run-async", json={"variables": {}}).json()
    final = _wait_for_terminal(job["job_id"])

    assert final["status"] == "success"
    assert len(calls) == 1
    assert calls[0]["workflow_id"] == workflow["id"]
    assert calls[0]["trigger"] == "manual"
    assert calls[0]["job_id"] == job["job_id"]


def test_workflow_run_openlineage_endpoint_404(client, isolated_storage):
    response = client.get("/api/workflow-runs/missing/openlineage")

    assert response.status_code == 404
