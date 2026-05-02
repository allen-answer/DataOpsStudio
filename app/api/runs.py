"""异步任务（compare 或 workflow run）的状态查询与取消。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.models import JobInfo
from app.services.jobs import cancel_job, get_job


router = APIRouter()


@router.get("/api/runs/{job_id}", response_model=JobInfo)
def run_status_api(job_id: str):
    try:
        return get_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Run not found") from exc


@router.post("/api/runs/{job_id}/cancel", response_model=JobInfo)
def cancel_run_api(job_id: str):
    try:
        return cancel_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Run not found") from exc
