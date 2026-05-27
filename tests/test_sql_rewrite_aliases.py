"""rewrite_sql_inject_aliases 单测 — 自动给 SELECT 无 alias 的复合表达式注入 AS."""
from __future__ import annotations

import re

import sqlglot
from sqlglot import exp

from app.services.sql_tools import rewrite_sql_inject_aliases


def _normalize(sql: str) -> str:
    """折叠空白便于比对 — sqlglot serialize 出的格式跟原 SQL 不完全一致."""
    return " ".join(sql.split()).lower()


def _output_columns_via_sqlglot(sql: str) -> list[str]:
    """直接用 sqlglot 解析 rewritten SQL 拿真实 alias_or_name(模拟 DB 行为)."""
    parsed = sqlglot.parse_one(sql)
    return [e.alias_or_name for e in parsed.find_all(exp.Select).__next__().expressions]


# ─── 不需要改写 ─────────────────────────────────────────────────────────────

def test_no_change_when_all_aliased():
    """所有列都已有 alias / 是普通列 — 不应改写."""
    sql = "SELECT id, name, SUM(amt) AS total FROM t GROUP BY id, name"
    rewritten, labels, changed = rewrite_sql_inject_aliases(sql)
    assert changed is False
    assert rewritten == sql  # 原样返回
    assert labels == ["id", "name", "total"]


def test_no_change_for_pure_columns():
    sql = "SELECT id, name FROM t"
    rewritten, labels, changed = rewrite_sql_inject_aliases(sql)
    assert changed is False
    assert labels == ["id", "name"]


def test_no_change_for_star():
    sql = "SELECT * FROM t"
    rewritten, labels, changed = rewrite_sql_inject_aliases(sql)
    assert changed is False
    assert labels == ["*"]


def test_no_change_for_qualified_star():
    sql = "SELECT t.* FROM users t"
    rewritten, _, changed = rewrite_sql_inject_aliases(sql)
    assert changed is False


# ─── 注入 alias ─────────────────────────────────────────────────────────────

def test_sum_without_alias_gets_injected():
    sql = "SELECT id, SUM(amt) FROM t GROUP BY id"
    rewritten, labels, changed = rewrite_sql_inject_aliases(sql)
    assert changed is True
    assert labels == ["id", "sum_amt"]
    assert "sum(amt) as sum_amt" in _normalize(rewritten)


def test_multiple_aggregates_get_distinct_aliases():
    sql = "SELECT SUM(DONE_AMT), SUM(COMMISION), SUM(CHG_OWNER_FEE) FROM t"
    rewritten, labels, changed = rewrite_sql_inject_aliases(sql)
    assert changed is True
    assert labels == ["sum_done_amt", "sum_commision", "sum_chg_owner_fee"]
    # rewritten SQL 跑过 sqlglot 后,output column 跟 labels 对得上
    derived_labels = _output_columns_via_sqlglot(rewritten)
    assert derived_labels == labels


def test_count_avg_min_max():
    sql = "SELECT COUNT(*), AVG(price), MIN(price), MAX(price) FROM t"
    rewritten, labels, changed = rewrite_sql_inject_aliases(sql)
    assert changed is True
    assert labels == ["count_all", "avg_price", "min_price", "max_price"]
    derived = _output_columns_via_sqlglot(rewritten)
    assert derived == labels


def test_case_expression_injected():
    sql = "SELECT id, CASE WHEN x > 0 THEN 1 ELSE 0 END FROM t"
    rewritten, labels, changed = rewrite_sql_inject_aliases(sql)
    assert changed is True
    assert labels[0] == "id"
    assert labels[1].startswith("case")  # case_2 / case_x
    # rewritten 里必含 AS <label>
    assert f"as {labels[1].lower()}" in _normalize(rewritten)


def test_arithmetic_expression_injected():
    sql = "SELECT a + b FROM t"
    rewritten, labels, changed = rewrite_sql_inject_aliases(sql)
    assert changed is True
    assert labels[0].startswith("expr")
    assert f"as {labels[0].lower()}" in _normalize(rewritten)


# ─── 去重保证 ───────────────────────────────────────────────────────────────

def test_no_collision_with_existing_column():
    """SELECT sum_amt, SUM(amt) — 第二个不能也叫 sum_amt."""
    sql = "SELECT sum_amt, SUM(amt) FROM t"
    rewritten, labels, changed = rewrite_sql_inject_aliases(sql)
    assert changed is True
    assert labels == ["sum_amt", "sum_amt_2"]
    assert "sum(amt) as sum_amt_2" in _normalize(rewritten)


def test_no_collision_with_existing_alias():
    """SELECT amt AS sum_amt, SUM(amt) — 已有 alias 占名,SUM 推 sum_amt_2."""
    sql = "SELECT amt AS sum_amt, SUM(amt) FROM t"
    rewritten, labels, _ = rewrite_sql_inject_aliases(sql)
    assert labels == ["sum_amt", "sum_amt_2"]


def test_no_collision_two_same_aggregates():
    sql = "SELECT SUM(AMT), SUM(amt) FROM t"  # case-insensitive same
    rewritten, labels, _ = rewrite_sql_inject_aliases(sql)
    assert labels == ["sum_amt", "sum_amt_2"]


# ─── 嵌套 / 复杂场景 ─────────────────────────────────────────────────────────

def test_mixed_aliased_and_unaliased():
    sql = """
    SELECT
      OCCUR_DATE,
      CUST_NO,
      SUM(DONE_AMT),
      SUM(COMMISION) AS commision_total,
      COUNT(*)
    FROM t
    GROUP BY OCCUR_DATE, CUST_NO
    """
    rewritten, labels, changed = rewrite_sql_inject_aliases(sql)
    assert changed is True
    assert labels == ["OCCUR_DATE", "CUST_NO", "sum_done_amt", "commision_total", "count_all"]
    # 已有 alias 不被改:commision_total 在 rewritten 仍是原 alias
    assert "as commision_total" in _normalize(rewritten)
    # 顶层 SUM(done_amt) 被注入 alias
    assert "as sum_done_amt" in _normalize(rewritten)


def test_subquery_inner_select_also_aliased():
    """子查询里的 SUM 也应被注入 alias(虽然顶层 labels 不暴露内层名)."""
    sql = "SELECT id FROM (SELECT id, SUM(amt) FROM t GROUP BY id) sub"
    rewritten, labels, changed = rewrite_sql_inject_aliases(sql)
    # 顶层 SELECT 没复合表达式,labels = ['id'],但子查询内 SUM(amt) 被注入
    assert labels == ["id"]
    assert changed is True
    assert "as sum_amt" in _normalize(rewritten)


def test_cte_inner_select_aliased():
    sql = "WITH x AS (SELECT id, SUM(amt) FROM t GROUP BY id) SELECT id FROM x"
    rewritten, labels, changed = rewrite_sql_inject_aliases(sql)
    assert changed is True
    assert labels == ["id"]
    assert "as sum_amt" in _normalize(rewritten)


def test_select_star_inside_function_kept():
    """COUNT(*) inside expression — 不应该把 * 当列单独 alias."""
    sql = "SELECT COUNT(*) FROM t"
    rewritten, labels, changed = rewrite_sql_inject_aliases(sql)
    assert labels == ["count_all"]


# ─── 错误兜底 ───────────────────────────────────────────────────────────────

def test_unparseable_sql_returns_original():
    """sqlglot parse 不了的 SQL — 返原值,不挂."""
    sql = "SELEC garbage FROM"
    rewritten, _labels, changed = rewrite_sql_inject_aliases(sql)
    assert changed is False
    assert rewritten == sql


# ─── 集成 assess_sql endpoint ────────────────────────────────────────────────

def test_assess_sql_response_includes_rewritten():
    from app.services.sql_tools import sql_assist
    r = sql_assist("SELECT id, SUM(amt) FROM t GROUP BY id", dialect=None)
    assert r["alias_injected"] is True
    assert "as sum_amt" in _normalize(r["rewritten_sql"])
    assert r["output_columns"] == ["id", "sum_amt"]


def test_assess_sql_response_when_no_rewrite_needed():
    from app.services.sql_tools import sql_assist
    r = sql_assist("SELECT id FROM t", dialect=None)
    assert r["alias_injected"] is False
    assert r["rewritten_sql"] == "SELECT id FROM t"
