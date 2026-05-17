from __future__ import annotations

from fastapi import APIRouter, Body, Depends

from app.services.auth import get_current_user, require_role
from app.services.scheduler import scheduler_status, start_scheduler, stop_scheduler, tick


# router 级 default：viewer 看 status；start/stop/tick 升级 admin（系统级控制）。
router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get("/api/scheduler/status")
def scheduler_status_api():
    return scheduler_status()


@router.post("/api/scheduler/start")
def scheduler_start_api(
    payload: dict[str, object] | None = Body(None),
    _: object = Depends(require_role("admin")),
):
    payload = payload or {}
    interval = payload.get("interval_seconds")
    return start_scheduler(int(interval)) if interval is not None else start_scheduler()


@router.post("/api/scheduler/stop")
def scheduler_stop_api(_: object = Depends(require_role("admin"))):
    return stop_scheduler()


@router.post("/api/scheduler/tick")
def scheduler_tick_api(_: object = Depends(require_role("admin"))):
    return tick()
