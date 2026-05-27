"""sql_safety 单测。"""
from __future__ import annotations

import pytest

from app.utils.sql_safety import analyze_safety


# ─── warn 级 (SELECT * 无 WHERE 无 LIMIT) ────────────────────────────────────

def test_select_star_no_where_no_limit_is_warn():
    r = analyze_safety("SELECT * FROM users")
    assert r.risk_level == "warn"
    assert r.is_select_star is True
    assert r.has_where is False
    assert r.has_limit is False
    assert len(r.warnings) == 1


def test_select_star_with_distinct_no_filter_is_warn():
    r = analyze_safety("SELECT DISTINCT * FROM users")
    assert r.risk_level == "warn"
    assert r.is_select_star is True


# ─── notice 级 ──────────────────────────────────────────────────────────────

def test_select_star_with_where_is_notice():
    r = analyze_safety("SELECT * FROM users WHERE active = 1")
    assert r.risk_level == "notice"
    assert r.has_where is True
    assert r.has_limit is False


def test_no_select_star_no_where_no_limit_is_notice():
    """显式列 + 无 WHERE 无 LIMIT → 警告但级别低(列少内存压力低)"""
    r = analyze_safety("SELECT id, name FROM users")
    assert r.risk_level == "notice"
    assert r.is_select_star is False


# ─── safe 级 ────────────────────────────────────────────────────────────────

def test_with_where_and_limit_is_safe():
    r = analyze_safety("SELECT * FROM users WHERE active=1 LIMIT 100")
    assert r.risk_level == "safe"


def test_with_limit_only_is_safe():
    r = analyze_safety("SELECT * FROM users LIMIT 100")
    assert r.risk_level == "safe"


def test_with_where_only_select_columns_is_safe():
    r = analyze_safety("SELECT id FROM users WHERE active=1")
    assert r.risk_level == "safe"


def test_fetch_first_treated_as_limit():
    """DB2 / Oracle 12c FETCH FIRST 视为已带上限"""
    r = analyze_safety("SELECT * FROM users FETCH FIRST 100 ROWS ONLY")
    assert r.has_limit is True
    assert r.risk_level == "safe"


def test_rownum_treated_as_limit():
    r = analyze_safety("SELECT * FROM users WHERE ROWNUM <= 100")
    assert r.has_limit is True
    # 注意:ROWNUM 在 WHERE 子句中,所以 has_where=True,但 has_limit=True 也成立 → safe
    assert r.risk_level == "safe"


# ─── 注释 / 边界 ─────────────────────────────────────────────────────────────

def test_comments_stripped_before_analyze():
    """注释里的 WHERE / LIMIT 不算"""
    r = analyze_safety("SELECT * FROM users -- WHERE id=1\n")
    assert r.has_where is False
    assert r.risk_level == "warn"


def test_count_star_not_select_star():
    """COUNT(*) 不算 SELECT *"""
    r = analyze_safety("SELECT COUNT(*) FROM users")
    assert r.is_select_star is False
    # 没 WHERE 没 LIMIT,但只 1 行结果 → notice 即可
    assert r.risk_level == "notice"


def test_empty_sql_is_safe():
    r = analyze_safety("")
    assert r.risk_level == "safe"
    assert len(r.warnings) == 0


def test_whitespace_only_is_safe():
    r = analyze_safety("   \n\n  ")
    assert r.risk_level == "safe"


# ─── warnings 内容 ───────────────────────────────────────────────────────────

def test_warn_message_mentions_select_star():
    r = analyze_safety("SELECT * FROM big_table")
    assert any("SELECT *" in w for w in r.warnings)


def test_notice_message_for_no_filter():
    r = analyze_safety("SELECT id FROM users")
    assert any("WHERE" in w for w in r.warnings)
