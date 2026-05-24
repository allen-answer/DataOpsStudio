"""Slow SQL enhance 测试 —— sqlglot 解析 SQL 提取 WHERE / JOIN 列 + 生成
具体 CREATE INDEX DDL。覆盖核心 case 让规则建议有实质数据。
"""
from __future__ import annotations

import pytest

from app.services.slow_sql_enhance import (
    _build_index_ddl,
    enhance_for_issues,
    extract_table_usage,
)


# ─── extract_table_usage ────────────────────────────────────────────────────

def test_extract_basic_where_and_join():
    sql = """
    SELECT a.id, b.val
    FROM dw.fact a
    JOIN ods.dim b ON a.dim_id = b.id
    WHERE a.dt = '2026-01-01'
    """
    u = extract_table_usage(sql)
    assert "dw.fact" in u
    assert "ods.dim" in u
    assert u["dw.fact"].where_columns == {"dt"}
    assert u["dw.fact"].join_columns == {"dim_id"}
    assert u["ods.dim"].join_columns == {"id"}
    # 都没函数包,unwrapped_columns 含所有
    assert u["dw.fact"].unwrapped_columns == {"dt", "dim_id"}
    assert u["ods.dim"].unwrapped_columns == {"id"}


def test_extract_case_wraps_column():
    """JOIN 条件含 CASE 包列 → 该列归 wrapped_columns,不进 unwrapped"""
    sql = """
    SELECT 1 FROM t a
    LEFT JOIN s b ON (CASE WHEN a.x IN ('a','b') THEN 'c' ELSE a.x END) = b.x
    """
    u = extract_table_usage(sql)
    assert u["t"].wrapped_columns == {"x"}
    assert u["t"].unwrapped_columns == set()  # x 只出现在 CASE 里


def test_extract_partial_wrap_keeps_unwrapped():
    """同列既裸用又被函数包 → 归 unwrapped(普通索引仍有效,看裸用那处)"""
    sql = """
    SELECT 1 FROM t a
    WHERE a.x = 1
       OR TRIM(a.x) = '2'
    """
    u = extract_table_usage(sql)
    assert "x" in u["t"].unwrapped_columns  # WHERE a.x = 1 这处裸用
    assert "x" in u["t"].wrapped_columns    # TRIM(a.x) 这处包列


def test_extract_trim_wraps_column():
    sql = """
    SELECT 1 FROM s b WHERE TRIM(b.code) = 'X'
    """
    u = extract_table_usage(sql)
    assert u["s"].wrapped_columns == {"code"}
    assert "code" not in u["s"].unwrapped_columns


def test_extract_derived_table_columns_not_attributed_to_alias():
    """派生表的外层 alias 不在 alias_to_full 里 → 外层 JOIN 用 b.col 的列被跳过"""
    sql = """
    SELECT 1 FROM dw.fact a
    LEFT JOIN (SELECT id FROM ods.src) b ON a.k = b.id
    """
    u = extract_table_usage(sql)
    # b 是派生表别名,b.id 应被跳过(归属不明)
    assert "id" not in u.get("ods.src", type("X", (), {"join_columns": set()})()).join_columns
    # 但 a.k 仍能正确归到 dw.fact
    assert u["dw.fact"].join_columns == {"k"}


def test_extract_unparseable_returns_empty():
    """sqlglot 解析失败 → 返 {},不抛"""
    sql = "GIBBERISH NOT SQL at all"
    u = extract_table_usage(sql)
    assert u == {}


def test_extract_unqualified_column_skipped():
    """无别名前缀的列(`WHERE id = 1` 而非 `WHERE a.id = 1`)归属不明,跳过"""
    sql = "SELECT 1 FROM t WHERE id = 1"
    u = extract_table_usage(sql)
    # t 表 usage 可能存在(因 Table 节点收了),但 where_columns 不应包含未限定的 id
    # 实际是无限定时 col.table=='' → alias_to_full 找不到 → 整列跳过
    assert "t" not in u or u["t"].where_columns == set()


# ─── _build_index_ddl ────────────────────────────────────────────────────────

def test_build_index_ddl_with_schema():
    ddl = _build_index_ddl("dw", "fact", ["dt"])
    assert ddl == "CREATE INDEX `idx_fact_dt` ON `dw`.`fact` (`dt`);"


def test_build_index_ddl_without_schema():
    ddl = _build_index_ddl("", "fact", ["dt", "id"])
    assert ddl == "CREATE INDEX `idx_fact_dt_id` ON `fact` (`dt`, `id`);"


def test_build_index_ddl_rejects_unsafe_identifier():
    """标识符含非白名单字符 → None(防 DDL 注入)"""
    assert _build_index_ddl("dw", "fact;DROP", ["dt"]) is None
    assert _build_index_ddl("dw", "fact", ["dt`malicious"]) is None
    assert _build_index_ddl("dw-bad", "fact", ["dt"]) is None


# ─── enhance_for_issues 整合 ─────────────────────────────────────────────────

def test_enhance_skips_non_mysql():
    """Oracle / DM enhance 暂未实现 → 空 list 不抛"""
    assert enhance_for_issues(
        datasource_id="x", sql="SELECT 1",
        issues=[{"code": "full_table_scan", "table": "t"}],
        dialect="oracle",
    ) == []


def test_enhance_no_issues_returns_empty():
    assert enhance_for_issues(
        datasource_id="x", sql="SELECT 1", issues=[], dialect="mysql",
    ) == []


def test_enhance_filters_derived_pseudo_tables():
    """EXPLAIN 里 <derived2> / <auto_key0> 是伪表 → 不应被 enhance(防 introspect 报错)"""
    issues = [{"code": "full_table_scan", "table": "<derived2>"}]
    # 即使没真 datasource,也应直接返 [] 不报错
    result = enhance_for_issues(
        datasource_id="nonexistent", sql="SELECT 1 FROM t",
        issues=issues, dialect="mysql",
    )
    assert result == []


def test_enhance_with_real_introspect(monkeypatch):
    """主流程:enhance 产 schema_context dict 含 row_count + indexes + DDL"""
    # mock introspect 调用
    def fake_introspect_indexes(ds_id, table_name):
        if table_name == "dw.fact":
            return [{"name": "PRIMARY", "columns": ["id"], "unique": True, "is_pk": True}]
        return []

    def fake_introspect_row_count(ds_id, table_name):
        if table_name == "dw.fact":
            return 1500000
        return None

    monkeypatch.setattr(
        "app.services.datasource_introspect.introspect_indexes",
        fake_introspect_indexes,
    )
    monkeypatch.setattr(
        "app.services.datasource_introspect.introspect_row_count",
        fake_introspect_row_count,
    )

    sql = "SELECT * FROM dw.fact a WHERE a.dt = '2026-01-01' AND a.id = 1"
    issues = [{"code": "full_table_scan", "table": "dw.fact"}]
    result = enhance_for_issues(
        datasource_id="demo", sql=sql, issues=issues, dialect="mysql",
    )
    assert len(result) == 1
    r = result[0]
    assert r["table"] == "fact"
    assert r["schema"] == "dw"
    assert r["table_row_count"] == 1500000
    assert len(r["existing_indexes"]) == 1
    # dt 未被 PRIMARY 覆盖 → 应产 DDL;id 是 PRIMARY 前导列 → 不应产 DDL
    assert any("idx_fact_dt" in ddl for ddl in r["ddl_candidates"])
    assert not any("idx_fact_id" in ddl for ddl in r["ddl_candidates"])
    assert "1,500,000 行" in r["rationale"]


def test_enhance_skips_wrapped_only_columns(monkeypatch):
    """全部出现都被函数包的列不生成 DDL,rationale 标 hint"""
    monkeypatch.setattr(
        "app.services.datasource_introspect.introspect_indexes",
        lambda *a: [],
    )
    monkeypatch.setattr(
        "app.services.datasource_introspect.introspect_row_count",
        lambda *a: 100,
    )
    sql = """
    SELECT 1 FROM dw.fact a
    LEFT JOIN ods.dim b ON (CASE WHEN a.code IN ('x','y') THEN 'z' ELSE a.code END) = b.code
    """
    issues = [{"code": "full_table_scan", "table": "dw.fact"}]
    result = enhance_for_issues(
        datasource_id="demo", sql=sql, issues=issues, dialect="mysql",
    )
    assert len(result) == 1
    r = result[0]
    # code 全部被 CASE 包,不应进 ddl_candidates
    assert all("code" not in ddl for ddl in r["ddl_candidates"])
    # rationale 应含函数包列提示
    assert "函数" in r["rationale"]
    assert "code" in r["rationale"]
