from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from concurrent.futures import Future
from threading import Lock
from typing import Any
from uuid import uuid4

from app.services.runner import run_task


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


def _set_job(job_id: str, data: dict[str, Any]) -> None:
    with _lock:
        _jobs[job_id] = data


def _patch_job(job_id: str, **changes: Any) -> None:
    with _lock:
        if job_id not in _jobs:
            return
        _jobs[job_id].update(changes)
        _jobs[job_id]["updated_at"] = datetime.now().isoformat(timespec="seconds")


def _is_cancel_requested(job_id: str) -> bool:
    with _lock:
        return bool(_jobs.get(job_id, {}).get("cancel_requested"))
