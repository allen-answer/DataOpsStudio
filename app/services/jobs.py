from __future__ import annotations

import json
import logging
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from threading import Lock
from typing import Any
from uuid import uuid4

from app.services.runner import run_task
from app.services.repositories import workflow_store
from app.services.workflow_engine import run_workflow
from app.services.workflow_history import persist_workflow_run
from app.utils.paths import JOBS_FILE

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=2)
_lock = Lock()
_jobs: dict[str, dict[str, Any]] = {}
_futures: dict[str, Future[Any]] = {}


class JobCancelled(RuntimeError):
    pass


def submit_task_run(task_id: str) -> dict[str, Any]:
    job_id = uuid4().hex
    _set_job(
        job_id,
        {
            "job_id": job_id,
            "kind": "compare",
            "task_id": task_id,
            "status": "queued",
            "message": "等待执行",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "result": None,
            "error": "",
            "cancel_requested": False,
        },
    )
    future = _executor.submit(_run_job, job_id, task_id)
    with _lock:
        _futures[job_id] = future
    return get_job(job_id)


def submit_workflow_run(workflow_id: str, variables: dict[str, str] | None = None) -> dict[str, Any]:
    job_id = uuid4().hex
    _set_job(
        job_id,
        {
            "job_id": job_id,
            "kind": "workflow",
            "workflow_id": workflow_id,
            "variables": dict(variables or {}),
            "status": "queued",
            "message": "等待执行",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "result": None,
            "error": "",
            "cancel_requested": False,
        },
    )
    future = _executor.submit(_run_workflow_job, job_id, workflow_id, dict(variables or {}))
    with _lock:
        _futures[job_id] = future
    return get_job(job_id)


def get_job(job_id: str) -> dict[str, Any]:
    with _lock:
        if job_id not in _jobs:
            raise KeyError(job_id)
        return dict(_jobs[job_id])


def cancel_job(job_id: str) -> dict[str, Any]:
    with _lock:
        if job_id not in _jobs:
            raise KeyError(job_id)
        job = _jobs[job_id]
        if job["status"] in {"success", "failed", "cancelled"}:
            return dict(job)
        job["cancel_requested"] = True
        job["status"] = "cancelling"
        job["message"] = "正在取消"
        job["updated_at"] = datetime.now().isoformat(timespec="seconds")
        future = _futures.get(job_id)
    _persist_jobs()
    if future is not None:
        future.cancel()
    return get_job(job_id)


def _run_job(job_id: str, task_id: str) -> None:
    def update(status: str, message: str) -> None:
        if _is_cancel_requested(job_id):
            raise JobCancelled("任务已取消")
        _patch_job(job_id, status=status, message=message)

    try:
        if _is_cancel_requested(job_id):
            raise JobCancelled("任务已取消")
        update("running", "开始执行")
        result = run_task(task_id, status_callback=update)
        if _is_cancel_requested(job_id):
            raise JobCancelled("任务已取消")
        _patch_job(job_id, status="success", message="执行完成", result=result.model_dump(mode="json"))
    except JobCancelled as exc:
        _patch_job(job_id, status="cancelled", message="已取消", error=str(exc))
    except Exception as exc:
        _patch_job(job_id, status="failed", message="执行失败", error=str(exc))
    finally:
        with _lock:
            _futures.pop(job_id, None)


def _run_workflow_job(job_id: str, workflow_id: str, variables: dict[str, str]) -> None:
    try:
        if _is_cancel_requested(job_id):
            _patch_job(job_id, status="cancelled", message="已取消", error="任务已取消")
            return
        _patch_job(job_id, status="running", message="开始执行")
        workflow = workflow_store.get(workflow_id)
        if workflow is None:
            raise ValueError(f"Workflow not found: {workflow_id}")
        run = run_workflow(workflow, variables, cancel_check=lambda: _is_cancel_requested(job_id))
        persist_workflow_run(run)   # best-effort — persists to results/workflow_runs/<run_id>.json
        result = run.model_dump(mode="json")
        if run.error == "cancelled":
            _patch_job(job_id, status="cancelled", message="已取消", error="任务已取消", result=result)
        elif run.status.value == "success":
            _patch_job(job_id, status="success", message="执行完成", result=result)
        else:
            _patch_job(job_id, status="failed", message="执行失败", error=run.error or "workflow failed", result=result)
    except Exception as exc:
        _patch_job(job_id, status="failed", message="执行失败", error=str(exc))
    finally:
        with _lock:
            _futures.pop(job_id, None)


def _set_job(job_id: str, data: dict[str, Any]) -> None:
    with _lock:
        _jobs[job_id] = data
    _persist_jobs()


def _patch_job(job_id: str, **changes: Any) -> None:
    with _lock:
        if job_id not in _jobs:
            return
        _jobs[job_id].update(changes)
        _jobs[job_id]["updated_at"] = datetime.now().isoformat(timespec="seconds")
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
            if job.get("status") in {"running", "queued", "cancelling"}:
                job["status"] = "failed"
                job["message"] = "执行失败"
                job["error"] = "服务重启，任务中断"
                job["updated_at"] = datetime.now().isoformat(timespec="seconds")
            _jobs[job_id] = job
    except Exception:
        logger.exception("Failed to load job state from disk")


_load_jobs_from_disk()
