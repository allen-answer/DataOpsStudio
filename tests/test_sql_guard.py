from __future__ import annotations

import pytest

from app.utils.sql_guard import validate_readonly_sql


# ---------------------------------------------------------------------------
# 允许：合法的只读 SQL
# ---------------------------------------------------------------------------

def test_simple_select():
    validate_readonly_sql("SELECT * FROM t")


def test_select_lowercase():
    validate_readonly_sql("select 1")


def test_select_mixed_case():
    validate_readonly_sql("SeLeCt id FROM Users")


def test_select_with_cte():
    validate_readonly_sql("WITH cte AS (SELECT 1) SELECT * FROM cte")


def test_with_cte_lowercase():
    validate_readonly_sql("with cte as (select 1) select * from cte")


def test_select_leading_block_comment():
    validate_readonly_sql("/* report */ SELECT id FROM orders")


def test_select_leading_line_comment():
    validate_readonly_sql("-- main query\nSELECT id FROM orders")


def test_select_leading_multiple_comments():
    validate_readonly_sql("-- a\n-- b\n/* c */ SELECT 1")


def test_select_with_subquery():
    validate_readonly_sql("SELECT * FROM (SELECT id FROM t WHERE status = 'delete') sub")


def test_select_with_subquery_containing_keyword_in_string():
    validate_readonly_sql(
        "SELECT * FROM t WHERE description LIKE 'DROP TABLE %' OR description = 'INSERT'"
    )


def test_select_with_join_and_aggregate():
    validate_readonly_sql(
        "SELECT a.id, COUNT(*) FROM a JOIN b ON a.id = b.aid GROUP BY a.id HAVING COUNT(*) > 1"
    )


def test_select_with_window_function():
    validate_readonly_sql(
        "SELECT id, ROW_NUMBER() OVER (PARTITION BY uid ORDER BY ts DESC) rn FROM events"
    )


def test_select_with_union():
    validate_readonly_sql("SELECT id FROM a UNION ALL SELECT id FROM b")


def test_select_with_leading_whitespace():
    validate_readonly_sql("   \n\t  SELECT 1")


def test_forbidden_keyword_inside_string_is_allowed():
    validate_readonly_sql("SELECT 'drop table t' AS msg FROM dual")


def test_forbidden_keyword_inside_double_quoted_string_is_allowed():
    # Oracle / DM 用 " 包字面量也会有，但更常见的是 " 包标识符。
    # sql_guard 在 phrase_sanitized 把 " 包内容剥光，所以 keyword 不会触发。
    validate_readonly_sql('SELECT "drop me" AS msg FROM dual')


def test_forbidden_keyword_inside_line_comment_is_allowed():
    validate_readonly_sql("SELECT id -- drop this later\nFROM t")


def test_forbidden_keyword_inside_block_comment_is_allowed():
    validate_readonly_sql("SELECT id /* TODO: insert into archive */ FROM t")


def test_forbidden_keyword_inside_leading_block_comment_is_allowed():
    validate_readonly_sql("/* DROP UPDATE DELETE */ SELECT 1")


def test_string_with_escaped_single_quote_is_allowed():
    # `''` 转义（SQL 标准）+ `\'` 反斜杠转义（MySQL / SQLite）两路都要走通
    validate_readonly_sql("SELECT 'it''s a drop' AS msg FROM dual")
    validate_readonly_sql("SELECT 'a\\'b drop' AS msg FROM dual")


def test_identifier_containing_forbidden_word_is_allowed():
    # 列名含禁词（drop_date / update_time / delete_flag / insert_count）不能被误杀
    # 正则 `[a-zA-Z_][a-zA-Z0-9_]*` 抓的是整个标识符，不会把 drop_date 拆成 drop
    validate_readonly_sql(
        "SELECT drop_date, update_time, delete_flag, insert_count FROM t"
    )


def test_select_with_column_alias_for_forbidden_word():
    validate_readonly_sql("SELECT id AS drop_id FROM t")


def test_trailing_semicolon_allowed():
    validate_readonly_sql("SELECT 1;")


def test_trailing_semicolon_with_whitespace_allowed():
    validate_readonly_sql("SELECT 1;   \n")


def test_trailing_multiple_semicolons_allowed():
    # rstrip(';') 把尾部分号全干掉再判，单语句多个分号不应报多语句
    validate_readonly_sql("SELECT 1;;;")


def test_select_with_in_clause_keyword_safe():
    validate_readonly_sql("SELECT * FROM t WHERE name IN ('insert', 'delete', 'drop')")


def test_select_with_for_in_subselect_no_update():
    # `for` 单独出现（如 `FOR JSON`-like 子句）不应该误杀，必须 `for update` 才拒
    validate_readonly_sql("SELECT json_for(x) FROM t")


# ---------------------------------------------------------------------------
# 拒绝：空 / 仅注释
# ---------------------------------------------------------------------------

def test_rejects_empty():
    with pytest.raises(ValueError, match="empty"):
        validate_readonly_sql("")


def test_rejects_whitespace_only():
    with pytest.raises(ValueError, match="empty"):
        validate_readonly_sql("   \n\t  ")


def test_rejects_only_line_comment():
    with pytest.raises(ValueError, match="empty"):
        validate_readonly_sql("-- just a comment")


def test_rejects_only_block_comment():
    with pytest.raises(ValueError, match="empty"):
        validate_readonly_sql("/* only comment */")


def test_rejects_only_multiple_comments():
    with pytest.raises(ValueError, match="empty"):
        validate_readonly_sql("-- a\n-- b\n/* c */")


# ---------------------------------------------------------------------------
# 拒绝：DML
# ---------------------------------------------------------------------------

def test_rejects_insert():
    with pytest.raises(ValueError):
        validate_readonly_sql("INSERT INTO t VALUES (1)")


def test_rejects_insert_lowercase():
    with pytest.raises(ValueError):
        validate_readonly_sql("insert into t values (1)")


def test_rejects_update():
    with pytest.raises(ValueError):
        validate_readonly_sql("UPDATE t SET col = 1")


def test_rejects_delete():
    with pytest.raises(ValueError):
        validate_readonly_sql("DELETE FROM t")


def test_rejects_merge():
    with pytest.raises(ValueError):
        validate_readonly_sql(
            "MERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN UPDATE SET t.v = s.v"
        )


def test_rejects_replace():
    with pytest.raises(ValueError):
        validate_readonly_sql("REPLACE INTO t VALUES (1)")


# ---------------------------------------------------------------------------
# 拒绝：DDL
# ---------------------------------------------------------------------------

def test_rejects_drop():
    with pytest.raises(ValueError):
        validate_readonly_sql("DROP TABLE t")


def test_rejects_create():
    with pytest.raises(ValueError):
        validate_readonly_sql("CREATE TABLE t (id INT)")


def test_rejects_alter():
    with pytest.raises(ValueError):
        validate_readonly_sql("ALTER TABLE t ADD col INT")


def test_rejects_truncate():
    with pytest.raises(ValueError):
        validate_readonly_sql("TRUNCATE TABLE t")


def test_rejects_grant():
    with pytest.raises(ValueError):
        validate_readonly_sql("GRANT SELECT ON t TO user1")


def test_rejects_revoke():
    with pytest.raises(ValueError):
        validate_readonly_sql("REVOKE SELECT ON t FROM user1")


# ---------------------------------------------------------------------------
# 拒绝：过程 / 锁 / 调用
# ---------------------------------------------------------------------------

def test_rejects_call():
    with pytest.raises(ValueError):
        validate_readonly_sql("CALL my_proc(1)")


def test_rejects_execute():
    with pytest.raises(ValueError):
        validate_readonly_sql("EXECUTE my_stmt")


def test_rejects_lock_table():
    with pytest.raises(ValueError):
        validate_readonly_sql("LOCK TABLES t WRITE")


# ---------------------------------------------------------------------------
# 拒绝：SELECT FOR UPDATE 各种变形
# ---------------------------------------------------------------------------

def test_rejects_select_for_update():
    with pytest.raises(ValueError, match="FOR UPDATE"):
        validate_readonly_sql("SELECT * FROM t FOR UPDATE")


def test_rejects_select_for_update_lowercase():
    with pytest.raises(ValueError, match="FOR UPDATE"):
        validate_readonly_sql("select * from t for update")


def test_rejects_select_for_update_with_extra_whitespace():
    with pytest.raises(ValueError, match="FOR UPDATE"):
        validate_readonly_sql("SELECT * FROM t FOR    UPDATE")


def test_rejects_select_for_update_with_newline():
    with pytest.raises(ValueError, match="FOR UPDATE"):
        validate_readonly_sql("SELECT * FROM t\nFOR\nUPDATE")


def test_rejects_select_for_update_with_comment_between():
    # `FOR /* x */ UPDATE` —— phrase_sanitized 用空格替换注释，\bfor\s+update\b 仍命中
    with pytest.raises(ValueError, match="FOR UPDATE"):
        validate_readonly_sql("SELECT * FROM t FOR /* lock hint */ UPDATE")


def test_rejects_select_lock_in_share_mode():
    # MySQL `LOCK IN SHARE MODE` —— `lock` 在禁词表里
    with pytest.raises(ValueError):
        validate_readonly_sql("SELECT * FROM t LOCK IN SHARE MODE")


# ---------------------------------------------------------------------------
# 拒绝：多语句
# ---------------------------------------------------------------------------

def test_rejects_multiple_statements_plain():
    with pytest.raises(ValueError, match="Multiple"):
        validate_readonly_sql("SELECT 1; SELECT 2")


def test_rejects_multiple_statements_no_space():
    with pytest.raises(ValueError, match="Multiple"):
        validate_readonly_sql("SELECT 1;SELECT 2")


def test_rejects_multiple_statements_with_newline():
    with pytest.raises(ValueError, match="Multiple"):
        validate_readonly_sql("SELECT 1;\nSELECT 2")


def test_rejects_select_with_inline_drop():
    with pytest.raises(ValueError):
        validate_readonly_sql("SELECT id FROM t WHERE 1=1; DROP TABLE t")


def test_rejects_select_with_inline_insert():
    with pytest.raises(ValueError):
        validate_readonly_sql("SELECT 1; INSERT INTO t VALUES (1)")


# ---------------------------------------------------------------------------
# 拒绝：注释 / 编码绕过
# ---------------------------------------------------------------------------

def test_rejects_comment_injection_bypass():
    # SEL/**/ECT 在去注释后归一化成 SELECT（合法），但分号后面的 DROP 仍然要被拒
    with pytest.raises(ValueError):
        validate_readonly_sql("SEL/**/ECT * FROM t; DROP TABLE t")


def test_rejects_select_with_drop_in_subquery_real_keyword():
    # 子查询里真的写了 DROP（不在字符串里也不在注释里）—— 必须拒
    with pytest.raises(ValueError):
        validate_readonly_sql("SELECT * FROM (DROP TABLE t) x")


def test_rejects_select_then_update_via_semicolon_in_comment_boundary():
    # 注释里的分号不算多语句（已被剥掉），但注释外的真分号 + UPDATE 必须拒
    with pytest.raises(ValueError):
        validate_readonly_sql("SELECT 1 /* ; SELECT 2 */; UPDATE t SET v=1")


# ---------------------------------------------------------------------------
# 拒绝：非 SELECT/WITH 起头
# ---------------------------------------------------------------------------

def test_rejects_show():
    # SHOW 不是 SELECT/WITH，first_word 校验直接拒
    with pytest.raises(ValueError, match="Only SELECT"):
        validate_readonly_sql("SHOW TABLES")


def test_rejects_describe():
    with pytest.raises(ValueError, match="Only SELECT"):
        validate_readonly_sql("DESCRIBE t")


def test_rejects_explain():
    # EXPLAIN 也不是 SELECT/WITH；要看执行计划走专门的 slow-sql endpoint
    with pytest.raises(ValueError, match="Only SELECT"):
        validate_readonly_sql("EXPLAIN SELECT * FROM t")


def test_rejects_set():
    with pytest.raises(ValueError, match="Only SELECT"):
        validate_readonly_sql("SET autocommit = 0")


def test_rejects_use_database():
    with pytest.raises(ValueError, match="Only SELECT"):
        validate_readonly_sql("USE mydb")


def test_rejects_begin():
    with pytest.raises(ValueError, match="Only SELECT"):
        validate_readonly_sql("BEGIN")


def test_rejects_commit():
    with pytest.raises(ValueError, match="Only SELECT"):
        validate_readonly_sql("COMMIT")
