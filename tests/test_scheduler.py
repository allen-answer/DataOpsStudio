from __future__ import annotations

from datetime import datetime

from app.models import Workflow, WorkflowStatus
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
