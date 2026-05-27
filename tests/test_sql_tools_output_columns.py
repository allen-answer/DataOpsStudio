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


def test_sum_without_alias_gets_short_label():
    """没 AS alias 的 SUM,自动起短别名 sum_<col>(防止 UI 撑爆,也避免序号 6/7/8)."""
    out = _extract_output_columns(
        "SELECT id, SUM(DONE_AMT), SUM(COMMISION), SUM(CHG_OWNER_FEE) FROM t GROUP BY id",
        None,
    )
    assert out == ["id", "sum_done_amt", "sum_commision", "sum_chg_owner_fee"]
    assert not any(c.isdigit() for c in out)


def test_count_star_short_label():
    out = _extract_output_columns("SELECT COUNT(*) FROM t", None)
    assert out == ["count_all"]


def test_avg_min_max_short_labels():
    out = _extract_output_columns("SELECT AVG(price), MIN(price), MAX(price) FROM t", None)
    assert out == ["avg_price", "min_price", "max_price"]


def test_sum_with_alias_uses_alias():
    out = _extract_output_columns(
        "SELECT SUM(DONE_AMT) AS total_amt, COUNT(*) AS cnt FROM t",
        None,
    )
    assert out == ["total_amt", "cnt"]


def test_case_expression_uses_case_n():
    """CASE 节点会被识别为 Func(sqlglot 新版),推 case_N 短名."""
    out = _extract_output_columns(
        "SELECT id, CASE WHEN x > 0 THEN 1 ELSE 0 END FROM t",
        None,
    )
    # 第 2 列,sqlglot CASE 是 Func,func_name=case → case_<col?>_<N>
    assert out[0] == "id"
    assert out[1].startswith("case")  # case_2 / case_x / 类似
    assert out[1] != "*" and not out[1].isdigit()


def test_cast_without_alias_uses_func_name():
    out = _extract_output_columns("SELECT CAST(x AS INT) FROM t", None)
    # CAST 是 Func,inner this 是 Column "x" → cast_x
    assert out == ["cast_x"]


def test_arithmetic_without_alias_uses_expr_n():
    """算术表达式没 alias 用 expr_N — 第几列从 1 起算."""
    out = _extract_output_columns("SELECT a + b, a * 100 FROM t", None)
    assert out == ["expr_1", "expr_2"]


def test_uniqueness_no_collision_with_existing_column():
    """SELECT amt, SUM(amt) - 后者会推 sum_amt,跟前面 amt 不冲突. 但 SELECT sum_amt, SUM(amt) 会冲突,要加 _2."""
    out = _extract_output_columns("SELECT amt, SUM(amt) FROM t", None)
    assert out == ["amt", "sum_amt"]
    out2 = _extract_output_columns("SELECT sum_amt, SUM(amt) FROM t", None)
    assert out2 == ["sum_amt", "sum_amt_2"]


def test_uniqueness_two_unnamed_case():
    """两个 CASE 都没 alias,第二个不能跟第一个撞名."""
    out = _extract_output_columns(
        "SELECT CASE WHEN x THEN 1 ELSE 0 END, CASE WHEN y THEN 2 ELSE 0 END FROM t",
        None,
    )
    # 两个 case 节点都被推 case_N,要保证不撞
    assert len(out) == 2
    assert len(set(c.lower() for c in out)) == 2
    for c in out:
        assert c.startswith("case")


def test_uniqueness_case_insensitive():
    """case-insensitive 比对 — SELECT ID, id(t.id 引用 lower)从 DB 角度看是同名."""
    # 这是 sqlglot 角度看,t.ID 跟 id 是同列,但 SELECT 里写成两份正常解析(实际跑会报错)
    # 这里只验证我们的 alias 生成 case-insensitive 不会两个都叫 sum_amt
    out = _extract_output_columns("SELECT SUM(AMT), SUM(amt) FROM t", None)
    # 第二个应该是 sum_amt_2(因为已 case-insensitive 占用了 sum_AMT)
    assert out == ["sum_amt", "sum_amt_2"]


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
        "sum_done_amt",
        "commision_total",
        "cnt",
    ]


def test_star_kept_as_is():
    """SELECT * — 前端会单独 filter,这里只确认提取行为不挂."""
    out = _extract_output_columns("SELECT * FROM t", None)
    assert out == ["*"]
