"""sql_preflight 静态体检测试 —— 每条规则正反例 + block/warn 分级 + 端点。

`assess_sql()` 是纯函数（不连库），绝大多数测试无需 fixture。
"""
from __future__ import annotations

from app.services.sql_preflight import SQLPreflightDecision, assess_sql


def _codes(decision: SQLPreflightDecision) -> set[str]:
    return {r.code for r in decision.rules}


def _level(decision: SQLPreflightDecision, code: str) -> str:
    return next(r.level for r in decision.rules if r.code == code)


# ─── 干净 SQL ────────────────────────────────────────────────────────────────


def test_clean_sql_has_no_rules():
    decision = assess_sql(sql="SELECT id, name FROM users WHERE id > 0")
    assert decision.rules == []
    assert decision.blocking is False
    assert decision.risk_level == "low"
    assert decision.normalized_sql is not None


# ─── not_readonly ───────────────────────────────────────────────────────────


def test_ddl_blocked_as_not_readonly():
    decision = assess_sql(sql="DROP TABLE users")
    assert decision.blocking is True
    assert "not_readonly" in _codes(decision)


def test_multi_statement_blocked():
    decision = assess_sql(sql="SELECT 1; SELECT 2")
    assert decision.blocking is True
    assert "not_readonly" in _codes(decision)


def test_empty_sql_blocked():
    decision = assess_sql(sql="   ")
    assert decision.blocking is True
    assert "not_readonly" in _codes(decision)


# ─── select_star ────────────────────────────────────────────────────────────


def test_select_star_warns():
    decision = assess_sql(sql="SELECT * FROM users WHERE id > 0")
    assert "select_star" in _codes(decision)
    assert _level(decision, "select_star") == "warn"
    assert decision.blocking is False


def test_select_star_blocks_for_large_task():
    decision = assess_sql(sql="SELECT * FROM users WHERE id > 0", max_rows=5_000_000)
    assert _level(decision, "select_star") == "block"
    assert decision.blocking is True


# ─── no_where ───────────────────────────────────────────────────────────────


def test_no_where_warns():
    decision = assess_sql(sql="SELECT id FROM users")
    assert "no_where" in _codes(decision)
    assert _level(decision, "no_where") == "warn"


def test_no_where_blocks_for_large_task():
    decision = assess_sql(sql="SELECT id FROM users", max_rows=5_000_000)
    assert _level(decision, "no_where") == "block"
    assert decision.blocking is True


def test_where_present_no_no_where_rule():
    decision = assess_sql(sql="SELECT id FROM users WHERE id > 0")
    assert "no_where" not in _codes(decision)


# ─── 流式有序性 ─────────────────────────────────────────────────────────────


def test_stream_without_order_blocks():
    decision = assess_sql(
        sql="SELECT id FROM users WHERE id > 0",
        key_columns=["id"], stream_compare=True,
    )
    assert "stream_no_order" in _codes(decision)
    assert decision.blocking is True
    assert decision.risk_level == "critical"


def test_stream_with_correct_order_ok():
    decision = assess_sql(
        sql="SELECT id, name FROM users WHERE id > 0 ORDER BY id",
        key_columns=["id"], stream_compare=True,
    )
    assert decision.rules == []
    assert decision.blocking is False


def test_order_not_covering_keys_blocks():
    decision = assess_sql(
        sql="SELECT id FROM users WHERE id > 0 ORDER BY name",
        key_columns=["id"], stream_compare=True,
    )
    assert "order_missing_keys" in _codes(decision)
    assert decision.blocking is True


def test_order_covers_multi_key_prefix():
    decision = assess_sql(
        sql="SELECT a, b FROM t WHERE a > 0 ORDER BY a, b, c",
        key_columns=["a", "b"], stream_compare=True,
    )
    assert "order_missing_keys" not in _codes(decision)
    assert "stream_no_order" not in _codes(decision)


def test_stream_rules_skipped_in_preview_mode():
    decision = assess_sql(
        sql="SELECT id FROM users WHERE id > 0",
        key_columns=["id"], stream_compare=True, mode="preview",
    )
    assert "stream_no_order" not in _codes(decision)


def test_stream_rules_skipped_when_not_streaming():
    decision = assess_sql(
        sql="SELECT id FROM users WHERE id > 0",
        key_columns=["id"], stream_compare=False,
    )
    assert "stream_no_order" not in _codes(decision)


# ─── 宽表 / 高成本算子 / 函数包裹排序 ───────────────────────────────────────


def test_wide_select_warns():
    cols = ", ".join(f"c{i}" for i in range(60))
    decision = assess_sql(sql=f"SELECT {cols} FROM t WHERE c0 > 0")
    assert "wide_select" in _codes(decision)
    assert _level(decision, "wide_select") == "warn"


def test_narrow_select_no_wide_rule():
    decision = assess_sql(sql="SELECT a, b, c FROM t WHERE a > 0")
    assert "wide_select" not in _codes(decision)


def test_expensive_ops_distinct_warns():
    decision = assess_sql(sql="SELECT DISTINCT id FROM users WHERE id > 0")
    assert "expensive_ops" in _codes(decision)


def test_expensive_ops_group_by_warns():
    decision = assess_sql(sql="SELECT dept, count(*) FROM users WHERE id > 0 GROUP BY dept")
    assert "expensive_ops" in _codes(decision)


def test_expensive_ops_union_warns():
    decision = assess_sql(
        sql="SELECT a FROM t1 WHERE a > 0 UNION SELECT a FROM t2 WHERE a > 0",
    )
    assert "expensive_ops" in _codes(decision)


def test_order_func_wrapped_warns():
    decision = assess_sql(sql="SELECT id FROM users WHERE id > 0 ORDER BY UPPER(name)")
    assert "order_func_wrapped" in _codes(decision)


# ─── parse 失败保守处理 ─────────────────────────────────────────────────────


def test_parse_failure_warns_not_silently_passed():
    # 过 readonly guard（首词 select、单语句、无禁词）但 sqlglot 解析不了
    decision = assess_sql(sql="SELECT FROM FROM WHERE WHERE )")
    assert "parse_failed" in _codes(decision)
    assert _level(decision, "parse_failed") == "warn"


# ─── 端点 ───────────────────────────────────────────────────────────────────


def test_preflight_endpoint_returns_decision(client):
    resp = client.post("/api/sql/preflight", json={
        "sql": "SELECT * FROM users",
        "max_rows": 5_000_000,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["blocking"] is True
    assert "select_star" in {r["code"] for r in body["rules"]}


def test_preflight_endpoint_clean_sql(client):
    resp = client.post("/api/sql/preflight", json={
        "sql": "SELECT id, name FROM users WHERE id > 0",
    })
    assert resp.status_code == 200
    assert resp.json()["blocking"] is False


def test_preflight_endpoint_rejects_empty_sql(client):
    resp = client.post("/api/sql/preflight", json={"sql": ""})
    assert resp.status_code == 400


def test_preflight_endpoint_key_columns_as_string(client):
    resp = client.post("/api/sql/preflight", json={
        "sql": "SELECT id FROM users WHERE id > 0",
        "key_columns": "id",
        "stream_compare": True,
    })
    assert resp.status_code == 200
    # stream + 无 ORDER BY → block
    assert resp.json()["blocking"] is True


def test_preflight_endpoint_requires_login(client_anon):
    resp = client_anon.post("/api/sql/preflight", json={"sql": "SELECT 1"})
    assert resp.status_code == 401
