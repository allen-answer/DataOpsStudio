"""Sensor / 事件触发器（Phase 7 F · 调度器扩展）。

支持的 trigger type：
- `file`：监听文件路径。config = {path, mode: "exists" | "newer_than", check_size}
  - exists：文件首次出现时 fire（之后只在被外部移除并重新出现时再次 fire）
  - newer_than：文件 mtime 超过 last_fired_mtime 时 fire（每次"重新落地"都触发）
  - check_size：mode=newer_than 时附加判断"size 也变了"，避免被 touch 触发
- `workflow_success`：监听上游 workflow 的成功 run。config = {workflow_id}
  - 每次上游 workflow 出现一条 status=success 的 run 且 finished_at > last_fired_at 时 fire
- 后续可扩展：webhook（外部 push）、time_window（在 [start, end] 时间段内每 N 分钟）等

Sensor 状态由调度器维护（非持久化；进程重启重新探测当前状态）。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SensorState:
    """单个 sensor 的运行时状态（不入库；进程重启就忘）。"""
    last_fired_at: str = ""              # ISO timestamp，最近一次 fire（防止重复 fire）
    last_fired_mtime: float = 0.0        # file sensor newer_than 模式记录 mtime
    last_fired_size: int = -1            # file sensor check_size 时记录上次 size
    last_seen_run_id: str = ""           # workflow_success 模式：最近一次见过的 SUCCESS run_id
    fire_count: int = 0
    last_error: str = ""


@dataclass
class SensorEvalResult:
    fired: bool
    reason: str = ""
    new_state: SensorState = field(default_factory=SensorState)


def evaluate_sensor(
    sensor: dict[str, Any],
    state: SensorState,
    *,
    workflow_run_lookup=None,
) -> SensorEvalResult:
    """给 sensor 配置 + 上次状态，判断这一次是否应该 fire。

    `workflow_run_lookup(workflow_id) -> list[dict]`：runtime 注入，避免本模块依赖
    workflow_history 触发循环 import。返回的 list 应按时间倒序，每项至少有 run_id /
    status / finished_at。
    """
    if not sensor.get("enabled", True):
        return SensorEvalResult(fired=False, reason="disabled", new_state=state)
    sensor_type = str(sensor.get("type") or "").lower()
    config = sensor.get("config") or {}
    if sensor_type == "file":
        return _evaluate_file_sensor(config, state)
    if sensor_type == "workflow_success":
        return _evaluate_workflow_success_sensor(config, state, workflow_run_lookup)
    return SensorEvalResult(fired=False, reason=f"unknown sensor type: {sensor_type}", new_state=state)


def _evaluate_file_sensor(config: dict[str, Any], state: SensorState) -> SensorEvalResult:
    path = str(config.get("path") or "").strip()
    if not path:
        return SensorEvalResult(fired=False, reason="missing path", new_state=state)
    mode = str(config.get("mode") or "exists").lower()
    check_size = bool(config.get("check_size"))
    try:
        stat = os.stat(path)
    except FileNotFoundError:
        # 文件不在 —— 重置 mtime/size，让下次出现时按"首次"判定
        new_state = SensorState(
            last_fired_at=state.last_fired_at,
            last_fired_mtime=0.0,
            last_fired_size=-1,
            last_seen_run_id=state.last_seen_run_id,
            fire_count=state.fire_count,
        )
        return SensorEvalResult(fired=False, reason=f"file not found: {path}", new_state=new_state)
    except Exception as exc:
        return SensorEvalResult(fired=False, reason=f"stat error: {exc}", new_state=state)

    mtime = stat.st_mtime
    size = stat.st_size

    if mode == "exists":
        # 文件存在 —— 只在 last_fired_mtime=0（之前不存在 / 重置过）时 fire 一次
        if state.last_fired_mtime > 0:
            return SensorEvalResult(fired=False, reason="already fired", new_state=state)
        new_state = _fired_state(state, mtime=mtime, size=size)
        return SensorEvalResult(fired=True, reason=f"file appeared: {path}", new_state=new_state)

    if mode == "newer_than":
        if mtime <= state.last_fired_mtime:
            return SensorEvalResult(fired=False, reason="mtime not newer", new_state=state)
        if check_size and size == state.last_fired_size:
            # mtime 变了但 size 没变（touch / chmod 之类）—— 跳过
            return SensorEvalResult(fired=False, reason="size unchanged (likely touch)", new_state=state)
        new_state = _fired_state(state, mtime=mtime, size=size)
        return SensorEvalResult(fired=True, reason=f"file updated: {path} (mtime={mtime})", new_state=new_state)

    return SensorEvalResult(fired=False, reason=f"unknown file mode: {mode}", new_state=state)


def _evaluate_workflow_success_sensor(
    config: dict[str, Any],
    state: SensorState,
    workflow_run_lookup,
) -> SensorEvalResult:
    upstream_id = str(config.get("workflow_id") or "").strip()
    if not upstream_id:
        return SensorEvalResult(fired=False, reason="missing workflow_id", new_state=state)
    if workflow_run_lookup is None:
        return SensorEvalResult(fired=False, reason="lookup unavailable", new_state=state)
    try:
        runs = workflow_run_lookup(upstream_id) or []
    except Exception as exc:
        return SensorEvalResult(fired=False, reason=f"lookup error: {exc}", new_state=state)
    success_runs = [r for r in runs if (r.get("status") or "").lower() == "success"]
    if not success_runs:
        return SensorEvalResult(fired=False, reason="no success runs", new_state=state)
    latest = success_runs[0]
    run_id = str(latest.get("run_id") or "")
    if not run_id or run_id == state.last_seen_run_id:
        return SensorEvalResult(fired=False, reason="no new success run", new_state=state)
    new_state = _fired_state(state, run_id=run_id)
    return SensorEvalResult(fired=True, reason=f"upstream success: {upstream_id} run={run_id}", new_state=new_state)


def _fired_state(prev: SensorState, *, mtime: float = 0.0, size: int = -1, run_id: str = "") -> SensorState:
    return SensorState(
        last_fired_at=datetime.now().isoformat(timespec="seconds"),
        last_fired_mtime=mtime if mtime > 0 else prev.last_fired_mtime,
        last_fired_size=size if size >= 0 else prev.last_fired_size,
        last_seen_run_id=run_id or prev.last_seen_run_id,
        fire_count=prev.fire_count + 1,
    )
