from __future__ import annotations

import json
from datetime import datetime

from app.models import Workflow, WorkflowRun, WorkflowRunStatus
from app.services import notifier


def _workflow(**overrides) -> Workflow:
    data = {
        "id": "wf-1",
        "name": "notify workflow",
        "nodes": [],
    }
    data.update(overrides)
    return Workflow.model_validate(data)


def _run(status: WorkflowRunStatus = WorkflowRunStatus.SUCCESS) -> WorkflowRun:
    now = datetime(2026, 5, 3, 2, 0).isoformat(timespec="seconds")
    return WorkflowRun(
        run_id="run-1",
        workflow_id="wf-1",
        workflow_name="notify workflow",
        status=status,
        started_at=now,
        finished_at=now,
        elapsed_seconds=0.1,
        error="" if status == WorkflowRunStatus.SUCCESS else "failed",
    )


def test_notify_workflow_run_no_targets_is_noop(monkeypatch):
    monkeypatch.delenv("DATAOPS_NOTIFY_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("DATAOPS_NOTIFY_WECOM_WEBHOOK", raising=False)
    monkeypatch.delenv("DATAOPS_NOTIFY_EMAIL_TO", raising=False)

    assert notifier.notify_workflow_run(_workflow(), _run()) == []


def test_notify_workflow_run_posts_generic_webhook(monkeypatch):
    captured: dict[str, object] = {}

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setattr(notifier.urllib.request, "urlopen", fake_urlopen)
    workflow = _workflow(notifications=[{
        "type": "webhook",
        "url": "http://notify.local/hook",
        "events": ["success"],
    }])

    result = notifier.notify_workflow_run(workflow, _run(), trigger="schedule", job_id="job-1")

    assert result == [{"type": "webhook", "target": "http://notify.local/hook", "ok": True, "error": ""}]
    assert captured["url"] == "http://notify.local/hook"
    assert captured["payload"]["trigger"] == "schedule"
    assert captured["payload"]["job_id"] == "job-1"
    assert captured["payload"]["status"] == "success"


def test_notify_workflow_run_respects_event_filter(monkeypatch):
    monkeypatch.setattr(
        notifier.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not send")),
    )
    workflow = _workflow(notifications=[{
        "type": "webhook",
        "url": "http://notify.local/hook",
        "events": ["failed"],
    }])

    assert notifier.notify_workflow_run(workflow, _run(WorkflowRunStatus.SUCCESS)) == []
