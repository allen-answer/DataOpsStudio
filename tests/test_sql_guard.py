from __future__ import annotations

import pytest

from app.utils.sql_guard import validate_readonly_sql


# --- valid queries ---

def test_simple_select():
    validate_readonly_sql("SELECT * FROM t")


def test_select_with_cte():
    validate_readonly_sql("WITH cte AS (SELECT 1) SELECT * FROM cte")


def test_select_leading_block_comment():
    validate_readonly_sql("/* report */ SELECT id FROM orders")


def test_select_leading_line_comment():
    validate_readonly_sql("-- main query\nSELECT id FROM orders")


def test_select_with_subquery():
    validate_readonly_sql("SELECT * FROM (SELECT id FROM t WHERE status = 'delete') sub")


def test_forbidden_keyword_inside_string_is_allowed():
    validate_readonly_sql("SELECT 'drop table t' AS msg FROM dual")


def test_forbidden_keyword_inside_comment_is_allowed():
    validate_readonly_sql("SELECT id -- drop this later\nFROM t")


def test_trailing_semicolon_allowed():
    validate_readonly_sql("SELECT 1;")


# --- rejected queries ---

def test_rejects_empty():
    with pytest.raises(ValueError, match="empty"):
        validate_readonly_sql("   ")


def test_rejects_only_comments():
    with pytest.raises(ValueError, match="empty"):
        validate_readonly_sql("-- just a comment")


def test_rejects_insert():
    with pytest.raises(ValueError):
        validate_readonly_sql("INSERT INTO t VALUES (1)")


def test_rejects_update():
    with pytest.raises(ValueError):
        validate_readonly_sql("UPDATE t SET col = 1")


def test_rejects_delete():
    with pytest.raises(ValueError):
        validate_readonly_sql("DELETE FROM t")


def test_rejects_drop():
    with pytest.raises(ValueError):
        validate_readonly_sql("DROP TABLE t")


def test_rejects_create():
    with pytest.raises(ValueError):
        validate_readonly_sql("CREATE TABLE t (id INT)")


def test_rejects_truncate():
    with pytest.raises(ValueError):
        validate_readonly_sql("TRUNCATE TABLE t")


def test_rejects_merge():
    with pytest.raises(ValueError):
        validate_readonly_sql("MERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN UPDATE SET t.v = s.v")


def test_rejects_select_for_update():
    with pytest.raises(ValueError, match="FOR UPDATE"):
        validate_readonly_sql("SELECT * FROM t FOR UPDATE")


def test_rejects_multiple_statements():
    with pytest.raises(ValueError, match="Multiple"):
        validate_readonly_sql("SELECT 1; SELECT 2")


def test_rejects_comment_injection_bypass():
    # SEL/**/ECT should normalize to SELECT (allowed), but DROP is still forbidden
    with pytest.raises(ValueError):
        validate_readonly_sql("SEL/**/ECT * FROM t; DROP TABLE t")


def test_rejects_select_with_inline_drop():
    with pytest.raises(ValueError):
        validate_readonly_sql("SELECT id FROM t WHERE 1=1; DROP TABLE t")
