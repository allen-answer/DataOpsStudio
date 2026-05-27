"""limit_injector 单测 —— 覆盖 dialect / 已带 LIMIT 不动 / 注释隔离 / 分号尾。"""
from __future__ import annotations

import pytest

from app.sqlide.limit_injector import has_explicit_limit, inject_limit, needs_injection


# ─── has_explicit_limit ─────────────────────────────────────────────────────

@pytest.mark.parametrize("sql,expected", [
    ("SELECT * FROM users", False),
    ("SELECT * FROM users LIMIT 100", True),
    ("SELECT * FROM users limit 100", True),  # 大小写不敏感
    ("SELECT * FROM users LIMIT 100 OFFSET 50", True),
    ("SELECT * FROM users LIMIT 50, 100", True),  # MySQL 双数
    ("SELECT * FROM users FETCH FIRST 10 ROWS ONLY", True),  # DB2 / Oracle 12c
    ("SELECT * FROM users WHERE ROWNUM <= 100", True),
    ("SELECT * FROM users WHERE rownum <= 100", True),  # 大小写
    ("SELECT id FROM users -- LIMIT 100\nWHERE active=1", False),  # 注释里不算
    ("SELECT id FROM /* LIMIT 100 */ users", False),
])
def test_has_explicit_limit(sql, expected):
    assert has_explicit_limit(sql) is expected


# ─── inject_limit dialect ────────────────────────────────────────────────────

def test_inject_mysql_simple():
    out = inject_limit("SELECT * FROM users", max_rows=1000, db_type="mysql")
    assert "LIMIT 1000" in out
    assert out.startswith("SELECT * FROM users")


def test_inject_oracle_wraps_with_rownum():
    out = inject_limit("SELECT id FROM ods.orders", max_rows=500, db_type="oracle")
    assert "ROWNUM <= 500" in out
    assert "SELECT * FROM (" in out
    assert "SELECT id FROM ods.orders" in out


def test_inject_dm_uses_oracle_path():
    """DM 跟 Oracle 兼容,走 ROWNUM wrap"""
    out = inject_limit("SELECT * FROM t", max_rows=100, db_type="dm")
    assert "ROWNUM <= 100" in out


def test_inject_db2_fetch_first():
    out = inject_limit("SELECT * FROM t", max_rows=200, db_type="db2")
    assert "FETCH FIRST 200 ROWS ONLY" in out


def test_inject_unknown_dialect_defaults_to_mysql():
    """未知 db_type 默认走 mysql LIMIT(最通用)"""
    out = inject_limit("SELECT * FROM t", max_rows=100, db_type="unknown_db")
    assert "LIMIT 100" in out


# ─── 已带 LIMIT 不动 ──────────────────────────────────────────────────────────

def test_already_has_limit_untouched():
    sql = "SELECT * FROM users LIMIT 50"
    out = inject_limit(sql, max_rows=1000, db_type="mysql")
    assert out == sql


def test_already_has_fetch_first_untouched():
    sql = "SELECT * FROM users FETCH FIRST 50 ROWS ONLY"
    out = inject_limit(sql, max_rows=1000, db_type="db2")
    assert out == sql


def test_already_has_rownum_untouched():
    sql = "SELECT * FROM users WHERE ROWNUM <= 50"
    out = inject_limit(sql, max_rows=1000, db_type="oracle")
    assert out == sql


# ─── 分号尾 ───────────────────────────────────────────────────────────────────

def test_trailing_semicolon_preserved():
    out = inject_limit("SELECT * FROM users;", max_rows=100, db_type="mysql")
    assert out.endswith(";")
    assert "LIMIT 100" in out
    # LIMIT 必须在分号之前
    assert out.find("LIMIT 100") < out.find(";")


def test_multiple_trailing_semicolons():
    out = inject_limit("SELECT * FROM users;;;", max_rows=100, db_type="mysql")
    assert out.endswith(";;;")
    assert "LIMIT 100" in out


# ─── 复杂 SQL 形态 ────────────────────────────────────────────────────────────

def test_with_cte_injected():
    """WITH ... SELECT 仍可注入 LIMIT(末尾追加)"""
    sql = "WITH cte AS (SELECT id FROM t) SELECT * FROM cte"
    out = inject_limit(sql, max_rows=100, db_type="mysql")
    assert "LIMIT 100" in out


def test_order_by_keeps_position():
    """ORDER BY 之后追加 LIMIT(MySQL)"""
    sql = "SELECT * FROM users ORDER BY id DESC"
    out = inject_limit(sql, max_rows=100, db_type="mysql")
    assert "ORDER BY id DESC" in out
    assert out.find("ORDER BY") < out.find("LIMIT 100")


def test_comment_with_limit_keyword_not_misread():
    """注释里有 'LIMIT' 字样不算已带 LIMIT"""
    sql = "SELECT * FROM t -- TODO: add LIMIT later\nWHERE x=1"
    assert needs_injection(sql, max_rows=100) is True
    out = inject_limit(sql, max_rows=100, db_type="mysql")
    assert out.count("LIMIT 100") == 1  # 真的注入了


# ─── needs_injection ─────────────────────────────────────────────────────────

def test_needs_injection_true_when_no_limit():
    assert needs_injection("SELECT * FROM t", max_rows=100) is True


def test_needs_injection_false_when_has_limit():
    assert needs_injection("SELECT * FROM t LIMIT 1", max_rows=100) is False


def test_needs_injection_false_when_max_rows_zero():
    """max_rows=0 视为关闭注入(防呆)"""
    assert needs_injection("SELECT * FROM t", max_rows=0) is False
    out = inject_limit("SELECT * FROM t", max_rows=0, db_type="mysql")
    assert out == "SELECT * FROM t"
