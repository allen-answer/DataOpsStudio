"""Wave 2 #14:GuardConfig 生产 enforce 默认 + jobs running/queued 拆分。"""
from __future__ import annotations

import os

import pytest

from app.services.jobs import _jobs, _lock, active_job_counts
from app.services.resource_guard import GuardConfig, _default_enforce_for_env


# ─── #14: GuardConfig 生产 enforce 默认 ─────────────────────────────────────

def test_guard_enforce_prod_default_true(monkeypatch):
    monkeypatch.setenv("DATAOPS_ENV", "prod")
    monkeypatch.delenv("DATAOPS_GUARD_ENFORCE", raising=False)
    assert _default_enforce_for_env() is True


def test_guard_enforce_production_alias(monkeypatch):
    monkeypatch.setenv("DATAOPS_ENV", "production")
    monkeypatch.delenv("DATAOPS_GUARD_ENFORCE", raising=False)
    assert _default_enforce_for_env() is True


def test_guard_enforce_dev_default_false(monkeypatch):
    monkeypatch.delenv("DATAOPS_ENV", raising=False)
    monkeypatch.delenv("DATAOPS_GUARD_ENFORCE", raising=False)
    assert _default_enforce_for_env() is False


def test_guard_enforce_dev_can_opt_in(monkeypatch):
    monkeypatch.delenv("DATAOPS_ENV", raising=False)
    monkeypatch.setenv("DATAOPS_GUARD_ENFORCE", "true")
    assert _default_enforce_for_env() is True


def test_guard_config_from_env_uses_default_helper(monkeypatch):
    monkeypatch.setenv("DATAOPS_ENV", "prod")
    monkeypatch.delenv("DATAOPS_GUARD_ENFORCE", raising=False)
    cfg = GuardConfig.from_env()
    assert cfg.enforce is True


# ─── jobs.active_job_counts running / queued 分离 ─────────────────────────

@pytest.fixture
def _isolate_jobs():
    """每个测试前后清空 _jobs(独立测试,不依赖其它 setup)。"""
    with _lock:
        saved = dict(_jobs)
        _jobs.clear()
    yield
    with _lock:
        _jobs.clear()
        _jobs.update(saved)


def _inject_job(job_id: str, kind: str, status: str) -> None:
    with _lock:
        _jobs[job_id] = {"id": job_id, "kind": kind, "status": status, "task_id": "t1"}


def test_active_counts_compare_running_only_real_running(_isolate_jobs):
    _inject_job("j1", "compare", "queued")
    _inject_job("j2", "compare", "running")
    _inject_job("j3", "compare", "cancelling")
    _inject_job("j4", "compare", "success")  # terminal,不算
    counts = active_job_counts()
    assert counts["compare_running"] == 1, counts  # 只有 j2
    assert counts["compare_queued"] == 2, counts   # j1 + j3
    assert counts["active_total"] == 3, counts     # j1+j2+j3


def test_active_counts_export_running_split(_isolate_jobs):
    _inject_job("e1", "excel_export", "queued")
    _inject_job("e2", "excel_export", "running")
    counts = active_job_counts()
    assert counts["export_running"] == 1
    assert counts["export_queued"] == 1


def test_active_counts_task_kind_treated_as_compare(_isolate_jobs):
    """老代码 kind='task' 视为 compare 兼容路径。"""
    _inject_job("t1", "task", "running")
    counts = active_job_counts()
    assert counts["compare_running"] == 1


def test_active_counts_unknown_kind_only_in_total(_isolate_jobs):
    _inject_job("x1", "lineage", "running")
    counts = active_job_counts()
    assert counts["compare_running"] == 0
    assert counts["export_running"] == 0
    assert counts["active_total"] == 1


def test_active_counts_terminal_excluded(_isolate_jobs):
    for st in ("success", "failed", "cancelled"):
        _inject_job(f"j_{st}", "compare", st)
    counts = active_job_counts()
    assert counts["active_total"] == 0
    assert counts["compare_running"] == 0
    assert counts["compare_queued"] == 0
