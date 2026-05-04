from __future__ import annotations

import json
import time
from datetime import datetime, timedelta

from app.services import jobs


class _FakeResult:
    def __init__(self, payload):
        self.payload = payload

    def model_dump(self, mode="json"):
        return dict(self.payload)


def _wait_for_terminal(job_id: str, timeout: float = 2.0) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        last = jobs.get_job(job_id)
        if last["status"] in {"success", "failed", "cancelled"}:
            return last
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} did not finish, last={last}")


def test_cleanup_jobs_prunes_only_expired_terminal_jobs(isolated_storage):
    now = datetime(2026, 5, 3, 12, 0, 0)
    old = now - timedelta(hours=2)
    jobs._jobs["old-success"] = {
        "job_id": "old-success",
        "kind": "compare",
        "status": "success",
        "created_at": old.isoformat(timespec="seconds"),
        "updated_at": old.isoformat(timespec="seconds"),
    }
    jobs._jobs["active-running"] = {
        "job_id": "active-running",
        "kind": "workflow",
        "status": "running",
        "created_at": old.isoformat(timespec="seconds"),
        "updated_at": old.isoformat(timespec="seconds"),
    }

    removed = jobs.cleanup_jobs(ttl_seconds=3600, now=now)

    assert removed == 1
    assert "old-success" not in jobs._jobs
    assert "active-running" in jobs._jobs
    # Phase 9 ADR 6 起 jobs 主存储是 SQLite —— 走 sqlite_store 验证
    from app.services import sqlite_store
    with sqlite_store.connect() as conn:
        rows = conn.execute("SELECT id FROM jobs ORDER BY id").fetchall()
    assert [r["id"] for r in rows] == ["active-running"]


def test_submit_task_run_retries_failed_attempt_once(monkeypatch, isolated_storage):
    attempts = {"count": 0}

    def fake_run_task(task_id, status_callback=None):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("temporary database error")
        if status_callback is not None:
            status_callback("exporting", "writing result")
        return _FakeResult({"run_id": "ok-run", "task_id": task_id})

    monkeypatch.setattr(jobs, "run_task", fake_run_task)

    job = jobs.submit_task_run("task-1", max_retries=1)
    final = _wait_for_terminal(job["job_id"])

    assert final["status"] == "success"
    assert final["stage"] == "success"
    assert final["retry_count"] == 1
    assert final["max_retries"] == 1
    assert final["result"] == {"run_id": "ok-run", "task_id": "task-1"}
    assert final["expires_at"]
    assert attempts["count"] == 2


def test_submit_task_run_without_retry_fails_after_first_attempt(monkeypatch, isolated_storage):
    attempts = {"count": 0}

    def fake_run_task(task_id, status_callback=None):
        attempts["count"] += 1
        raise RuntimeError("permanent failure")

    monkeypatch.setattr(jobs, "run_task", fake_run_task)

    job = jobs.submit_task_run("task-1", max_retries=0)
    final = _wait_for_terminal(job["job_id"])

    assert final["status"] == "failed"
    assert final["retry_count"] == 0
    assert final["error"] == "permanent failure"
    assert attempts["count"] == 1


def test_job_info_model_accepts_compare_kind():
    from app.models import JobInfo

    job = JobInfo.model_validate(
        {
            "job_id": "j1",
            "kind": "compare",
            "status": "running",
            "stage": "querying_source",
            "message": "reading source",
        }
    )

    assert job.kind == "compare"
    assert job.stage == "querying_source"
