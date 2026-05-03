from __future__ import annotations

from fastapi import APIRouter, Body

from app.services.scheduler import scheduler_status, start_scheduler, stop_scheduler, tick


router = APIRouter()


@router.get("/api/scheduler/status")
def scheduler_status_api():
    return scheduler_status()


@router.post("/api/scheduler/start")
def scheduler_start_api(payload: dict[str, object] | None = Body(None)):
    payload = payload or {}
    interval = payload.get("interval_seconds")
    return start_scheduler(int(interval)) if interval is not None else start_scheduler()


@router.post("/api/scheduler/stop")
def scheduler_stop_api():
    return stop_scheduler()


@router.post("/api/scheduler/tick")
def scheduler_tick_api():
    return tick()
