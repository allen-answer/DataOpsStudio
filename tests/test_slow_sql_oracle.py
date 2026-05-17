"""Oracle / DM slow-sql tests（Phase 12 切片 16）。

scope:
- detect_oracle_issues 6 条规则（pure plan-row rule funcs, IO 外）
- build_oracle_suggestions 派生 + dedup
- analyze_sql 在 dialect=oracle/dm 时走 _analyze_oracle 路径（monkeypatch _fetch_oracle_plan）
- DM 复用 Oracle 路径
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.models.datasource import DatabaseType, DataSourceCreate
from app.services.slow_sql import (
    SlowSqlError,
    analyze_sql,
    build_oracle_suggestions,
    detect_oracle_issues,
)


def _orow(**kw: Any) -> dict[str, Any]:
    """PLAN_TABLE row lowercase 字段，模拟 cursor.description 归一化结果。"""
    base = {
        "id": 0, "operation": None, "options": None, "object_name": None,
        "cardinality": None, "cost": None, "bytes": None, "depth": 0, "parent_id": None,
    }
    base.update(kw)
    return base


# ─── pure detect rules ─────────────────────────────────────────────────────


def test_oracle_detect_table_access_full_flags_full_scan():
    rows = [_orow(operation="TABLE ACCESS", options="FULL",
                  object_name="ODS.ORDERS", cardinality=5000, cost=120)]
    issues = detect_oracle_issues(rows)
    codes = [i.code for i in issues]
    assert "full_table_scan" in codes
    full = next(i for i in issues if i.code == "full_table_scan")
    assert full.table == "ODS.ORDERS"
    assert "cardinality≈5000" in full.detail


def test_oracle_detect_sort_order_by():
    rows = [_orow(operation="SORT", options="ORDER BY", cost=80)]
    issues = detect_oracle_issues(rows)
    assert any(i.code == "sort_order_by" for i in issues)


def test_oracle_detect_sort_group_by():
    rows = [_orow(operation="SORT", options="GROUP BY", cost=60)]
    issues = detect_oracle_issues(rows)
    assert any(i.code == "sort_group_by" for i in issues)


def test_oracle_detect_sort_unique():
    """SORT UNIQUE 也算 sort_group_by（DISTINCT 触发）。"""
    rows = [_orow(operation="SORT", options="UNIQUE", cost=50)]
    issues = detect_oracle_issues(rows)
    assert any(i.code == "sort_group_by" for i in issues)


def test_oracle_detect_nested_loops_high_card():
    rows = [_orow(operation="NESTED LOOPS", cardinality=50000, cost=400)]
    issues = detect_oracle_issues(rows)
    codes = [i.code for i in issues]
    assert "nested_loops_high_card" in codes


def test_oracle_detect_nested_loops_below_threshold_skipped():
    rows = [_orow(operation="NESTED LOOPS", cardinality=5000, cost=200)]
    issues = detect_oracle_issues(rows)
    assert all(i.code != "nested_loops_high_card" for i in issues)


def test_oracle_detect_high_cost_step():
    rows = [_orow(operation="HASH JOIN", cardinality=100, cost=1500)]
    issues = detect_oracle_issues(rows)
    high = next(i for i in issues if i.code == "high_cost")
    assert high.severity == "info"
    assert "cost=1500" in high.message


def test_oracle_detect_select_statement_skipped_for_high_cost():
    """SELECT STATEMENT 是顶层壳子，自身 cost 不该单独报 high_cost。"""
    rows = [_orow(operation="SELECT STATEMENT", cost=2000)]
    issues = detect_oracle_issues(rows)
    assert all(i.code != "high_cost" for i in issues)


def test_oracle_detect_high_row_scan_combines_with_full_scan():
    """超大 cardinality + TABLE ACCESS FULL 同时触发 full_table_scan + high_row_scan。"""
    rows = [_orow(operation="TABLE ACCESS", options="FULL",
                  object_name="DWH.FACT", cardinality=500000, cost=2000)]
    issues = detect_oracle_issues(rows)
    codes = [i.code for i in issues]
    assert "full_table_scan" in codes
    assert "high_row_scan" in codes
    assert "high_cost" in codes  # cost>1000 走 high_cost


def test_oracle_detect_clean_plan_no_issues():
    rows = [
        _orow(operation="SELECT STATEMENT", cost=10),
        _orow(operation="INDEX", options="UNIQUE SCAN",
              object_name="IDX_ORDERS_PK", cardinality=1, cost=2),
    ]
    issues = detect_oracle_issues(rows)
    assert issues == []


def test_oracle_detect_cardinality_none_treated_as_zero():
    rows = [_orow(operation="NESTED LOOPS", cardinality=None)]
    issues = detect_oracle_issues(rows)
    # cardinality=None → 0 < 10000 → 不触发 nested_loops_high_card
    assert all(i.code != "nested_loops_high_card" for i in issues)


def test_oracle_detect_mixed_case_options_normalized():
    """PLAN_TABLE 字段大小写不一致时（某些 client 全大写 / 部分小写），仍能识别。"""
    rows = [_orow(operation="table access", options="full",
                  object_name="orders", cardinality=2000)]
    issues = detect_oracle_issues(rows)
    assert any(i.code == "full_table_scan" for i in issues)


# ─── build_oracle_suggestions ─────────────────────────────────────────────


def test_oracle_suggestions_for_full_scan():
    issues = detect_oracle_issues([_orow(
        operation="TABLE ACCESS", options="FULL",
        object_name="ORDERS", cardinality=5000)])
    sugg = build_oracle_suggestions(issues)
    codes = [s.code for s in sugg]
    assert "add_index" in codes


def test_oracle_suggestions_dedup_per_table():
    """同表多次 full_scan 只给一条 add_index 建议。"""
    issues = detect_oracle_issues([
        _orow(operation="TABLE ACCESS", options="FULL", object_name="ORDERS", cardinality=1000),
        _orow(operation="TABLE ACCESS", options="FULL", object_name="ORDERS", cardinality=2000),
    ])
    sugg = build_oracle_suggestions(issues)
    add_idx = [s for s in sugg if s.code == "add_index"]
    assert len(add_idx) == 1


def test_oracle_suggestions_nested_loops_yields_force_hash_join():
    issues = detect_oracle_issues([
        _orow(operation="NESTED LOOPS", cardinality=50000)])
    sugg = build_oracle_suggestions(issues)
    codes = [s.code for s in sugg]
    assert "force_hash_join" in codes


def test_oracle_suggestions_high_cost_yields_review_cost():
    issues = detect_oracle_issues([_orow(operation="HASH JOIN", cost=1500)])
    sugg = build_oracle_suggestions(issues)
    codes = [s.code for s in sugg]
    assert "review_cost" in codes


def test_oracle_suggestions_sort_group_by_global_dedup():
    """多 table 触发 sort_group_by 只产 1 条 group_by_index 建议。"""
    issues = detect_oracle_issues([
        _orow(operation="SORT", options="GROUP BY"),
        _orow(operation="SORT", options="UNIQUE"),
    ])
    sugg = build_oracle_suggestions(issues)
    grp = [s for s in sugg if s.code == "group_by_index"]
    assert len(grp) == 1


# ─── analyze_sql IO path（dialect=oracle / dm） ───────────────────────────


@pytest.fixture
def oracle_datasource(isolated_storage):
    from app.services.repositories import datasource_store
    return datasource_store.create(DataSourceCreate(
        name="mock-oracle", db_type=DatabaseType.ORACLE,
        host="localhost", port=1521, database="ORCL",
        username="u", password="p",
    ))


@pytest.fixture
def dm_datasource(isolated_storage):
    from app.services.repositories import datasource_store
    return datasource_store.create(DataSourceCreate(
        name="mock-dm", db_type=DatabaseType.DM,
        host="localhost", port=5236, database="DAMENG",
        username="u", password="p",
    ))


def test_analyze_sql_oracle_dialect_returns_oracle_envelope(oracle_datasource, monkeypatch):
    canned = [
        _orow(id=0, operation="SELECT STATEMENT", cost=120),
        _orow(id=1, operation="TABLE ACCESS", options="FULL",
              object_name="ORDERS", cardinality=5000, cost=120),
    ]
    from app.services import slow_sql as svc
    monkeypatch.setattr(svc, "_fetch_oracle_plan", lambda src, sql, n: canned)

    result = analyze_sql(oracle_datasource.id, "SELECT * FROM orders WHERE 1=1")
    assert result["dialect"] == "oracle"
    assert result["explain_sql"].startswith("EXPLAIN PLAN FOR ")
    assert "SELECT * FROM orders" in result["explain_sql"]
    codes = [i["code"] for i in result["issues"]]
    assert "full_table_scan" in codes


def test_analyze_sql_dm_uses_oracle_path(dm_datasource, monkeypatch):
    """DM 走 oracle 路径但 dialect 字段保持 'oracle'（PLAN_TABLE 协议一致）。"""
    canned = [_orow(operation="TABLE ACCESS", options="FULL",
                    object_name="T", cardinality=100)]
    from app.services import slow_sql as svc
    monkeypatch.setattr(svc, "_fetch_oracle_plan", lambda src, sql, n: canned)

    result = analyze_sql(dm_datasource.id, "SELECT * FROM t")
    assert result["dialect"] == "oracle"
    assert len(result["plan"]) == 1


def test_analyze_sql_oracle_strips_trailing_semicolon(oracle_datasource, monkeypatch):
    """EXPLAIN PLAN FOR 不能末尾带分号（Oracle DDL 拒绝），同 MySQL 路径一致 strip。"""
    captured = {}
    from app.services import slow_sql as svc

    def fake(source, sql, n):
        captured["sql"] = sql
        return []

    monkeypatch.setattr(svc, "_fetch_oracle_plan", fake)
    result = analyze_sql(oracle_datasource.id, "SELECT 1 FROM dual;")
    assert captured["sql"] == "SELECT 1 FROM dual"
    assert result["explain_sql"] == "EXPLAIN PLAN FOR SELECT 1 FROM dual"


def test_analyze_sql_oracle_driver_error_wrapped(oracle_datasource, monkeypatch):
    from app.services import slow_sql as svc

    def boom(*a, **kw):
        raise RuntimeError("ORA-00942: table or view does not exist")

    monkeypatch.setattr(svc, "_fetch_oracle_plan", boom)
    with pytest.raises(SlowSqlError, match="EXPLAIN failed"):
        analyze_sql(oracle_datasource.id, "SELECT * FROM no_such")


# ─── endpoint integration ─────────────────────────────────────────────────


# `client` fixture 来自 conftest.py（admin-authed）。


def test_endpoint_analyze_oracle_dispatches_correctly(client, oracle_datasource, monkeypatch):
    canned = [_orow(operation="TABLE ACCESS", options="FULL",
                    object_name="ODS.ORDERS", cardinality=5000)]
    from app.services import slow_sql as svc
    monkeypatch.setattr(svc, "_fetch_oracle_plan", lambda src, sql, n: canned)

    r = client.post("/api/slow-sql/analyze", json={
        "sql": "SELECT * FROM ods.orders",
        "datasource_id": oracle_datasource.id,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dialect"] == "oracle"
    assert any(i["code"] == "full_table_scan" for i in body["issues"])


def test_endpoint_enrich_accepts_dialect_param(client):
    """enrich 接 dialect 字段，provider=off 时仍 200 + ok=False。"""
    r = client.post("/api/slow-sql/enrich", json={
        "sql": "SELECT * FROM t",
        "plan": [],
        "issues": [],
        "suggestions": [],
        "expected_optimizations": [],
        "dialect": "oracle",
    })
    assert r.status_code == 200
    body = r.json()
    # 默认 provider 配置 off → ok=False 不抛
    assert "ok" in body
