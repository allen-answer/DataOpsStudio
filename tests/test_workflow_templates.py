from __future__ import annotations

from fastapi.testclient import TestClient


def _client() -> TestClient:
    from main import app
    return TestClient(app)


def _workflow_payload(name: str = "daily-orders") -> dict:
    return {
        "name": name,
        "description": "daily template source",
        "project": "dwd",
        "owner": "dataops",
        "tags": ["daily", "orders"],
        "default_variables": {"biz_date": "2026-05-03"},
        "nodes": [
            {
                "id": "params",
                "type": "params",
                "name": "params",
                "config": {"parameters": [{"name": "biz_date", "default": "2026-05-03"}]},
            },
            {
                "id": "export",
                "type": "excel_export",
                "name": "export",
                "depends_on": ["params"],
                "config": {"sheets": [{"name": "summary", "source": "params.summary"}]},
            },
        ],
    }


def test_create_template_and_instantiate_workflow(isolated_storage):
    client = _client()

    response = client.post("/api/workflow-templates", json={
        "name": "orders-daily-template",
        "description": "Reusable daily orders workflow",
        "category": "warehouse",
        "tags": ["daily"],
        "workflow": _workflow_payload(),
    })
    assert response.status_code == 200, response.text
    template = response.json()
    assert template["id"]
    assert template["workflow"]["nodes"][1]["depends_on"] == ["params"]
    assert template["created_at"]
    assert template["updated_at"]

    response = client.post(f"/api/workflow-templates/{template['id']}/instantiate", json={
        "name": "orders-daily-prod",
        "project": "prod",
        "owner": "ops",
        "status": "active",
    })
    assert response.status_code == 200, response.text
    workflow = response.json()
    assert workflow["id"] != template["id"]
    assert workflow["name"] == "orders-daily-prod"
    assert workflow["project"] == "prod"
    assert workflow["owner"] == "ops"
    assert workflow["status"] == "active"
    assert workflow["nodes"][1]["depends_on"] == ["params"]


def test_save_existing_workflow_as_template_and_bootstrap(isolated_storage):
    client = _client()

    workflow = client.post("/api/workflows", json=_workflow_payload("saved-source")).json()
    response = client.post(f"/api/workflows/{workflow['id']}/template", json={
        "name": "saved-template",
        "category": "curated",
        "tags": ["copy"],
    })
    assert response.status_code == 200, response.text
    template = response.json()
    assert template["name"] == "saved-template"
    assert template["category"] == "curated"
    assert template["workflow"]["name"] == "saved-source"
    assert template["workflow"]["project"] == "dwd"

    bootstrap = client.get("/api/bootstrap").json()
    assert any(item["id"] == template["id"] for item in bootstrap["workflow_templates"])


def test_template_rejects_bad_dependency(isolated_storage):
    client = _client()
    payload = _workflow_payload()
    payload["nodes"][1]["depends_on"] = ["missing"]

    response = client.post("/api/workflow-templates", json={
        "name": "bad-template",
        "workflow": payload,
    })
    assert response.status_code == 400
    assert "depends_on" in response.json()["detail"]
