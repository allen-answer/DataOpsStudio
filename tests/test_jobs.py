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


# ─── Phase 13:JobInfo 三字段补全(owner / project / target_run_id)───────────


class _FakeResultWithRunId:
    """有真 `run_id` 属性(jobs._run_job getattr 抓取)+ model_dump 返还。"""

    def __init__(self, run_id: str, task_id: str) -> None:
        self.run_id = run_id
        self._payload = {"run_id": run_id, "task_id": task_id}

    def model_dump(self, mode="json"):
        return dict(self._payload)


def test_submit_task_run_captures_owner_and_project(monkeypatch, isolated_storage):
    """submit_task_run 接收 owner_user_id + project_id,落 job dict 供 audit / authz 直接读"""
    def fake_run_task(task_id, status_callback=None):
        return _FakeResultWithRunId("run-xyz", task_id)
    monkeypatch.setattr(jobs, "run_task", fake_run_task)

    job = jobs.submit_task_run(
        "task-42",
        owner_user_id="user-7",
        project_id="proj-9",
    )
    final = _wait_for_terminal(job["job_id"])

    assert final["owner_user_id"] == "user-7"
    assert final["project_id"] == "proj-9"
    # success 分支应填 target_run_id(从 result.run_id 抓)
    assert final["target_run_id"] == "run-xyz"


def test_submit_task_run_defaults_blank_owner_when_not_passed(monkeypatch, isolated_storage):
    """老 caller(scheduler 外 / 测试)不传 owner/project,字段默认空串向后兼容"""
    def fake_run_task(task_id, status_callback=None):
        return _FakeResultWithRunId("run-abc", task_id)
    monkeypatch.setattr(jobs, "run_task", fake_run_task)

    job = jobs.submit_task_run("task-1")
    final = _wait_for_terminal(job["job_id"])

    assert final["owner_user_id"] == ""
    assert final["project_id"] == ""
    assert final["target_run_id"] == "run-abc"


def test_job_info_model_has_new_fields():
    from app.models import JobInfo
    job = JobInfo.model_validate(
        {
            "job_id": "j2",
            "kind": "compare",
            "status": "success",
            "owner_user_id": "u-1",
            "project_id": "p-1",
            "target_run_id": "r-1",
        }
    )
    assert job.owner_user_id == "u-1"
    assert job.project_id == "p-1"
    assert job.target_run_id == "r-1"


def test_job_info_model_new_fields_default_empty():
    """老 job dict 不带新字段 → JobInfo 反序列化默认空串(向后兼容关键)"""
    from app.models import JobInfo
    job = JobInfo.model_validate(
        {
            "job_id": "j3",
            "kind": "workflow",
            "status": "queued",
        }
    )
    assert job.owner_user_id == ""
    assert job.project_id == ""
    assert job.target_run_id == ""
