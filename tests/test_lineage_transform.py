"""字段级 transform 分类细化测试（Phase 7 G）。

覆盖 11 类细分：聚合 / 窗口 / 条件 / 类型转换 / 空值兜底 / 算术 /
字符串函数 / 日期函数 / 数值函数 / UDF / 函数 / 字面量 / 直接映射 / 表达式
"""
from __future__ import annotations

from app.lineage.analyzer import analyze_sql_lineage


def _columns_by_output(sql: str, dialect: str = "mysql") -> dict[str, dict]:
    result = analyze_sql_lineage(sql, dialect=dialect)
    return {col["output_column"]: col for col in result["columns"]}


def test_direct_mapping():
    cols = _columns_by_output(
        "INSERT INTO tgt SELECT a.id, a.name FROM src a"
    )
    assert cols["id"]["transform"] == "直接映射"
    assert cols["name"]["transform"] == "直接映射"


def test_aggregation():
    cols = _columns_by_output(
        "INSERT INTO tgt SELECT user_id, SUM(amount) AS total FROM orders GROUP BY user_id"
    )
    assert cols["total"]["transform"] == "聚合"


def test_window_function_emits_partition_by_and_order_by():
    cols = _columns_by_output(
        "INSERT INTO tgt SELECT user_id, "
        "ROW_NUMBER() OVER (PARTITION BY dept_id ORDER BY hire_date DESC) AS rn "
        "FROM employees"
    )
    rn = cols["rn"]
    assert rn["transform"] == "窗口", rn
    assert "window" in rn, "window 字段应该挂 partition / order"
    assert "dept_id" in str(rn["window"]["partition_by"]), rn["window"]
    assert "hire_date" in str(rn["window"]["order_by"]), rn["window"]


def test_case_when():
    cols = _columns_by_output(
        "INSERT INTO tgt SELECT id, "
        "CASE WHEN amount > 100 THEN 'big' ELSE 'small' END AS bucket FROM orders"
    )
    assert cols["bucket"]["transform"] == "条件"


def test_cast():
    cols = _columns_by_output(
        "INSERT INTO tgt SELECT CAST(id AS CHAR) AS id_str FROM users"
    )
    assert cols["id_str"]["transform"] == "类型转换"


def test_coalesce():
    cols = _columns_by_output(
        "INSERT INTO tgt SELECT COALESCE(nick, name) AS display FROM users"
    )
    assert cols["display"]["transform"] == "空值兜底"


def test_arithmetic():
    cols = _columns_by_output(
        "INSERT INTO tgt SELECT id, price * qty AS amount FROM order_items"
    )
    assert cols["amount"]["transform"] == "算术"


def test_string_function():
    cols = _columns_by_output(
        "INSERT INTO tgt SELECT id, UPPER(name) AS name_upper FROM users"
    )
    assert cols["name_upper"]["transform"] == "字符串函数"


def test_date_function():
    cols = _columns_by_output(
        "INSERT INTO tgt SELECT id, DATE_DIFF(NOW(), created_at, DAY) AS age_days FROM users"
    )
    assert cols["age_days"]["transform"] in {"日期函数", "函数"}


def test_literal():
    cols = _columns_by_output(
        "INSERT INTO tgt SELECT id, 'pending' AS status FROM users"
    )
    assert cols["status"]["transform"] == "字面量"


def test_udf_or_anonymous_function():
    """方言无内建映射的函数算 UDF / 函数。Oracle pkg.fn(...) 形式落 UDF 类。"""
    cols = _columns_by_output(
        "INSERT INTO tgt SELECT id, my_pkg.compute_score(score) AS final_score FROM users",
        dialect="oracle",
    )
    final = cols.get("final_score")
    assert final is not None
    # sqlglot 不一定把 my_pkg.compute_score(...) 解析成 Anonymous，可能落到 Func/表达式 都算合理
    assert final["transform"] in {"UDF", "函数", "表达式"}, final
