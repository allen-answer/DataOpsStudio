"""query_concurrency P1-3 单测 —— 同用户 × 数据源 in-flight 并发限制。"""
from __future__ import annotations

import pytest

from app.services.query_concurrency import (
    QueryConcurrencyExceeded,
    acquire_slot,
    get_in_flight,
    get_in_flight_snapshot,
    release,
    reset_for_tests,
    try_acquire,
)


@pytest.fixture(autouse=True)
def _clean_state():
    reset_for_tests()
    yield
    reset_for_tests()


@pytest.fixture
def _enforce_mode(monkeypatch):
    monkeypatch.setenv("DATAOPS_QUERY_CONCURRENCY_ENFORCE", "true")
    yield


@pytest.fixture
def _warn_mode(monkeypatch):
    monkeypatch.setenv("DATAOPS_QUERY_CONCURRENCY_ENFORCE", "false")
    yield


# ─── try_acquire / release 基本流程 ──────────────────────────────────────────

def test_first_acquire_succeeds(_enforce_mode):
    ok, reason = try_acquire("u1", "ds1")
    assert ok is True
    assert reason == ""
    assert get_in_flight("u1", "ds1") == 1


def test_release_decrements_counter(_enforce_mode):
    try_acquire("u1", "ds1")
    try_acquire("u1", "ds1")
    assert get_in_flight("u1", "ds1") == 2
    release("u1", "ds1")
    assert get_in_flight("u1", "ds1") == 1
    release("u1", "ds1")
    assert get_in_flight("u1", "ds1") == 0


def test_exceed_limit_returns_false_in_enforce(_enforce_mode):
    """默认 limit=3,第 4 次 acquire 应该被拒。"""
    for _ in range(3):
        ok, _ = try_acquire("u1", "ds1")
        assert ok is True
    ok, reason = try_acquire("u1", "ds1")
    assert ok is False
    assert "上限" in reason


def test_exceed_limit_passes_in_warn(_warn_mode):
    """warn 模式只 log,acquire 仍 success(counter 累加,只警告)。"""
    for _ in range(5):
        ok, _ = try_acquire("u1", "ds1")
        assert ok is True
    # warn 模式 counter 会持续涨,因为没 enforce
    assert get_in_flight("u1", "ds1") == 5


def test_different_users_isolated(_enforce_mode):
    """同 ds 不同 user 是独立 quota,不互相影响。"""
    for _ in range(3):
        try_acquire("u1", "ds1")
    ok_u1, _ = try_acquire("u1", "ds1")
    assert ok_u1 is False  # u1 满了
    ok_u2, _ = try_acquire("u2", "ds1")
    assert ok_u2 is True   # u2 仍可以


def test_different_datasources_isolated(_enforce_mode):
    """同 user 不同 ds 也独立(避免单数据源问题阻塞用户跨库工作)。"""
    for _ in range(3):
        try_acquire("u1", "ds1")
    ok_ds2, _ = try_acquire("u1", "ds2")
    assert ok_ds2 is True


# ─── acquire_slot context manager ────────────────────────────────────────────

def test_context_manager_auto_releases(_enforce_mode):
    """正常退出 with 块 → counter 自动回 0。"""
    with acquire_slot("u1", "ds1"):
        assert get_in_flight("u1", "ds1") == 1
    assert get_in_flight("u1", "ds1") == 0


def test_context_manager_releases_on_exception(_enforce_mode):
    """worker 抛异常仍要 release(避免 counter 泄漏死锁后续查询)。"""
    with pytest.raises(RuntimeError, match="boom"):
        with acquire_slot("u1", "ds1"):
            raise RuntimeError("boom")
    assert get_in_flight("u1", "ds1") == 0


def test_context_manager_raises_when_full(_enforce_mode):
    for _ in range(3):
        try_acquire("u1", "ds1")
    with pytest.raises(QueryConcurrencyExceeded):
        with acquire_slot("u1", "ds1"):
            pass


def test_empty_keys_dont_count(_enforce_mode):
    """user_id 或 datasource_id 为空 → 直接放行,不进 counter(系统任务 / 内部路径)。"""
    ok, _ = try_acquire("", "ds1")
    assert ok is True
    ok, _ = try_acquire("u1", "")
    assert ok is True
    assert get_in_flight("u1", "ds1") == 0


def test_release_idempotent(_enforce_mode):
    """重复 release 不会让 counter 变负数。"""
    try_acquire("u1", "ds1")
    release("u1", "ds1")
    release("u1", "ds1")  # 已经 0,再调
    release("u1", "ds1")
    assert get_in_flight("u1", "ds1") == 0


# ─── snapshot ────────────────────────────────────────────────────────────────

def test_snapshot_returns_active_only(_enforce_mode):
    try_acquire("u1", "ds1")
    try_acquire("u1", "ds1")
    try_acquire("u2", "ds2")
    snap = get_in_flight_snapshot()
    assert snap == {"u1|ds1": 2, "u2|ds2": 1}


def test_snapshot_excludes_zero_counters(_enforce_mode):
    try_acquire("u1", "ds1")
    release("u1", "ds1")
    snap = get_in_flight_snapshot()
    assert "u1|ds1" not in snap


# ─── env-driven limit ────────────────────────────────────────────────────────

def test_custom_limit_via_env(_enforce_mode, monkeypatch):
    monkeypatch.setenv("DATAOPS_QUERY_CONCURRENCY_PER_USER_DS", "5")
    for _ in range(5):
        ok, _ = try_acquire("u1", "ds1")
        assert ok is True
    ok, _ = try_acquire("u1", "ds1")
    assert ok is False  # 第 6 个被拒
