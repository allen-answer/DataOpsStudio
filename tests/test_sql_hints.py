"""SQL 工作台 v0.5 静态文本规则测试 —— 4 条规则 + 边界。"""
from __future__ import annotations

from app.sqlide.sql_hints import lint_sql


def _codes(sql: str) -> list[str]:
    return sorted(h["code"] for h in lint_sql(sql))


# ─── select_star ─────────────────────────────────────────────────────


def test_select_star_triggers():
    assert "select_star" in _codes("SELECT * FROM users WHERE id=1")


def test_select_distinct_star_also_triggers():
    assert "select_star" in _codes("SELECT DISTINCT * FROM users WHERE id=1")


def test_count_star_does_not_trigger_select_star():
    # COUNT(*) 不是"选所有列",不该误报
    assert "select_star" not in _codes("SELECT COUNT(*) FROM users WHERE id=1")


def test_select_specific_columns_does_not_trigger():
    assert "select_star" not in _codes("SELECT id, name FROM users WHERE id=1")


# ─── no_where ────────────────────────────────────────────────────────


def test_no_where_triggers():
    assert "no_where" in _codes("SELECT id FROM users")


def test_with_where_does_not_trigger():
    assert "no_where" not in _codes("SELECT id FROM users WHERE id=1")


def test_no_from_no_no_where():
    # 没 FROM 的 SQL 不该触发 no_where(如 SELECT 1)
    assert "no_where" not in _codes("SELECT 1")


# ─── leading_wildcard ────────────────────────────────────────────────


def test_leading_wildcard_single_quote():
    assert "leading_wildcard" in _codes("SELECT id FROM t WHERE name LIKE '%abc'")


def test_leading_wildcard_double_quote():
    assert "leading_wildcard" in _codes('SELECT id FROM t WHERE name LIKE "%abc"')


def test_leading_wildcard_full_wildcard():
    # %abc% 也是前置 % → 索引失效
    assert "leading_wildcard" in _codes("SELECT id FROM t WHERE name LIKE '%abc%'")


def test_trailing_wildcard_does_not_trigger():
    # abc% 走前缀索引,不算前置通配符
    assert "leading_wildcard" not in _codes("SELECT id FROM t WHERE name LIKE 'abc%'")


def test_case_insensitive_like():
    # like 小写也命中
    assert "leading_wildcard" in _codes("SELECT id FROM t WHERE name like '%abc'")


# ─── order_no_limit ──────────────────────────────────────────────────


def test_order_by_without_limit_triggers():
    assert "order_no_limit" in _codes("SELECT id FROM t WHERE id>0 ORDER BY id")


def test_order_by_with_limit_does_not_trigger():
    assert "order_no_limit" not in _codes("SELECT id FROM t WHERE id>0 ORDER BY id LIMIT 10")


def test_order_by_with_fetch_first_does_not_trigger():
    # Oracle FETCH FIRST 等价于 LIMIT
    assert "order_no_limit" not in _codes(
        "SELECT id FROM t WHERE id>0 ORDER BY id FETCH FIRST 10 ROWS ONLY"
    )


def test_order_by_with_rownum_does_not_trigger():
    # Oracle 旧 ROWNUM 风格
    assert "order_no_limit" not in _codes(
        "SELECT id FROM t WHERE rownum<10 ORDER BY id"
    )


def test_order_by_with_top_does_not_trigger():
    # MSSQL TOP N
    assert "order_no_limit" not in _codes("SELECT TOP 10 id FROM t WHERE id>0 ORDER BY id")


def test_no_order_by_no_no_limit():
    # 没 ORDER BY 时,缺 LIMIT 不应触发该规则
    assert "order_no_limit" not in _codes("SELECT id FROM t WHERE id>0")


# ─── 综合 ────────────────────────────────────────────────────────────


def test_multiple_rules_triggered():
    # SELECT * + no WHERE + ORDER BY no LIMIT 都中
    codes = _codes("SELECT * FROM users ORDER BY id")
    assert "select_star" in codes
    assert "no_where" in codes
    assert "order_no_limit" in codes
    assert len(codes) == 3


def test_clean_sql_no_hints():
    codes = _codes("SELECT id, name FROM users WHERE deleted=0 ORDER BY id LIMIT 100")
    assert codes == []


def test_empty_sql_returns_no_hints():
    assert lint_sql("") == []
    assert lint_sql("   ") == []
    assert lint_sql(None) == []  # type: ignore[arg-type]


def test_block_comment_does_not_trigger():
    # 注释里的 SELECT * 不该触发
    sql = "/* SELECT * FROM evil ORDER BY x */ SELECT id FROM users WHERE id=1"
    assert _codes(sql) == []


def test_line_comment_does_not_trigger():
    sql = "-- SELECT * FROM evil ORDER BY x\nSELECT id FROM users WHERE id=1"
    assert _codes(sql) == []


def test_hint_payload_shape():
    hints = lint_sql("SELECT * FROM users")
    for h in hints:
        assert set(h.keys()) == {"code", "severity", "message"}
        assert h["severity"] in ("info", "warning", "error")
        assert h["message"]
