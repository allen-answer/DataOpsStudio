from __future__ import annotations

import json
import logging
import os
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Lock
from typing import Any
from uuid import uuid4

from app.models import WorkflowRun
from app.services.repositories import workflow_store
from app.services.runner import run_task
from app.services.notifier import notify_workflow_run
from app.services.workflow_engine import run_workflow
from app.services.workflow_history import persist_workflow_run
from app.utils.paths import JOBS_FILE

logger = logging.getLogger(__name__)

DEFAULT_JOB_TTL_SECONDS = int(os.getenv("DATAOPS_JOB_TTL_SECONDS", str(24 * 60 * 60)))
DEFAULT_JOB_MAX_RETRIES = int(os.getenv("DATAOPS_JOB_MAX_RETRIES", "0"))
MAX_JOB_RETRIES = 5

_TERMINAL_STATUSES = {"success", "failed", "cancelled"}
_executor = ThreadPoolExecutor(max_workers=2)
_lock = Lock()
_jobs: dict[str, dict[str, Any]] = {}
_futures: dict[str, Future[Any]] = {}


class JobCancelled(RuntimeError):
    pass


def submit_task_run(task_id: str, max_retries: int | None = None) -> dict[str, Any]:
    cleanup_jobs()
    job_id = uuid4().hex
    now = datetime.now()
    _set_job(
        job_id,
        {
            "job_id": job_id,
            "kind": "compare",
            "task_id": task_id,
            "status": "queued",
            "stage": "queued",
            "message": "queued",
            "created_at": _iso(now),
            "updated_at": _iso(now),
            "expires_at": "",
            "retry_count": 0,
            "max_retries": _coerce_max_retries(max_retries),
            "result": None,
            "error": "",
            "cancel_requested": False,
        },
    )
    future = _executor.submit(_run_job, job_id, task_id)
    with _lock:
        _futures[job_id] = future
    return get_job(job_id)


def submit_workflow_run(
    workflow_id: str,
    variables: dict[str, str] | None = None,
    resume_from: WorkflowRun | None = None,
    from_node_id: str | None = None,
    max_retries: int | None = None,
    trigger: str = "manual",
) -> dict[str, Any]:
    cleanup_jobs()
    job_id = uuid4().hex
    now = datetime.now()
    job_record: dict[str, Any] = {
        "job_id": job_id,
        "kind": "workflow",
        "workflow_id": workflow_id,
        "variables": dict(variables or {}),
        "trigger": trigger,
        "status": "queued",
        "stage": "queued",
        "message": "queued",
        "created_at": _iso(now),
        "updated_at": _iso(now),
        "expires_at": "",
        "retry_count": 0,
        "max_retries": _coerce_max_retries(max_retries),
        "result": None,
        "error": "",
        "cancel_requested": False,
    }
    if resume_from is not None and from_node_id:
        job_record["resumed_from"] = {
            "run_id": resume_from.run_id,
            "from_node_id": from_node_id,
        }
    _set_job(job_id, job_record)
    future = _executor.submit(
        _run_workflow_job,
        job_id,
        workflow_id,
        dict(variables or {}),
        resume_from,
        from_node_id,
        trigger,
    )
    with _lock:
        _futures[job_id] = future
    return get_job(job_id)


def get_job(job_id: str) -> dict[str, Any]:
    cleanup_jobs()
    with _lock:
        if job_id not in _jobs:
            raise KeyError(job_id)
        return dict(_jobs[job_id])


def cancel_job(job_id: str) -> dict[str, Any]:
    cleanup_jobs()
    with _lock:
        if job_id not in _jobs:
            raise KeyError(job_id)
        job = _jobs[job_id]
        if job["status"] in _TERMINAL_STATUSES:
            return dict(job)
        job["cancel_requested"] = True
        job["status"] = "cancelling"
        job["stage"] = "cancelling"
        job["message"] = "cancelling"
        job["updated_at"] = _iso(datetime.now())
        future = _futures.get(job_id)
    _persist_jobs()
    if future is not None and future.cancel():
        _patch_job(job_id, status="cancelled", stage="cancelled", message="cancelled", error="cancelled before start")
    return get_job(job_id)


def cleanup_jobs(ttl_seconds: int | None = None, now: datetime | None = None) -> int:
    ttl = DEFAULT_JOB_TTL_SECONDS if ttl_seconds is None else int(ttl_seconds)
    if ttl <= 0:
        return 0
    now = now or datetime.now()
    with _lock:
        expired_ids = [
            job_id
            for job_id, job in _jobs.items()
            if _is_expired_terminal_job(job, now, ttl)
        ]
        for job_id in expired_ids:
            _jobs.pop(job_id, None)
            _futures.pop(job_id, None)
    if expired_ids:
        _persist_jobs()
    return len(expired_ids)


def _run_job(job_id: str, task_id: str) -> None:
    max_retries = _job_max_retries(job_id)

    def update(stage: str, message: str) -> None:
        if _is_cancel_requested(job_id):
            raise JobCancelled("job cancelled")
        _patch_job(job_id, status="running", stage=stage, message=message)

    try:
        retry_count = 0
        while True:
            try:
                if _is_cancel_requested(job_id):
                    raise JobCancelled("job cancelled")
                _patch_job(
                    job_id,
                    status="running",
                    stage="running",
                    message="running" if retry_count == 0 else f"retry {retry_count}/{max_retries}",
                    error="",
                    retry_count=retry_count,
                )
                result = run_task(task_id, status_callback=update)
                if _is_cancel_requested(job_id):
                    raise JobCancelled("job cancelled")
                _patch_job(
                    job_id,
                    status="success",
                    stage="success",
                    message="success",
                    result=result.model_dump(mode="json"),
                    retry_count=retry_count,
                )
                return
            except JobCancelled:
                raise
            except Exception as exc:
                if retry_count < max_retries and not _is_cancel_requested(job_id):
                    retry_count += 1
                    _patch_job(
                        job_id,
                        status="queued",
                        stage="retry_wait",
                        message=f"retry pending {retry_count}/{max_retries}",
                        error=str(exc),
                        retry_count=retry_count,
                    )
                    continue
                _patch_job(
                    job_id,
                    status="failed",
                    stage="failed",
                    message="failed",
                    error=str(exc),
                    retry_count=retry_count,
                )
                return
    except JobCancelled as exc:
        _patch_job(job_id, status="cancelled", stage="cancelled", message="cancelled", error=str(exc))
    finally:
        with _lock:
            _futures.pop(job_id, None)


def _run_workflow_job(
    job_id: str,
    workflow_id: str,
    variables: dict[str, str],
    resume_from: WorkflowRun | None = None,
    from_node_id: str | None = None,
    trigger: str = "manual",
) -> None:
    max_retries = _job_max_retries(job_id)
    try:
        retry_count = 0
        while True:
            try:
                if _is_cancel_requested(job_id):
                    _patch_job(job_id, status="cancelled", stage="cancelled", message="cancelled", error="job cancelled")
                    return
                _patch_job(
                    job_id,
                    status="running",
                    stage="running",
                    message="running" if retry_count == 0 else f"retry {retry_count}/{max_retries}",
                    error="",
                    retry_count=retry_count,
                )
                workflow = workflow_store.get(workflow_id)
                if workflow is None:
                    raise ValueError(f"Workflow not found: {workflow_id}")
                run = run_workflow(
                    workflow,
                    variables,
                    cancel_check=lambda: _is_cancel_requested(job_id),
                    resume_from=resume_from,
                    from_node_id=from_node_id,
                )
                persist_workflow_run(run)
                notify_workflow_run(workflow, run, trigger=trigger, job_id=job_id)
                result = run.model_dump(mode="json")
                if run.error == "cancelled":
                    _patch_job(
                        job_id,
                        status="cancelled",
                        stage="cancelled",
                        message="cancelled",
                        error="job cancelled",
                        result=result,
                    )
                    return
                if run.status.value == "success":
                    _patch_job(
                        job_id,
                        status="success",
                        stage="success",
                        message="success",
                        result=result,
                        retry_count=retry_count,
                    )
                    return
                if retry_count < max_retries and not _is_cancel_requested(job_id):
                    retry_count += 1
                    _patch_job(
                        job_id,
                        status="queued",
                        stage="retry_wait",
                        message=f"retry pending {retry_count}/{max_retries}",
                        error=run.error or "workflow failed",
                        result=result,
                        retry_count=retry_count,
                    )
                    continue
                _patch_job(
                    job_id,
                    status="failed",
                    stage="failed",
                    message="failed",
                    error=run.error or "workflow failed",
                    result=result,
                    retry_count=retry_count,
                )
                return
            except Exception as exc:
                if retry_count < max_retries and not _is_cancel_requested(job_id):
                    retry_count += 1
                    _patch_job(
                        job_id,
                        status="queued",
                        stage="retry_wait",
                        message=f"retry pending {retry_count}/{max_retries}",
                        error=str(exc),
                        retry_count=retry_count,
                    )
                    continue
                _patch_job(job_id, status="failed", stage="failed", message="failed", error=str(exc), retry_count=retry_count)
                return
    finally:
        with _lock:
            _futures.pop(job_id, None)


def _set_job(job_id: str, data: dict[str, Any]) -> None:
    with _lock:
        _jobs[job_id] = data
    _persist_jobs()


def _patch_job(job_id: str, **changes: Any) -> None:
    now = datetime.now()
    with _lock:
        if job_id not in _jobs:
            return
        _jobs[job_id].update(changes)
        _jobs[job_id]["updated_at"] = _iso(now)
        if _jobs[job_id].get("status") in _TERMINAL_STATUSES:
            _jobs[job_id]["expires_at"] = _expires_at(now)
    _persist_jobs()


def _is_cancel_requested(job_id: str) -> bool:
    with _lock:
        return bool(_jobs.get(job_id, {}).get("cancel_requested"))


def _persist_jobs() -> None:
    try:
        JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = JOBS_FILE.with_name(f".{JOBS_FILE.name}.tmp")
        with _lock:
            snapshot = list(_jobs.values())
        tmp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(JOBS_FILE)
    except Exception:
        logger.exception("Failed to persist job state")


def _load_jobs_from_disk() -> None:
    if not JOBS_FILE.exists():
        return
    try:
        data = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return
        for job in data:
            job_id = job.get("job_id")
            if not job_id:
                continue
            if job.get("status") not in _TERMINAL_STATUSES:
                now = datetime.now()
                job["status"] = "failed"
                job["stage"] = "failed"
                job["message"] = "failed"
                job["error"] = "service restarted while job was active"
                job["updated_at"] = _iso(now)
                job["expires_at"] = _expires_at(now)
            job.setdefault("stage", job.get("status", ""))
            job.setdefault("expires_at", "")
            job.setdefault("retry_count", 0)
            job.setdefault("max_retries", 0)
            _jobs[job_id] = job
        cleanup_jobs()
    except Exception:
        logger.exception("Failed to load job state from disk")


def _coerce_max_retries(value: int | None) -> int:
    if value is None:
        value = DEFAULT_JOB_MAX_RETRIES
    try:
        return max(0, min(int(value), MAX_JOB_RETRIES))
    except (TypeError, ValueError):
        return DEFAULT_JOB_MAX_RETRIES


def _job_max_retries(job_id: str) -> int:
    with _lock:
        return _coerce_max_retries(_jobs.get(job_id, {}).get("max_retries"))


def _is_expired_terminal_job(job: dict[str, Any], now: datetime, ttl_seconds: int) -> bool:
    if job.get("status") not in _TERMINAL_STATUSES:
        return False
    expires_at = _parse_datetime(str(job.get("expires_at") or ""))
    if expires_at is not None:
        return expires_at <= now
    base = _parse_datetime(str(job.get("updated_at") or "")) or _parse_datetime(str(job.get("created_at") or ""))
    return base is not None and base + timedelta(seconds=ttl_seconds) <= now


def _expires_at(now: datetime) -> str:
    if DEFAULT_JOB_TTL_SECONDS <= 0:
        return ""
    return _iso(now + timedelta(seconds=DEFAULT_JOB_TTL_SECONDS))


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


_load_jobs_from_disk()
