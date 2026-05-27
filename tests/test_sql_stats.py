"""sql_stats 单测 (P2-2)。"""
from __future__ import annotations

import pytest

from app.services import sql_stats


@pytest.fixture(autouse=True)
def _clean():
    sql_stats.reset_for_tests()
    yield
    sql_stats.reset_for_tests()


# ─── record + aggregation ────────────────────────────────────────────────────

def test_record_counts_first_execution():
    h = sql_stats.record(sql="SELECT 1", elapsed_ms=100.0, success=True, user="alice")
    top = sql_stats.top_slow(limit=10)
    assert len(top) == 1
    assert top[0]["sql_hash"] == h
    assert top[0]["count"] == 1
    assert top[0]["success_count"] == 1
    assert top[0]["failed_count"] == 0


def test_aggregates_by_sql_template():
    """同 SQL 模板不同字面值聚合到一条统计(感谢 sql_fingerprint)。"""
    sql_stats.record(sql="SELECT * FROM t WHERE id=1", elapsed_ms=50, success=True)
    sql_stats.record(sql="SELECT * FROM t WHERE id=2", elapsed_ms=80, success=True)
    sql_stats.record(sql="SELECT * FROM t WHERE id=3", elapsed_ms=120, success=True)
    top = sql_stats.top_slow(limit=10)
    assert len(top) == 1
    assert top[0]["count"] == 3
    # avg = (50+80+120) / 3 = 83.33
    assert abs(top[0]["avg_ms"] - 83.33) < 0.05


def test_distinguishes_different_templates():
    sql_stats.record(sql="SELECT * FROM users", elapsed_ms=10, success=True)
    sql_stats.record(sql="SELECT * FROM orders", elapsed_ms=20, success=True)
    top = sql_stats.top_slow(limit=10)
    assert len(top) == 2


def test_max_min_tracked():
    sql_stats.record(sql="SELECT 1", elapsed_ms=10, success=True)
    sql_stats.record(sql="SELECT 1", elapsed_ms=500, success=True)
    sql_stats.record(sql="SELECT 1", elapsed_ms=100, success=True)
    top = sql_stats.top_slow(limit=1)
    assert top[0]["max_ms"] == 500
    assert top[0]["min_ms"] == 10


def test_failure_count():
    sql_stats.record(sql="SELECT 1", elapsed_ms=10, success=True)
    sql_stats.record(sql="SELECT 1", elapsed_ms=20, success=False)
    sql_stats.record(sql="SELECT 1", elapsed_ms=30, success=False)
    top = sql_stats.top_slow(limit=1)
    assert top[0]["count"] == 3
    assert top[0]["success_count"] == 1
    assert top[0]["failed_count"] == 2


# ─── ranking ──────────────────────────────────────────────────────────────

def test_ranks_by_avg_ms_default():
    sql_stats.record(sql="SELECT 1", elapsed_ms=10, success=True)
    sql_stats.record(sql="SELECT 2", elapsed_ms=100, success=True)
    sql_stats.record(sql="SELECT 3", elapsed_ms=50, success=True)
    top = sql_stats.top_slow(limit=10)
    assert top[0]["avg_ms"] == 100
    assert top[1]["avg_ms"] == 50
    assert top[2]["avg_ms"] == 10


def test_ranks_by_max_ms():
    sql_stats.record(sql="SELECT 1", elapsed_ms=10, success=True)
    sql_stats.record(sql="SELECT 1", elapsed_ms=10, success=True)
    # 单次 200 但 avg 73,跟下面单次 100 但 avg 100 比 max 谁高
    sql_stats.record(sql="SELECT 1", elapsed_ms=200, success=True)  # avg 73, max 200
    sql_stats.record(sql="SELECT 2", elapsed_ms=100, success=True)  # avg 100, max 100
    top = sql_stats.top_slow(limit=10, metric="max_ms")
    assert top[0]["max_ms"] == 200


def test_ranks_by_count():
    sql_stats.record(sql="SELECT 1", elapsed_ms=10, success=True)
    sql_stats.record(sql="SELECT 1", elapsed_ms=10, success=True)
    sql_stats.record(sql="SELECT 2", elapsed_ms=1000, success=True)  # 慢但只跑 1 次
    top = sql_stats.top_slow(limit=10, metric="count")
    assert top[0]["count"] == 2


def test_min_count_filter():
    sql_stats.record(sql="SELECT 1", elapsed_ms=1000, success=True)  # 跑 1 次
    sql_stats.record(sql="SELECT 2", elapsed_ms=100, success=True)
    sql_stats.record(sql="SELECT 2", elapsed_ms=100, success=True)
    top = sql_stats.top_slow(limit=10, min_count=2)
    # 只有 SELECT 2 跑过 ≥ 2 次,SELECT 1 被过滤掉
    assert len(top) == 1
    assert top[0]["count"] == 2


# ─── summary ─────────────────────────────────────────────────────────────────

def test_summary_returns_global_metrics():
    sql_stats.record(sql="SELECT 1", elapsed_ms=100, success=True)
    sql_stats.record(sql="SELECT 2", elapsed_ms=200, success=False)
    s = sql_stats.get_summary()
    assert s["distinct_sql_templates"] == 2
    assert s["total_executions"] == 2
    assert s["total_failures"] == 1
    assert s["failure_rate"] == 0.5
    assert s["total_ms"] == 300


def test_summary_empty():
    s = sql_stats.get_summary()
    assert s["distinct_sql_templates"] == 0
    assert s["total_executions"] == 0
    assert s["failure_rate"] == 0


# ─── LRU eviction ────────────────────────────────────────────────────────────

def test_lru_eviction_caps_dict():
    """超过 _MAX_DISTINCT_HASHES 时砍掉一半。"""
    import app.services.sql_stats as mod
    original_cap = mod._MAX_DISTINCT_HASHES
    mod._MAX_DISTINCT_HASHES = 10
    try:
        for i in range(15):
            sql_stats.record(sql=f"SELECT {i} FROM tab{i}", elapsed_ms=10, success=True)
        # 触发后字典应被砍到 ≤ 10
        assert len(mod._registry.stats) <= 10
    finally:
        mod._MAX_DISTINCT_HASHES = original_cap


# ─── preview 脱敏 ────────────────────────────────────────────────────────────

def test_preview_redacts_in_prod_mode(monkeypatch):
    """prod 模式 last_sql_preview 字面值应脱敏。"""
    monkeypatch.setenv("DATAOPS_ENV", "prod")
    sql_stats.record(sql="SELECT * FROM users WHERE phone='13800138000'", elapsed_ms=10, success=True)
    top = sql_stats.top_slow(limit=1)
    assert "13800138000" not in top[0]["last_sql_preview"]
