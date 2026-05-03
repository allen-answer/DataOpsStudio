from __future__ import annotations

import logging
import os
import threading
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

try:  # APScheduler is the production scheduler; fallback keeps old images testable.
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    APSCHEDULER_AVAILABLE = True
except Exception:  # pragma: no cover - exercised only in environments without the dependency
    BackgroundScheduler = None  # type: ignore[assignment]
    CronTrigger = None  # type: ignore[assignment]
    IntervalTrigger = None  # type: ignore[assignment]
    APSCHEDULER_AVAILABLE = False

from app.models import Workflow, WorkflowStatus
from app.services.jobs import get_job, submit_workflow_run
from app.dbclients.factory import fetch_rows
from app.services.repositories import datasource_store, workflow_store
from app.services.sensors import SensorState, evaluate_sensor
from app.utils.sql_guard import validate_readonly_sql


logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = int(os.getenv("DATAOPS_SCHEDULER_INTERVAL_SECONDS", "60"))
DEFAULT_ENABLED = os.getenv("DATAOPS_SCHEDULER_ENABLED", "true").lower() not in {"0", "false", "no"}
DEFAULT_MAX_RETRIES = int(os.getenv("DATAOPS_SCHEDULER_MAX_RETRIES", "0"))
_ACTIVE_JOB_STATUSES = {"queued", "running", "cancelling"}
_SYNC_JOB_ID = "__dataops_scheduler_sync__"
DEFAULT_SENSOR_INTERVAL_SECONDS = int(os.getenv("DATAOPS_SENSOR_INTERVAL_SECONDS", "60"))
DEFAULT_SENSOR_COOLDOWN_SECONDS = int(os.getenv("DATAOPS_SENSOR_COOLDOWN_SECONDS", "300"))


@dataclass
class ScheduleEntry:
    workflow_id: str
    workflow_name: str
    cron: str
    next_run_at: str = ""
    last_run_at: str = ""
    last_job_id: str = ""
    last_error: str = ""
    skipped_overlap: int = 0


@dataclass
class SensorEntry:
    workflow_id: str
    workflow_name: str
    sensor_id: str
    sensor_type: str
    interval_seconds: int = DEFAULT_SENSOR_INTERVAL_SECONDS
    cooldown_seconds: int = DEFAULT_SENSOR_COOLDOWN_SECONDS
    next_run_at: str = ""
    last_checked_at: str = ""
    last_triggered_at: str = ""
    last_job_id: str = ""
    last_error: str = ""
    last_value: str = ""
    skipped_overlap: int = 0


@dataclass
class SchedulerState:
    enabled: bool = DEFAULT_ENABLED
    running: bool = False
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS
    backend: str = "apscheduler" if APSCHEDULER_AVAILABLE else "fallback"
    entries: dict[str, ScheduleEntry] = field(default_factory=dict)
    sensor_entries: dict[str, SensorEntry] = field(default_factory=dict)


_state = SchedulerState()
_lock = threading.RLock()
_stop_event = threading.Event()
_thread: threading.Thread | None = None
_scheduler: Any | None = None
# sensor 状态：(workflow_id, sensor_index) → SensorState；进程内存活，重启清空
_sensor_states: dict[tuple[str, int], SensorState] = {}


def start_scheduler(interval_seconds: int | None = None) -> dict[str, Any]:
    """Start scheduler service and sync workflow cron definitions.

    With APScheduler installed this creates one cron job per active workflow and
    one interval sync job. In old/offline images without APScheduler, it falls
    back to the previous lightweight polling loop so tests and local dev still
    work before dependencies are rebuilt.
    """
    global _thread, _scheduler
    with _lock:
        if interval_seconds is not None:
            _state.interval_seconds = max(1, int(interval_seconds))
        _state.enabled = True
        if APSCHEDULER_AVAILABLE:
            if _scheduler is None:
                _scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
            _state.backend = "apscheduler"
            _state.running = True
            _ensure_sync_job()
            tick()
            if not _scheduler.running:
                _scheduler.start()
            return scheduler_status()

        _state.backend = "fallback"
        if _state.running and _thread and _thread.is_alive():
            return scheduler_status()
        _stop_event.clear()
        tick()
        _thread = threading.Thread(target=_fallback_loop, name="dataops-scheduler", daemon=True)
        _state.running = True
        _thread.start()
    return scheduler_status()


def stop_scheduler() -> dict[str, Any]:
    global _thread, _scheduler
    if APSCHEDULER_AVAILABLE:
        with _lock:
            scheduler = _scheduler
            _scheduler = None
            _state.running = False
            _state.entries.clear()
            _state.sensor_entries.clear()
        if scheduler is not None and scheduler.running:
            scheduler.shutdown(wait=False)
        return scheduler_status()

    _stop_event.set()
    thread = _thread
    if thread and thread.is_alive():
        thread.join(timeout=2)
    with _lock:
        _state.running = False
        _thread = None
        _state.entries.clear()
        _state.sensor_entries.clear()
    return scheduler_status()


def scheduler_status() -> dict[str, Any]:
    with _lock:
        running = bool(_scheduler and _scheduler.running) if APSCHEDULER_AVAILABLE else _state.running
        return {
            "enabled": _state.enabled,
            "running": running,
            "backend": _state.backend,
            "interval_seconds": _state.interval_seconds,
            "entries": [
                {
                    "workflow_id": entry.workflow_id,
                    "workflow_name": entry.workflow_name,
                    "cron": entry.cron,
                    "next_run_at": _job_next_run_at(entry.workflow_id) or entry.next_run_at,
                    "last_run_at": entry.last_run_at,
                    "last_job_id": entry.last_job_id,
                    "last_error": entry.last_error,
                    "skipped_overlap": entry.skipped_overlap,
                }
                for entry in sorted(_state.entries.values(), key=lambda item: item.workflow_name)
            ],
            "sensors": [
                {
                    "workflow_id": entry.workflow_id,
                    "workflow_name": entry.workflow_name,
                    "sensor_id": entry.sensor_id,
                    "type": entry.sensor_type,
                    "interval_seconds": entry.interval_seconds,
                    "cooldown_seconds": entry.cooldown_seconds,
                    "next_run_at": _sensor_job_next_run_at(entry.workflow_id, entry.sensor_id) or entry.next_run_at,
                    "last_checked_at": entry.last_checked_at,
                    "last_triggered_at": entry.last_triggered_at,
                    "last_job_id": entry.last_job_id,
                    "last_error": entry.last_error,
                    "last_value": entry.last_value,
                    "skipped_overlap": entry.skipped_overlap,
                }
                for entry in sorted(_state.sensor_entries.values(), key=lambda item: (item.workflow_name, item.sensor_id))
            ],
        }


def tick(now: datetime | None = None) -> dict[str, Any]:
    """Synchronize scheduler jobs with current workflow definitions.

    APScheduler handles actual cron firing. The fallback path keeps the previous
    manual due-check behavior for environments where APScheduler is not yet
    installed. Sensor / event triggers are also polled here regardless of backend.
    """
    if APSCHEDULER_AVAILABLE:
        errors = _sync_jobs(now or datetime.now())
        sensor_submitted = _tick_sensors()
        return {"submitted": sensor_submitted, "errors": errors, "status": scheduler_status()}
    return _fallback_tick(now)


def next_run_after(expression: str, after: datetime) -> datetime:
    """Compatibility helper used by tests and fallback mode."""
    schedule = CronSchedule.parse(expression)
    candidate = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    max_minutes = 366 * 24 * 60 * 5
    for _ in range(max_minutes):
        if schedule.matches(candidate):
            return candidate
        candidate += timedelta(minutes=1)
    raise ValueError(f"cron expression has no run in the next 5 years: {expression}")


def reset_scheduler_state_for_tests() -> None:
    stop_scheduler()
    with _lock:
        _state.enabled = DEFAULT_ENABLED
        _state.interval_seconds = DEFAULT_INTERVAL_SECONDS
        _state.backend = "apscheduler" if APSCHEDULER_AVAILABLE else "fallback"
        _state.entries.clear()
        _state.sensor_entries.clear()
        _sensor_states.clear()


def _ensure_sync_job() -> None:
    if not APSCHEDULER_AVAILABLE or _scheduler is None:
        return
    if _scheduler.get_job(_SYNC_JOB_ID):
        _scheduler.reschedule_job(
            _SYNC_JOB_ID,
            trigger=IntervalTrigger(seconds=max(1, _state.interval_seconds)),
        )
        return
    _scheduler.add_job(
        tick,
        trigger=IntervalTrigger(seconds=max(1, _state.interval_seconds)),
        id=_SYNC_JOB_ID,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )


def _sync_jobs(now: datetime) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    workflows = workflow_store.list()
    active = {workflow.id: workflow for workflow in workflows if _is_schedulable(workflow)}
    active_sensors: dict[str, tuple[Workflow, dict[str, Any]]] = {}
    for workflow in workflows:
        if _workflow_is_active(workflow):
            for sensor in _workflow_sensors(workflow):
                active_sensors[_sensor_key(workflow.id, sensor["id"])] = (workflow, sensor)

    with _lock:
        for stale_id in list(_state.entries):
            if stale_id not in active:
                _remove_job_locked(stale_id)
                _state.entries.pop(stale_id, None)
        for stale_key in list(_state.sensor_entries):
            if stale_key not in active_sensors:
                entry = _state.sensor_entries.pop(stale_key, None)
                if entry:
                    _remove_sensor_job_locked(entry.workflow_id, entry.sensor_id)

    for workflow in active.values():
        try:
            _entry_for(workflow, now)
            _upsert_job(workflow)
        except Exception as exc:
            logger.exception("workflow schedule sync failed workflow_id=%s", workflow.id)
            errors.append({"workflow_id": workflow.id, "error": str(exc)})
            with _lock:
                entry = _state.entries.get(workflow.id)
                if entry is not None:
                    entry.last_error = str(exc)
    for workflow, sensor in active_sensors.values():
        try:
            _sensor_entry_for(workflow, sensor, now)
            _upsert_sensor_job(workflow, sensor)
        except Exception as exc:
            logger.exception("workflow sensor sync failed workflow_id=%s sensor_id=%s", workflow.id, sensor.get("id"))
            errors.append({"workflow_id": workflow.id, "sensor_id": str(sensor.get("id") or ""), "error": str(exc)})
            with _lock:
                entry = _state.sensor_entries.get(_sensor_key(workflow.id, str(sensor.get("id") or "")))
                if entry is not None:
                    entry.last_error = str(exc)
    return errors


def _upsert_job(workflow: Workflow) -> None:
    if not APSCHEDULER_AVAILABLE or _scheduler is None:
        return
    job_id = _job_id(workflow.id)
    trigger = CronTrigger.from_crontab(workflow.schedule_cron, timezone="Asia/Shanghai")
    existing = _scheduler.get_job(job_id)
    if existing is None:
        _scheduler.add_job(
            _run_scheduled_workflow,
            trigger=trigger,
            args=[workflow.id],
            id=job_id,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
    else:
        _scheduler.reschedule_job(job_id, trigger=trigger)
    with _lock:
        entry = _state.entries.get(workflow.id)
        if entry is not None:
            entry.next_run_at = _job_next_run_at(workflow.id) or entry.next_run_at


def _upsert_sensor_job(workflow: Workflow, sensor: dict[str, Any]) -> None:
    if not APSCHEDULER_AVAILABLE or _scheduler is None:
        return
    sensor_id = str(sensor["id"])
    job_id = _sensor_job_id(workflow.id, sensor_id)
    trigger = IntervalTrigger(seconds=_sensor_interval(sensor))
    existing = _scheduler.get_job(job_id)
    if existing is None:
        _scheduler.add_job(
            _run_sensor_workflow,
            trigger=trigger,
            args=[workflow.id, sensor_id],
            id=job_id,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
    else:
        _scheduler.reschedule_job(job_id, trigger=trigger)
    with _lock:
        entry = _state.sensor_entries.get(_sensor_key(workflow.id, sensor_id))
        if entry is not None:
            entry.next_run_at = _sensor_job_next_run_at(workflow.id, sensor_id) or entry.next_run_at


def _run_scheduled_workflow(workflow_id: str) -> None:
    workflow = workflow_store.get(workflow_id)
    if workflow is None or not _is_schedulable(workflow):
        tick()
        return
    with _lock:
        entry = _entry_for(workflow, datetime.now())
        if entry.last_job_id and _is_active_job(entry.last_job_id):
            entry.skipped_overlap += 1
            entry.last_error = "previous scheduled job still running"
            return
    try:
        job = submit_workflow_run(
            workflow.id,
            {},
            max_retries=DEFAULT_MAX_RETRIES,
            trigger="schedule",
        )
        with _lock:
            entry = _entry_for(workflow, datetime.now())
            entry.last_run_at = _iso(datetime.now())
            entry.last_job_id = str(job.get("job_id") or "")
            entry.last_error = ""
            entry.next_run_at = _job_next_run_at(workflow.id) or entry.next_run_at
    except Exception as exc:
        logger.exception("workflow scheduled submit failed workflow_id=%s", workflow.id)
        with _lock:
            entry = _entry_for(workflow, datetime.now())
            entry.last_error = str(exc)


def _run_sensor_workflow(workflow_id: str, sensor_id: str) -> None:
    workflow = workflow_store.get(workflow_id)
    if workflow is None or not _workflow_is_active(workflow):
        tick()
        return
    sensor = _sensor_by_id(workflow, sensor_id)
    if sensor is None:
        tick()
        return
    now = datetime.now()
    try:
        _evaluate_sensor_and_maybe_submit(workflow, sensor, now)
    except Exception as exc:
        logger.exception("workflow sensor check failed workflow_id=%s sensor_id=%s", workflow_id, sensor_id)
        with _lock:
            entry = _sensor_entry_for(workflow, sensor, now)
            entry.last_checked_at = _iso(now)
            entry.last_error = str(exc)


def _fallback_loop() -> None:
    while not _stop_event.wait(_state.interval_seconds):
        if _state.enabled:
            tick()
    with _lock:
        _state.running = False


def _fallback_tick(now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now()
    submitted: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    workflows = workflow_store.list()
    active_ids = {workflow.id for workflow in workflows if _is_schedulable(workflow)}
    active_sensor_ids = {
        _sensor_key(workflow.id, sensor["id"])
        for workflow in workflows
        if _workflow_is_active(workflow)
        for sensor in _workflow_sensors(workflow)
    }
    with _lock:
        for stale_id in list(_state.entries):
            if stale_id not in active_ids:
                _state.entries.pop(stale_id, None)
        for stale_id in list(_state.sensor_entries):
            if stale_id not in active_sensor_ids:
                _state.sensor_entries.pop(stale_id, None)

    for workflow in workflows:
        if _is_schedulable(workflow):
            try:
                entry = _entry_for(workflow, now)
                due_at = _parse_iso(entry.next_run_at)
                if due_at is not None and due_at <= now:
                    if entry.last_job_id and _is_active_job(entry.last_job_id):
                        entry.skipped_overlap += 1
                        entry.last_error = "previous scheduled job still running"
                        entry.next_run_at = _iso(next_run_after(workflow.schedule_cron, now))
                    else:
                        job = submit_workflow_run(
                            workflow.id,
                            {},
                            max_retries=DEFAULT_MAX_RETRIES,
                            trigger="schedule",
                        )
                        entry.last_run_at = _iso(now)
                        entry.last_job_id = str(job.get("job_id") or "")
                        entry.last_error = ""
                        entry.next_run_at = _iso(next_run_after(workflow.schedule_cron, now))
                        submitted.append({"workflow_id": workflow.id, "job_id": entry.last_job_id})
            except Exception as exc:
                logger.exception("workflow schedule tick failed workflow_id=%s", workflow.id)
                errors.append({"workflow_id": workflow.id, "error": str(exc)})
                with _lock:
                    entry = _state.entries.get(workflow.id)
                    if entry is not None:
                        entry.last_error = str(exc)
        if _workflow_is_active(workflow):
            for sensor in _workflow_sensors(workflow):
                try:
                    entry = _sensor_entry_for(workflow, sensor, now)
                    due_at = _parse_iso(entry.next_run_at)
                    if due_at is None or due_at > now:
                        continue
                    job = _evaluate_sensor_and_maybe_submit(workflow, sensor, now)
                    entry.next_run_at = _iso(now + timedelta(seconds=_sensor_interval(sensor)))
                    if job:
                        submitted.append({"workflow_id": workflow.id, "sensor_id": sensor["id"], "job_id": entry.last_job_id})
                except Exception as exc:
                    logger.exception("workflow sensor tick failed workflow_id=%s sensor_id=%s", workflow.id, sensor.get("id"))
                    errors.append({"workflow_id": workflow.id, "sensor_id": str(sensor.get("id") or ""), "error": str(exc)})
                    with _lock:
                        entry = _state.sensor_entries.get(_sensor_key(workflow.id, str(sensor.get("id") or "")))
                        if entry is not None:
                            entry.last_error = str(exc)
    submitted.extend(_tick_sensors())
    return {"submitted": submitted, "errors": errors, "status": scheduler_status()}


def _tick_sensors() -> list[dict[str, Any]]:
    """轮询所有 active workflow 的 triggers，命中即提交 run。"""
    submitted: list[dict[str, Any]] = []
    workflows = workflow_store.list()
    for workflow in workflows:
        status_value = getattr(workflow.status, "value", workflow.status)
        if status_value != WorkflowStatus.ACTIVE.value:
            continue
        triggers = list(getattr(workflow, "triggers", None) or [])
        if not triggers:
            continue
        for index, sensor in enumerate(triggers):
            if not isinstance(sensor, dict):
                continue
            key = (workflow.id, index)
            with _lock:
                state = _sensor_states.get(key) or SensorState()
            try:
                eval_result = evaluate_sensor(sensor, state, workflow_run_lookup=_lookup_runs)
            except Exception as exc:
                logger.exception("sensor evaluation crashed workflow_id=%s index=%s", workflow.id, index)
                with _lock:
                    _sensor_states[key] = state
                continue
            with _lock:
                _sensor_states[key] = eval_result.new_state
            if not eval_result.fired:
                continue
            try:
                job = submit_workflow_run(
                    workflow.id,
                    {},
                    max_retries=DEFAULT_MAX_RETRIES,
                    trigger=f"sensor:{sensor.get('type', 'unknown')}",
                )
                submitted.append({
                    "workflow_id": workflow.id,
                    "job_id": str(job.get("job_id") or ""),
                    "sensor_type": sensor.get("type", ""),
                    "sensor_index": index,
                    "reason": eval_result.reason,
                })
            except Exception:
                logger.exception("sensor submit_workflow_run failed workflow_id=%s", workflow.id)
    return submitted


def _lookup_runs(workflow_id: str) -> list[dict[str, Any]]:
    """workflow_success sensor 用：拿上游 workflow 的最近 runs。"""
    from app.services.workflow_history import list_workflow_runs
    summaries = list_workflow_runs(workflow_id, limit=20)
    return [s.model_dump() if hasattr(s, "model_dump") else dict(s) for s in summaries]


def _entry_for(workflow: Workflow, now: datetime) -> ScheduleEntry:
    with _lock:
        existing = _state.entries.get(workflow.id)
        if existing is None or existing.cron != workflow.schedule_cron:
            existing = ScheduleEntry(
                workflow_id=workflow.id,
                workflow_name=workflow.name,
                cron=workflow.schedule_cron,
                next_run_at=_iso(next_run_after(workflow.schedule_cron, now - timedelta(minutes=1))),
            )
            _state.entries[workflow.id] = existing
        else:
            existing.workflow_name = workflow.name
        return existing


def _sensor_entry_for(workflow: Workflow, sensor: dict[str, Any], now: datetime) -> SensorEntry:
    sensor_id = str(sensor["id"])
    key = _sensor_key(workflow.id, sensor_id)
    with _lock:
        existing = _state.sensor_entries.get(key)
        interval = _sensor_interval(sensor)
        cooldown = _sensor_cooldown(sensor)
        if (
            existing is None
            or existing.sensor_type != sensor["type"]
            or existing.interval_seconds != interval
            or existing.cooldown_seconds != cooldown
        ):
            existing = SensorEntry(
                workflow_id=workflow.id,
                workflow_name=workflow.name,
                sensor_id=sensor_id,
                sensor_type=str(sensor["type"]),
                interval_seconds=interval,
                cooldown_seconds=cooldown,
                next_run_at=_iso(now),
            )
            _state.sensor_entries[key] = existing
        else:
            existing.workflow_name = workflow.name
        return existing


def _is_schedulable(workflow: Workflow) -> bool:
    return _workflow_is_active(workflow) and bool(workflow.schedule_cron.strip())


def _workflow_is_active(workflow: Workflow) -> bool:
    status = getattr(workflow.status, "value", workflow.status)
    return status == WorkflowStatus.ACTIVE.value


def _is_active_job(job_id: str) -> bool:
    try:
        job = get_job(job_id)
    except KeyError:
        return False
    return str(job.get("status") or "") in _ACTIVE_JOB_STATUSES


def _job_id(workflow_id: str) -> str:
    return f"workflow:{workflow_id}"


def _sensor_key(workflow_id: str, sensor_id: str) -> str:
    return f"{workflow_id}:{sensor_id}"


def _sensor_job_id(workflow_id: str, sensor_id: str) -> str:
    return f"sensor:{workflow_id}:{sensor_id}"


def _job_next_run_at(workflow_id: str) -> str:
    if not APSCHEDULER_AVAILABLE or _scheduler is None:
        return ""
    job = _scheduler.get_job(_job_id(workflow_id))
    next_run = getattr(job, "next_run_time", None) if job is not None else None
    if next_run is None:
        return ""
    if getattr(next_run, "tzinfo", None) is not None:
        next_run = next_run.replace(tzinfo=None)
    return _iso(next_run)


def _sensor_job_next_run_at(workflow_id: str, sensor_id: str) -> str:
    if not APSCHEDULER_AVAILABLE or _scheduler is None:
        return ""
    job = _scheduler.get_job(_sensor_job_id(workflow_id, sensor_id))
    next_run = getattr(job, "next_run_time", None) if job is not None else None
    if next_run is None:
        return ""
    if getattr(next_run, "tzinfo", None) is not None:
        next_run = next_run.replace(tzinfo=None)
    return _iso(next_run)


def _remove_job_locked(workflow_id: str) -> None:
    if not APSCHEDULER_AVAILABLE or _scheduler is None:
        return
    job_id = _job_id(workflow_id)
    if _scheduler.get_job(job_id):
        _scheduler.remove_job(job_id)


def _remove_sensor_job_locked(workflow_id: str, sensor_id: str) -> None:
    if not APSCHEDULER_AVAILABLE or _scheduler is None:
        return
    job_id = _sensor_job_id(workflow_id, sensor_id)
    if _scheduler.get_job(job_id):
        _scheduler.remove_job(job_id)


def _workflow_sensors(workflow: Workflow) -> list[dict[str, Any]]:
    sensors: list[dict[str, Any]] = []
    for index, raw in enumerate(getattr(workflow, "sensors", []) or [], start=1):
        if not isinstance(raw, dict) or raw.get("enabled", True) is False:
            continue
        sensor_type = str(raw.get("type") or "").strip().lower()
        if sensor_type not in {"sql", "http"}:
            continue
        sensor = dict(raw)
        sensor["type"] = sensor_type
        sensor["id"] = str(sensor.get("id") or f"{sensor_type}_{index}").strip()
        sensors.append(sensor)
    return sensors


def _sensor_by_id(workflow: Workflow, sensor_id: str) -> dict[str, Any] | None:
    for sensor in _workflow_sensors(workflow):
        if sensor["id"] == sensor_id:
            return sensor
    return None


def _sensor_interval(sensor: dict[str, Any]) -> int:
    return max(5, int(sensor.get("interval_seconds") or DEFAULT_SENSOR_INTERVAL_SECONDS))


def _sensor_cooldown(sensor: dict[str, Any]) -> int:
    return max(0, int(sensor.get("cooldown_seconds") or DEFAULT_SENSOR_COOLDOWN_SECONDS))


def _evaluate_sensor_and_maybe_submit(workflow: Workflow, sensor: dict[str, Any], now: datetime) -> dict[str, Any] | None:
    entry = _sensor_entry_for(workflow, sensor, now)
    with _lock:
        if entry.last_job_id and _is_active_job(entry.last_job_id):
            entry.skipped_overlap += 1
            entry.last_checked_at = _iso(now)
            entry.last_error = "previous sensor-triggered job still running"
            return None
        if entry.last_triggered_at:
            triggered_at = _parse_iso(entry.last_triggered_at)
            if triggered_at is not None and (now - triggered_at).total_seconds() < entry.cooldown_seconds:
                entry.last_checked_at = _iso(now)
                entry.last_error = ""
                return None

    matched, value = _evaluate_sensor(sensor)
    with _lock:
        entry.last_checked_at = _iso(now)
        entry.last_value = str(value)
        entry.last_error = ""
    if not matched:
        return None
    job = submit_workflow_run(
        workflow.id,
        {},
        max_retries=DEFAULT_MAX_RETRIES,
        trigger="sensor",
    )
    with _lock:
        entry.last_triggered_at = _iso(now)
        entry.last_job_id = str(job.get("job_id") or "")
        entry.last_error = ""
        entry.next_run_at = _sensor_job_next_run_at(workflow.id, entry.sensor_id) or entry.next_run_at
    return job


def _evaluate_sensor(sensor: dict[str, Any]) -> tuple[bool, Any]:
    if sensor["type"] == "sql":
        return _evaluate_sql_sensor(sensor)
    if sensor["type"] == "http":
        return _evaluate_http_sensor(sensor)
    return False, ""


def _evaluate_sql_sensor(sensor: dict[str, Any]) -> tuple[bool, Any]:
    datasource_id = str(sensor.get("datasource_id") or sensor.get("datasource") or "").strip()
    sql_text = str(sensor.get("sql") or "").strip()
    if not datasource_id:
        raise ValueError("sql sensor requires datasource_id")
    if not sql_text:
        raise ValueError("sql sensor requires sql")
    validate_readonly_sql(sql_text)
    datasource = datasource_store.get(datasource_id)
    if datasource is None:
        raise ValueError(f"datasource not found: {datasource_id}")
    rows = fetch_rows(datasource, sql_text, max_rows=1, raise_on_overflow=False)
    value = _first_cell(rows)
    return _truthy(value), value


def _evaluate_http_sensor(sensor: dict[str, Any]) -> tuple[bool, Any]:
    url = str(sensor.get("url") or "").strip()
    if not url:
        raise ValueError("http sensor requires url")
    method = str(sensor.get("method") or "GET").upper()
    timeout = float(sensor.get("timeout_seconds") or 10)
    request = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - user-configured internal sensor endpoint
        status = int(getattr(response, "status", 200))
        body = response.read(256 * 1024)
    expect_status = int(sensor.get("expect_status") or 200)
    if status != expect_status:
        return False, f"http {status}"
    if sensor.get("json_path"):
        import json

        value = _json_path_value(json.loads(body.decode("utf-8")), str(sensor.get("json_path") or ""))
        if sensor.get("equals") not in (None, ""):
            return str(value) == str(sensor.get("equals")), value
        return _truthy(value), value
    return True, f"http {status}"


def _first_cell(rows: list[dict[str, Any]]) -> Any:
    if not rows:
        return None
    row = rows[0]
    if not row:
        return None
    return next(iter(row.values()))


def _truthy(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() not in {"", "0", "false", "no", "n", "none", "null"}


def _json_path_value(payload: Any, path: str) -> Any:
    current = payload
    for part in path.strip().lstrip("$.").split("."):
        if not part:
            continue
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            current = current[index] if index < len(current) else None
        else:
            return None
    return current


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


@dataclass(frozen=True)
class CronSchedule:
    minutes: set[int]
    hours: set[int]
    days: set[int]
    months: set[int]
    weekdays: set[int]
    day_wildcard: bool
    weekday_wildcard: bool

    @classmethod
    def parse(cls, expression: str) -> "CronSchedule":
        fields = expression.split()
        if len(fields) != 5:
            raise ValueError("cron must have 5 fields: minute hour day month weekday")
        minutes, hours, days, months, weekdays = fields
        day_values, day_wildcard = _parse_field(days, minimum=1, maximum=31)
        weekday_values, weekday_wildcard = _parse_field(weekdays, minimum=0, maximum=7, normalize_7_to_0=True)
        return cls(
            minutes=_parse_field(minutes, minimum=0, maximum=59)[0],
            hours=_parse_field(hours, minimum=0, maximum=23)[0],
            days=day_values,
            months=_parse_field(months, minimum=1, maximum=12)[0],
            weekdays=weekday_values,
            day_wildcard=day_wildcard,
            weekday_wildcard=weekday_wildcard,
        )

    def matches(self, value: datetime) -> bool:
        if value.minute not in self.minutes or value.hour not in self.hours or value.month not in self.months:
            return False
        day_match = value.day in self.days
        weekday = (value.weekday() + 1) % 7
        weekday_match = weekday in self.weekdays
        if not self.day_wildcard and not self.weekday_wildcard:
            return day_match or weekday_match
        return day_match and weekday_match


def _parse_field(
    field: str,
    *,
    minimum: int,
    maximum: int,
    normalize_7_to_0: bool = False,
) -> tuple[set[int], bool]:
    if not field:
        raise ValueError("empty cron field")
    result: set[int] = set()
    wildcard = field == "*"
    for part in field.split(","):
        part = part.strip()
        if not part:
            raise ValueError("empty cron field segment")
        if "/" in part:
            base, step_text = part.split("/", 1)
            step = int(step_text)
            if step <= 0:
                raise ValueError("cron step must be positive")
        else:
            base, step = part, 1
        if base == "*":
            start, end = minimum, 6 if normalize_7_to_0 else maximum
            wildcard = True
        elif "-" in base:
            start_text, end_text = base.split("-", 1)
            start, end = int(start_text), int(end_text)
        else:
            start = end = int(base)
        if normalize_7_to_0:
            if start == 7:
                start = 0
            if end == 7:
                end = 0
        if start < minimum or start > maximum or end < minimum or end > maximum:
            raise ValueError(f"cron value out of range {minimum}-{maximum}: {part}")
        if start <= end:
            values = range(start, end + 1, step)
        else:
            values = list(range(start, maximum + 1, step)) + list(range(minimum, end + 1, step))
        result.update(values)
    if normalize_7_to_0:
        result.discard(7)
    return result, wildcard
