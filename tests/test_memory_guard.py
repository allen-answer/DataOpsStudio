"""Wave 4 #15:MemoryGuard 单测 —— mode 切换 + 阈值检测。"""
from __future__ import annotations

import pytest


def test_mode_default_observe_in_dev(monkeypatch):
    monkeypatch.delenv("DATAOPS_ENV", raising=False)
    monkeypatch.delenv("DATAOPS_MEMORY_GUARD_MODE", raising=False)
    from app.services.memory_guard import get_mode
    assert get_mode() == "observe"


def test_mode_default_enforce_in_prod(monkeypatch):
    monkeypatch.setenv("DATAOPS_ENV", "prod")
    monkeypatch.delenv("DATAOPS_MEMORY_GUARD_MODE", raising=False)
    from app.services.memory_guard import get_mode
    assert get_mode() == "enforce"


def test_mode_explicit_overrides(monkeypatch):
    monkeypatch.setenv("DATAOPS_ENV", "prod")
    monkeypatch.setenv("DATAOPS_MEMORY_GUARD_MODE", "observe")
    from app.services.memory_guard import get_mode
    assert get_mode() == "observe"


def test_compute_run_budget_with_env_limit(monkeypatch):
    monkeypatch.setenv("DATAOPS_MEMORY_LIMIT_MB", "2048")  # 2 GB
    monkeypatch.delenv("DATAOPS_MEMORY_HEADROOM_MB", raising=False)
    from app.services.memory_guard import compute_run_budget_bytes
    budget, limit, source = compute_run_budget_bytes()
    assert source == "env"
    assert limit == 2048 * 1024 * 1024
    # 35% of 2 GB ≈ 716 MB,50% of (2GB - 15% headroom = 1.7 GB) ≈ 870 MB,取 min
    expected_max = int(0.35 * limit)
    assert 0 < budget <= expected_max


def test_compute_run_budget_no_source_returns_zero(monkeypatch):
    """无 env、无 cgroup、无 host meminfo(非 Linux) → 返 0 +'none'。"""
    monkeypatch.delenv("DATAOPS_MEMORY_LIMIT_MB", raising=False)
    monkeypatch.setattr("app.services.memory_guard._read_cgroup_limit_bytes", lambda: (None, ""))
    monkeypatch.setattr("app.services.memory_guard._read_host_available_bytes", lambda: 0)
    from app.services.memory_guard import compute_run_budget_bytes
    budget, limit, source = compute_run_budget_bytes()
    assert budget == 0
    assert limit is None
    assert source == "none"


def test_guard_off_mode_never_raises(monkeypatch):
    monkeypatch.setattr("app.services.memory_guard._read_rss_bytes", lambda: 999 * 1024 * 1024 * 1024)
    from app.services.memory_guard import MemoryGuard
    g = MemoryGuard(run_budget_bytes=1024 * 1024, mode="off")
    snap = g.check(stage="t")
    assert snap.rss_bytes > 0
    # 不抛即可


def test_guard_observe_mode_warns_but_no_raise(monkeypatch, caplog):
    """observe 模式即使 RSS > hard 也只 log 不抛。"""
    monkeypatch.setattr("app.services.memory_guard._read_rss_bytes", lambda: 900 * 1024 * 1024)
    from app.services.memory_guard import MemoryGuard
    g = MemoryGuard(run_budget_bytes=1000 * 1024 * 1024, mode="observe")  # hard=900MB
    snap = g.check(stage="test_stage")
    assert snap.rss_bytes == 900 * 1024 * 1024


def test_guard_enforce_mode_raises_on_hard(monkeypatch):
    monkeypatch.setattr("app.services.memory_guard._read_rss_bytes", lambda: 950 * 1024 * 1024)
    from app.services.memory_guard import MemoryGuard, MemoryBudgetExceeded
    g = MemoryGuard(run_budget_bytes=1000 * 1024 * 1024, mode="enforce")  # hard=900MB
    with pytest.raises(MemoryBudgetExceeded, match="memory hard limit exceeded"):
        g.check(stage="reader.iter_rows")


def test_guard_below_soft_no_warn(monkeypatch):
    monkeypatch.setattr("app.services.memory_guard._read_rss_bytes", lambda: 100 * 1024 * 1024)
    from app.services.memory_guard import MemoryGuard
    g = MemoryGuard(run_budget_bytes=1000 * 1024 * 1024, mode="enforce")
    snap = g.check(stage="t")
    assert snap.rss_bytes == 100 * 1024 * 1024
    # 不抛


def test_guard_peak_rss_tracks_max(monkeypatch):
    samples = iter([100 * 1024 * 1024, 500 * 1024 * 1024, 200 * 1024 * 1024])
    monkeypatch.setattr("app.services.memory_guard._read_rss_bytes", lambda: next(samples))
    from app.services.memory_guard import MemoryGuard
    g = MemoryGuard(run_budget_bytes=1000 * 1024 * 1024, mode="off")
    g.check()
    g.check()
    g.check()
    assert g.peak_rss_mb() == pytest.approx(500.0, rel=0.01)


def test_guard_zero_budget_never_raises(monkeypatch):
    """无 limit 信源时(budget=0)guard 退化为 no-op。"""
    monkeypatch.setattr("app.services.memory_guard._read_rss_bytes", lambda: 10 * 1024 * 1024 * 1024)
    from app.services.memory_guard import MemoryGuard
    g = MemoryGuard(run_budget_bytes=0, mode="enforce")
    snap = g.check()
    assert snap.rss_bytes > 0
