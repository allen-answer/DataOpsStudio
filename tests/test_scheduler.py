from __future__ import annotations

from datetime import datetime

from app.models import DataSource, DatabaseType, Workflow, WorkflowStatus
from app.services import scheduler


def _workflow(**overrides) -> Workflow:
    data = {
        "id": "wf-1",
        "name": "scheduled workflow",
        "nodes": [],
        "status": WorkflowStatus.ACTIVE,
        "schedule_cron": "0 2 * * *",
    }
    data.update(overrides)
    return Workflow.model_validate(data)


def setup_function():
    scheduler.reset_scheduler_state_for_tests()


def teardown_function():
    scheduler.reset_scheduler_state_for_tests()


def test_next_run_after_supports_daily_cron():
    value = scheduler.next_run_after("0 2 * * *", datetime(2026, 5, 3, 1, 59, 30))
    assert value == datetime(2026, 5, 3, 2, 0)


def test_next_run_after_supports_step_cron():
    value = scheduler.next_run_after("*/15 * * * *", datetime(2026, 5, 3, 2, 1))
    assert value == datetime(2026, 5, 3, 2, 15)


def test_tick_submits_due_active_workflow(monkeypatch):
    submissions: list[dict] = []
    monkeypatch.setattr(scheduler.workflow_store, "list", lambda: [_workflow()])

    def fake_submit(workflow_id, variables=None, max_retries=None, trigger="manual", **_):
        submissions.append({
            "workflow_id": workflow_id,
            "variables": variables,
            "max_retries": max_retries,
            "trigger": trigger,
        })
        return {"job_id": "job-1", "status": "queued"}

    monkeypatch.setattr(scheduler, "submit_workflow_run", fake_submit)

    result = scheduler.tick(datetime(2026, 5, 3, 2, 0))

    if scheduler.APSCHEDULER_AVAILABLE:
        assert submissions == []
        assert result["submitted"] == []
    else:
        assert submissions == [{
            "workflow_id": "wf-1",
            "variables": {},
            "max_retries": scheduler.DEFAULT_MAX_RETRIES,
            "trigger": "schedule",
        }]
        assert result["submitted"] == [{"workflow_id": "wf-1", "job_id": "job-1"}]
    entry = result["status"]["entries"][0]
    if scheduler.APSCHEDULER_AVAILABLE:
        assert entry["last_job_id"] == ""
    else:
        assert entry["last_job_id"] == "job-1"
        assert entry["next_run_at"] == "2026-05-04T02:00:00"


def test_tick_ignores_non_active_workflow(monkeypatch):
    monkeypatch.setattr(scheduler.workflow_store, "list", lambda: [_workflow(status=WorkflowStatus.PAUSED)])
    monkeypatch.setattr(scheduler, "submit_workflow_run", lambda *_, **__: (_ for _ in ()).throw(AssertionError("no submit")))

    result = scheduler.tick(datetime(2026, 5, 3, 2, 0))

    assert result["submitted"] == []
    assert result["status"]["entries"] == []


def test_tick_reports_invalid_cron_without_raising(monkeypatch):
    monkeypatch.setattr(scheduler.workflow_store, "list", lambda: [_workflow(schedule_cron="bad")])

    result = scheduler.tick(datetime(2026, 5, 3, 2, 0))

    assert result["submitted"] == []
    assert result["errors"][0]["workflow_id"] == "wf-1"
    assert "cron" in result["errors"][0]["error"]


def test_start_scheduler_registers_active_workflow(monkeypatch):
    monkeypatch.setattr(scheduler.workflow_store, "list", lambda: [_workflow()])

    status = scheduler.start_scheduler(interval_seconds=30)

    assert status["running"] is True
    assert status["interval_seconds"] == 30
    assert len(status["entries"]) == 1
    assert status["entries"][0]["workflow_id"] == "wf-1"
    assert status["entries"][0]["cron"] == "0 2 * * *"
    assert status["entries"][0]["next_run_at"]


def test_tick_updates_changed_cron_without_duplicate(monkeypatch):
    current = {"workflow": _workflow(schedule_cron="0 2 * * *")}
    monkeypatch.setattr(scheduler.workflow_store, "list", lambda: [current["workflow"]])

    first = scheduler.tick(datetime(2026, 5, 3, 1, 0))
    current["workflow"] = _workflow(schedule_cron="30 3 * * *")
    second = scheduler.tick(datetime(2026, 5, 3, 1, 0))

    assert len(first["status"]["entries"]) == 1
    assert len(second["status"]["entries"]) == 1
    assert second["status"]["entries"][0]["cron"] == "30 3 * * *"


def test_scheduled_job_submit_uses_schedule_trigger(monkeypatch):
    submissions: list[dict] = []
    monkeypatch.setattr(scheduler.workflow_store, "get", lambda workflow_id: _workflow(id=workflow_id))

    def fake_submit(workflow_id, variables=None, max_retries=None, trigger="manual", **_):
        submissions.append({
            "workflow_id": workflow_id,
            "variables": variables,
            "max_retries": max_retries,
            "trigger": trigger,
        })
        return {"job_id": "job-1", "status": "queued"}

    monkeypatch.setattr(scheduler, "submit_workflow_run", fake_submit)

    scheduler._run_scheduled_workflow("wf-1")

    assert submissions == [{
        "workflow_id": "wf-1",
        "variables": {},
        "max_retries": scheduler.DEFAULT_MAX_RETRIES,
        "trigger": "schedule",
    }]
    entry = scheduler.scheduler_status()["entries"][0]
    assert entry["last_job_id"] == "job-1"


def test_active_sql_sensor_registers_without_cron(monkeypatch):
    workflow = _workflow(schedule_cron="", sensors=[{
        "id": "ready",
        "type": "sql",
        "datasource_id": "ds-1",
        "sql": "select 1 as ready",
        "interval_seconds": 30,
    }])
    monkeypatch.setattr(scheduler.workflow_store, "list", lambda: [workflow])

    result = scheduler.tick(datetime(2026, 5, 3, 2, 0))

    assert result["status"]["entries"] == []
    assert len(result["status"]["sensors"]) == 1
    assert result["status"]["sensors"][0]["sensor_id"] == "ready"
    assert result["status"]["sensors"][0]["interval_seconds"] == 30


def test_sql_sensor_truthy_submits_workflow(monkeypatch):
    submissions: list[dict] = []
    workflow = _workflow(schedule_cron="", sensors=[{
        "id": "ready",
        "type": "sql",
        "datasource_id": "ds-1",
        "sql": "select 1 as ready",
        "cooldown_seconds": 300,
    }])
    datasource = DataSource(
        id="ds-1",
        name="local",
        db_type=DatabaseType.MYSQL,
        host="localhost",
        port=3306,
    )
    monkeypatch.setattr(scheduler.workflow_store, "get", lambda workflow_id: workflow)
    monkeypatch.setattr(scheduler.datasource_store, "get", lambda datasource_id: datasource)
    monkeypatch.setattr(scheduler, "fetch_rows", lambda *_args, **_kwargs: [{"ready": 1}])

    def fake_submit(workflow_id, variables=None, max_retries=None, trigger="manual", **_):
        submissions.append({
            "workflow_id": workflow_id,
            "variables": variables,
            "max_retries": max_retries,
            "trigger": trigger,
        })
        return {"job_id": "job-sensor", "status": "queued"}

    monkeypatch.setattr(scheduler, "submit_workflow_run", fake_submit)

    scheduler._run_sensor_workflow("wf-1", "ready")

    assert submissions == [{
        "workflow_id": "wf-1",
        "variables": {},
        "max_retries": scheduler.DEFAULT_MAX_RETRIES,
        "trigger": "sensor",
    }]
    sensor_status = scheduler.scheduler_status()["sensors"][0]
    assert sensor_status["last_job_id"] == "job-sensor"
    assert sensor_status["last_value"] == "1"


def test_sql_sensor_falsy_does_not_submit(monkeypatch):
    workflow = _workflow(schedule_cron="", sensors=[{"id": "ready", "type": "sql", "datasource_id": "ds-1", "sql": "select 0"}])
    datasource = DataSource(id="ds-1", name="local", db_type=DatabaseType.MYSQL, host="localhost", port=3306)
    monkeypatch.setattr(scheduler.datasource_store, "get", lambda datasource_id: datasource)
    monkeypatch.setattr(scheduler, "fetch_rows", lambda *_args, **_kwargs: [{"ready": 0}])
    monkeypatch.setattr(scheduler, "submit_workflow_run", lambda *_, **__: (_ for _ in ()).throw(AssertionError("no submit")))

    job = scheduler._evaluate_sensor_and_maybe_submit(workflow, workflow.sensors[0], datetime(2026, 5, 3, 2, 0))

    assert job is None
    assert scheduler.scheduler_status()["sensors"][0]["last_value"] == "0"


def test_http_sensor_json_path_triggers(monkeypatch):
    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self, *_):
            return b'{"ready": true}'

    monkeypatch.setattr(scheduler.urllib.request, "urlopen", lambda *_args, **_kwargs: FakeResponse())

    matched, value = scheduler._evaluate_http_sensor({
        "id": "api",
        "type": "http",
        "url": "http://sensor.local/ready",
        "json_path": "$.ready",
    })

    assert matched is True
    assert value is True
