from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from app.models import Workflow, WorkflowStatus
from app.services.jobs import get_job, submit_workflow_run
from app.services.repositories import workflow_store


logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = int(os.getenv("DATAOPS_SCHEDULER_INTERVAL_SECONDS", "60"))
DEFAULT_ENABLED = os.getenv("DATAOPS_SCHEDULER_ENABLED", "true").lower() not in {"0", "false", "no"}
DEFAULT_MAX_RETRIES = int(os.getenv("DATAOPS_SCHEDULER_MAX_RETRIES", "0"))
_ACTIVE_JOB_STATUSES = {"queued", "running", "cancelling"}


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
class SchedulerState:
    enabled: bool = DEFAULT_ENABLED
    running: bool = False
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS
    entries: dict[str, ScheduleEntry] = field(default_factory=dict)


_state = SchedulerState()
_lock = threading.RLock()
_stop_event = threading.Event()
_thread: threading.Thread | None = None


def start_scheduler(interval_seconds: int | None = None) -> dict[str, Any]:
    global _thread
    with _lock:
        if interval_seconds is not None:
            _state.interval_seconds = max(1, int(interval_seconds))
        _state.enabled = True
        if _state.running and _thread and _thread.is_alive():
            return scheduler_status()
        _stop_event.clear()
        _thread = threading.Thread(target=_loop, name="dataops-scheduler", daemon=True)
        _state.running = True
        _thread.start()
    return scheduler_status()


def stop_scheduler() -> dict[str, Any]:
    global _thread
    _stop_event.set()
    thread = _thread
    if thread and thread.is_alive():
        thread.join(timeout=2)
    with _lock:
        _state.running = False
        _thread = None
    return scheduler_status()


def scheduler_status() -> dict[str, Any]:
    with _lock:
        return {
            "enabled": _state.enabled,
            "running": _state.running,
            "interval_seconds": _state.interval_seconds,
            "entries": [
                {
                    "workflow_id": entry.workflow_id,
                    "workflow_name": entry.workflow_name,
                    "cron": entry.cron,
                    "next_run_at": entry.next_run_at,
                    "last_run_at": entry.last_run_at,
                    "last_job_id": entry.last_job_id,
                    "last_error": entry.last_error,
                    "skipped_overlap": entry.skipped_overlap,
                }
                for entry in sorted(_state.entries.values(), key=lambda item: item.workflow_name)
            ],
        }


def tick(now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now()
    submitted: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    workflows = workflow_store.list()
    active_ids = {workflow.id for workflow in workflows if _is_schedulable(workflow)}
    with _lock:
        for stale_id in list(_state.entries):
            if stale_id not in active_ids:
                _state.entries.pop(stale_id, None)

    for workflow in workflows:
        if not _is_schedulable(workflow):
            continue
        try:
            entry = _entry_for(workflow, now)
            due_at = _parse_iso(entry.next_run_at)
            if due_at is None or due_at > now:
                continue
            if entry.last_job_id and _is_active_job(entry.last_job_id):
                entry.skipped_overlap += 1
                entry.last_error = "previous scheduled job still running"
                entry.next_run_at = _iso(next_run_after(workflow.schedule_cron, now))
                continue
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
    return {"submitted": submitted, "errors": errors, "status": scheduler_status()}


def next_run_after(expression: str, after: datetime) -> datetime:
    schedule = CronSchedule.parse(expression)
    candidate = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    max_minutes = 366 * 24 * 60 * 5
    for _ in range(max_minutes):
        if schedule.matches(candidate):
            return candidate
        candidate += timedelta(minutes=1)
    raise ValueError(f"cron expression has no run in the next 5 years: {expression}")


def _loop() -> None:
    while not _stop_event.wait(_state.interval_seconds):
        if _state.enabled:
            tick()
    with _lock:
        _state.running = False


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


def _is_schedulable(workflow: Workflow) -> bool:
    status = getattr(workflow.status, "value", workflow.status)
    return status == WorkflowStatus.ACTIVE.value and bool(workflow.schedule_cron.strip())


def _is_active_job(job_id: str) -> bool:
    try:
        job = get_job(job_id)
    except KeyError:
        return False
    return str(job.get("status") or "") in _ACTIVE_JOB_STATUSES


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


def reset_scheduler_state_for_tests() -> None:
    stop_scheduler()
    with _lock:
        _state.enabled = DEFAULT_ENABLED
        _state.interval_seconds = DEFAULT_INTERVAL_SECONDS
        _state.entries.clear()
