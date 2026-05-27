"""sql_log_sanitizer 单测 —— fingerprint 稳定性 + dev/prod 模式区分 + 字面值脱敏。"""
from __future__ import annotations

import pytest

from app.utils.sql_log_sanitizer import (
    format_sql_for_log,
    sanitize_sql_for_log,
    sql_fingerprint,
)


# ─── sql_fingerprint 稳定性 ──────────────────────────────────────────────────

def test_fingerprint_returns_12_hex():
    h = sql_fingerprint("SELECT * FROM users")
    assert len(h) == 12
    assert all(c in "0123456789abcdef" for c in h)


def test_fingerprint_stable_across_whitespace():
    """空白差异不应改变 hash —— 同一 SQL 模板该聚合。"""
    h1 = sql_fingerprint("SELECT * FROM users")
    h2 = sql_fingerprint("SELECT  *  FROM\nusers")
    h3 = sql_fingerprint("  SELECT * FROM users  ")
    assert h1 == h2 == h3


def test_fingerprint_stable_across_case():
    h1 = sql_fingerprint("SELECT * FROM users")
    h2 = sql_fingerprint("select * from USERS")
    assert h1 == h2


def test_fingerprint_stable_across_in_values():
    """IN (1,2,3) 跟 IN (4,5,6) 应该是同一 hash —— 字面值不影响 SQL 模板。"""
    h1 = sql_fingerprint("SELECT * FROM users WHERE id IN (1, 2, 3)")
    h2 = sql_fingerprint("SELECT * FROM users WHERE id IN (4, 5, 6, 7)")
    assert h1 == h2


def test_fingerprint_stable_across_string_lits():
    h1 = sql_fingerprint("SELECT * FROM users WHERE name = 'alice'")
    h2 = sql_fingerprint("SELECT * FROM users WHERE name = 'bob'")
    assert h1 == h2


def test_fingerprint_stable_across_number_lits():
    h1 = sql_fingerprint("SELECT * FROM users WHERE age > 18")
    h2 = sql_fingerprint("SELECT * FROM users WHERE age > 21")
    assert h1 == h2


def test_fingerprint_differs_for_different_tables():
    """表名 / 列名变化必须改变 hash。"""
    h1 = sql_fingerprint("SELECT * FROM users WHERE id = 1")
    h2 = sql_fingerprint("SELECT * FROM orders WHERE id = 1")
    assert h1 != h2


def test_fingerprint_differs_for_different_columns():
    h1 = sql_fingerprint("SELECT id FROM users")
    h2 = sql_fingerprint("SELECT name FROM users")
    assert h1 != h2


# ─── sanitize_sql_for_log dev 模式 ───────────────────────────────────────────

def test_dev_mode_keeps_full_sql():
    out = sanitize_sql_for_log("SELECT * FROM users WHERE name='alice'", force_redact=False)
    # 字符串字面值保留
    assert "'alice'" in out


def test_dev_mode_collapses_whitespace():
    out = sanitize_sql_for_log("SELECT  *  FROM\n\tusers", force_redact=False)
    assert out == "SELECT * FROM users"


def test_dev_mode_truncates_at_500_chars():
    long_sql = "SELECT " + "a, " * 200 + " FROM t"
    out = sanitize_sql_for_log(long_sql, force_redact=False)
    assert len(out) <= 503  # 500 + "..."
    assert out.endswith("...")


# ─── sanitize_sql_for_log prod 模式 ──────────────────────────────────────────

def test_prod_mode_redacts_string_literal():
    out = sanitize_sql_for_log(
        "SELECT * FROM users WHERE name='alice'",
        force_redact=True,
    )
    assert "alice" not in out
    assert "?" in out


def test_prod_mode_redacts_in_values():
    out = sanitize_sql_for_log(
        "SELECT * FROM users WHERE id IN (1, 2, 3, 4, 5)",
        force_redact=True,
    )
    assert "IN (?)" in out
    # 数字字面值不该出现
    assert ", 5)" not in out


def test_prod_mode_redacts_number_literal():
    out = sanitize_sql_for_log(
        "SELECT * FROM users WHERE age > 18",
        force_redact=True,
    )
    assert "18" not in out
    assert "?" in out


def test_prod_mode_truncates_at_80_chars_default():
    long_sql = "SELECT id, name, email, phone, address FROM users WHERE active=1 ORDER BY created_at DESC"
    out = sanitize_sql_for_log(long_sql, force_redact=True)
    assert len(out) <= 83  # 80 + "..."


def test_prod_mode_custom_max_chars():
    sql = "SELECT * FROM users WHERE name='alice'"
    out = sanitize_sql_for_log(sql, max_chars=20, force_redact=True)
    assert len(out) <= 23


def test_prod_mode_with_phone_in_list():
    """关键场景:business PII in IN clause 不能落到日志。"""
    sql = "SELECT * FROM users WHERE phone IN ('13800138000', '13900139000')"
    out = sanitize_sql_for_log(sql, force_redact=True)
    assert "13800138000" not in out
    assert "13900139000" not in out


def test_prod_mode_does_not_truncate_table_name():
    """表名 / 列名应保留(那不是隐私) —— prod 模式只 redact 字面值。"""
    out = sanitize_sql_for_log(
        "SELECT id, email FROM users WHERE id = 1",
        force_redact=True,
    )
    assert "users" in out
    assert "email" in out


# ─── format_sql_for_log 结构化输出 ───────────────────────────────────────────

def test_format_returns_hash_preview_length():
    info = format_sql_for_log("SELECT * FROM users WHERE id = 1")
    assert "sql_hash" in info
    assert "sql_preview" in info
    assert "sql_length" in info
    assert info["sql_length"] == len("SELECT * FROM users WHERE id = 1")
    assert len(info["sql_hash"]) == 12


def test_format_hash_matches_fingerprint():
    sql = "SELECT 1"
    info = format_sql_for_log(sql)
    assert info["sql_hash"] == sql_fingerprint(sql)


# ─── env 模式切换 ───────────────────────────────────────────────────────────

def test_env_prod_triggers_redaction(monkeypatch):
    """DATAOPS_ENV=prod 时 sanitize_sql_for_log 默认走脱敏路径。"""
    monkeypatch.setenv("DATAOPS_ENV", "prod")
    out = sanitize_sql_for_log("SELECT * FROM users WHERE name='alice'")
    assert "alice" not in out


def test_env_dev_keeps_full(monkeypatch):
    """DATAOPS_ENV=dev (或未设)时保留完整 SQL。"""
    monkeypatch.delenv("DATAOPS_ENV", raising=False)
    out = sanitize_sql_for_log("SELECT * FROM users WHERE name='alice'")
    assert "'alice'" in out


# ─── 边界 ────────────────────────────────────────────────────────────────────

def test_empty_sql():
    assert sanitize_sql_for_log("") == ""
    assert sql_fingerprint("")
    info = format_sql_for_log("")
    assert info["sql_length"] == 0


def test_only_whitespace():
    out = sanitize_sql_for_log("   \n\t  ", force_redact=False)
    assert out == ""
