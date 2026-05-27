"""sql_tools._extract_output_columns 单测 —— 覆盖 SUM / CASE / 复合表达式
列名提取(用户报"SUM 字段被显示为 6,7,8")."""
from __future__ import annotations

from app.services.sql_tools import _extract_output_columns


def test_simple_columns():
    out = _extract_output_columns("SELECT id, name FROM t", None)
    assert out == ["id", "name"]


def test_qualified_columns():
    out = _extract_output_columns("SELECT t.id, t.name FROM users t", None)
    assert out == ["id", "name"]


def test_sum_without_alias_uses_full_expression():
    """没 AS alias 的聚合,列名必须是原表达式,不能是序号."""
    out = _extract_output_columns(
        "SELECT id, SUM(DONE_AMT), SUM(COMMISION), SUM(CHG_OWNER_FEE) FROM t GROUP BY id",
        None,
    )
    assert out == ["id", "SUM(DONE_AMT)", "SUM(COMMISION)", "SUM(CHG_OWNER_FEE)"]
    # 关键 invariant:没有列名是纯数字
    assert not any(c.isdigit() for c in out)


def test_sum_with_alias_uses_alias():
    out = _extract_output_columns(
        "SELECT SUM(DONE_AMT) AS total_amt, COUNT(*) AS cnt FROM t",
        None,
    )
    assert out == ["total_amt", "cnt"]


def test_case_expression_uses_full_sql():
    out = _extract_output_columns(
        "SELECT CASE WHEN x > 0 THEN 1 ELSE 0 END FROM t",
        None,
    )
    # CASE 没 alias,用原 SQL — 至少不能是数字
    assert len(out) == 1
    assert "CASE" in out[0].upper()
    assert not out[0].isdigit()


def test_cast_without_alias():
    out = _extract_output_columns("SELECT CAST(x AS INT) FROM t", None)
    assert len(out) == 1
    assert "CAST" in out[0].upper()


def test_arithmetic_without_alias():
    out = _extract_output_columns("SELECT a + b, a * 100 FROM t", None)
    # 算术表达式没 alias,fallback 原表达式
    assert len(out) == 2
    for col in out:
        assert not col.isdigit()


def test_mixed_columns():
    """实际用户场景:常规列 + 聚合 + alias 混在一起."""
    sql = """
    SELECT
        OCCUR_DATE,
        CUST_NO,
        sec_code,
        SUM(DONE_AMT),
        SUM(COMMISION) AS commision_total,
        COUNT(*) AS cnt
    FROM ks.his_done
    GROUP BY OCCUR_DATE, CUST_NO, sec_code
    """
    out = _extract_output_columns(sql, None)
    assert out == [
        "OCCUR_DATE",
        "CUST_NO",
        "sec_code",
        "SUM(DONE_AMT)",
        "commision_total",
        "cnt",
    ]


def test_star_kept_as_is():
    """SELECT * — 前端会单独 filter,这里只确认提取行为不挂."""
    out = _extract_output_columns("SELECT * FROM t", None)
    assert out == ["*"]
