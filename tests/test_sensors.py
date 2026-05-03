"""Sensor / 事件触发器测试（Phase 7 F · 调度器扩展）。

覆盖：
- file sensor exists 模式：首次出现 fire / 重复 tick 不再 fire
- file sensor newer_than 模式：mtime 推进时 fire
- file sensor check_size：size 没变（touch）跳过
- workflow_success sensor：上游成功 run 时 fire / 重复不再 fire
- 调度器 tick 集成：active workflow 配 triggers 后被自动提交
"""
from __future__ import annotations

import os
import time
from datetime import datetime
from unittest.mock import patch

import pytest

from app.services.sensors import SensorState, evaluate_sensor


# ─── file sensor ──────────────────────────────────────────────────────────────


def test_file_exists_fires_once(tmp_path):
    target = tmp_path / "landing.csv"
    target.write_text("hello", encoding="utf-8")
    sensor = {"type": "file", "config": {"path": str(target), "mode": "exists"}}
    state = SensorState()

    first = evaluate_sensor(sensor, state)
    assert first.fired
    assert "appeared" in first.reason

    # 同一文件再次 tick 不应重复 fire
    second = evaluate_sensor(sensor, first.new_state)
    assert not second.fired


def test_file_exists_resets_when_file_removed(tmp_path):
    target = tmp_path / "landing.csv"
    target.write_text("x", encoding="utf-8")
    sensor = {"type": "file", "config": {"path": str(target), "mode": "exists"}}

    fired = evaluate_sensor(sensor, SensorState()).new_state
    target.unlink()
    after_unlink = evaluate_sensor(sensor, fired)
    assert not after_unlink.fired

    target.write_text("x", encoding="utf-8")
    re_appeared = evaluate_sensor(sensor, after_unlink.new_state)
    assert re_appeared.fired, "file 重新出现应再次 fire"


def test_file_newer_than_fires_on_mtime_advance(tmp_path):
    target = tmp_path / "data.csv"
    target.write_text("v1", encoding="utf-8")
    sensor = {"type": "file", "config": {"path": str(target), "mode": "newer_than"}}

    first = evaluate_sensor(sensor, SensorState())
    assert first.fired

    # mtime 没变 —— 不 fire
    second = evaluate_sensor(sensor, first.new_state)
    assert not second.fired

    # 写新内容然后强制把 mtime 推到未来 —— 避免依赖 wall clock 精度
    target.write_text("v2", encoding="utf-8")
    future = first.new_state.last_fired_mtime + 100
    os.utime(target, (future, future))
    third = evaluate_sensor(sensor, second.new_state)
    assert third.fired


def test_file_check_size_skips_pure_touch(tmp_path):
    target = tmp_path / "data.csv"
    target.write_text("hello", encoding="utf-8")
    sensor = {"type": "file", "config": {"path": str(target), "mode": "newer_than", "check_size": True}}

    first = evaluate_sensor(sensor, SensorState())
    assert first.fired

    # 模拟 touch：mtime advance 但 size 不变
    new_mtime = first.new_state.last_fired_mtime + 100
    os.utime(target, (new_mtime, new_mtime))
    second = evaluate_sensor(sensor, first.new_state)
    assert not second.fired, "size 没变应跳过 touch"


def test_file_sensor_missing_path_no_fire(tmp_path):
    sensor = {"type": "file", "config": {"path": str(tmp_path / "ghost"), "mode": "exists"}}
    result = evaluate_sensor(sensor, SensorState())
    assert not result.fired


def test_file_sensor_disabled(tmp_path):
    target = tmp_path / "x.csv"
    target.write_text("a", encoding="utf-8")
    sensor = {"type": "file", "config": {"path": str(target)}, "enabled": False}
    result = evaluate_sensor(sensor, SensorState())
    assert not result.fired


# ─── workflow_success sensor ──────────────────────────────────────────────────


def test_workflow_success_fires_on_new_run():
    sensor = {"type": "workflow_success", "config": {"workflow_id": "upstream-1"}}
    runs = [{"run_id": "run-A", "status": "success", "finished_at": "2026-05-03T01:00:00"}]

    first = evaluate_sensor(sensor, SensorState(), workflow_run_lookup=lambda _: runs)
    assert first.fired
    assert "run-A" in first.reason

    # 同一 run 再次 tick 不应 fire
    second = evaluate_sensor(sensor, first.new_state, workflow_run_lookup=lambda _: runs)
    assert not second.fired

    # 新 run 出现 → 再 fire
    runs2 = [
        {"run_id": "run-B", "status": "success", "finished_at": "2026-05-03T02:00:00"},
        {"run_id": "run-A", "status": "success", "finished_at": "2026-05-03T01:00:00"},
    ]
    third = evaluate_sensor(sensor, second.new_state, workflow_run_lookup=lambda _: runs2)
    assert third.fired
    assert "run-B" in third.reason


def test_workflow_success_no_runs_no_fire():
    sensor = {"type": "workflow_success", "config": {"workflow_id": "ghost"}}
    result = evaluate_sensor(sensor, SensorState(), workflow_run_lookup=lambda _: [])
    assert not result.fired


def test_workflow_success_only_failed_runs_no_fire():
    sensor = {"type": "workflow_success", "config": {"workflow_id": "u"}}
    runs = [{"run_id": "r1", "status": "failed"}]
    result = evaluate_sensor(sensor, SensorState(), workflow_run_lookup=lambda _: runs)
    assert not result.fired


def test_unknown_sensor_type_no_fire():
    sensor = {"type": "alien", "config": {}}
    result = evaluate_sensor(sensor, SensorState())
    assert not result.fired


# ─── 调度器 tick 集成 ─────────────────────────────────────────────────────────


def test_scheduler_tick_fires_workflow_on_file_sensor(isolated_storage, tmp_path, monkeypatch):
    """workflow.triggers 配 file sensor，tick() 时文件出现就 submit_workflow_run。"""
    from app.services import scheduler as sched
    from app.services.repositories import workflow_store

    sched.reset_scheduler_state_for_tests()

    target = tmp_path / "landing.csv"
    # 文件初始不存在 → 第一次 tick 不 fire
    workflow_payload = {
        "name": "sensor-wf",
        "status": "active",
        "schedule_cron": "",  # 仅 sensor 触发
        "nodes": [{"id": "p", "type": "params", "config": {"parameters": [{"name": "x", "default": "1"}]}}],
        "triggers": [{
            "type": "file",
            "config": {"path": str(target), "mode": "exists"},
            "enabled": True,
        }],
    }
    workflow = workflow_store.create(_to_create(workflow_payload))

    submitted: list[dict] = []
    def fake_submit(workflow_id, variables, *, max_retries=0, trigger=""):
        submitted.append({"workflow_id": workflow_id, "trigger": trigger})
        return {"job_id": f"job-{len(submitted)}", "status": "queued"}
    monkeypatch.setattr("app.services.scheduler.submit_workflow_run", fake_submit)

    # 文件不在 → 第一次 tick 不应触发
    sched._tick_sensors()
    assert submitted == []

    # 文件出现 → 第二次 tick 应触发
    target.write_text("data", encoding="utf-8")
    sched._tick_sensors()
    assert len(submitted) == 1
    assert submitted[0]["workflow_id"] == workflow.id
    assert submitted[0]["trigger"].startswith("sensor:file")

    # 第三次 tick 不应再次触发（文件还在但 already fired）
    sched._tick_sensors()
    assert len(submitted) == 1


def test_scheduler_tick_skips_inactive_workflow(isolated_storage, tmp_path, monkeypatch):
    """status=draft 的 workflow 即使配了 sensor 也不应被触发。"""
    from app.services import scheduler as sched
    from app.services.repositories import workflow_store

    sched.reset_scheduler_state_for_tests()

    target = tmp_path / "landing.csv"
    target.write_text("data", encoding="utf-8")

    workflow_payload = {
        "name": "draft-wf",
        "status": "draft",
        "nodes": [{"id": "p", "type": "params", "config": {"parameters": [{"name": "x", "default": "1"}]}}],
        "triggers": [{"type": "file", "config": {"path": str(target), "mode": "exists"}}],
    }
    workflow_store.create(_to_create(workflow_payload))

    submitted: list[dict] = []
    monkeypatch.setattr(
        "app.services.scheduler.submit_workflow_run",
        lambda *a, **kw: submitted.append(a) or {"job_id": "x", "status": "queued"},
    )
    sched._tick_sensors()
    assert submitted == []


# ─── helpers ──────────────────────────────────────────────────────────────────


def _to_create(payload: dict):
    """payload dict → WorkflowCreate（绕过 API 层）。"""
    from app.models import WorkflowCreate
    return WorkflowCreate.model_validate(payload)
