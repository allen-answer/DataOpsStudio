"""DM 慢 SQL analyze 测试(Phase 14 #3:DM 走 EXPLAIN SELECT 独立路径)。

核心断言:
1. DM 使用 EXPLAIN SELECT,不走 Oracle PLAN_TABLE
2. DM 返回 dialect="dm"
3. DM operator 规则识别(CSCN/SSEK/HAGR/NEST LOOP/SORT)
4. DM SQL Guard 拦 DML/DDL
5. DM prod allow_dm_explain flag 控制 endpoint policy
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.models.common import DatabaseType
from app.models.datasource import DataSourceCreate, make_sandbox_datasource_kwargs
from app.services import slow_sql as svc
from app.services.slow_sql import (
    analyze_sql,
    build_dm_suggestions,
    detect_dm_issues,
)


def _dm_row(operation: str = "", cardinality: int | None = None, **extra):
    """构造一行 DM EXPLAIN 输出。"""
    row: dict = {"operation": operation}
    if cardinality is not None:
        row["cardinality"] = cardinality
    row.update(extra)
    return row


# ─── DM rule detection(纯函数,不连库) ───────────────────────────────────────


def test_detect_dm_cscn_full_table_scan():
    rows = [_dm_row("CSCN2 [ods.orders]")]
    issues = detect_dm_issues(rows)
    assert any(i.code == "full_table_scan" for i in issues), [i.code for i in issues]


def test_detect_dm_ssek_index_range_scan_info():
    """SSEK / CSEK 是 info 级 — 走索引正常情况"""
    rows = [_dm_row("SSEK2 [idx_orders_id]")]
    issues = detect_dm_issues(rows)
    assert any(i.code == "index_range_scan" and i.severity == "info" for i in issues)


def test_detect_dm_hagr_hash_group():
    rows = [_dm_row("HAGR2 [...]")]
    issues = detect_dm_issues(rows)
    assert any(i.code == "hash_group" for i in issues)


def test_detect_dm_sagr_stream_group_info():
    rows = [_dm_row("SAGR2 [...]")]
    issues = detect_dm_issues(rows)
    assert any(i.code == "stream_group" and i.severity == "info" for i in issues)


def test_detect_dm_nest_loop_large():
    rows = [_dm_row("NEST LOOP [...]", cardinality=50000)]
    issues = detect_dm_issues(rows)
    assert any(i.code == "nested_loop_large" for i in issues)


def test_detect_dm_nest_loop_small_no_warn():
    rows = [_dm_row("NEST LOOP [...]", cardinality=100)]
    issues = detect_dm_issues(rows)
    # 行数小不该触发
    assert not any(i.code == "nested_loop_large" for i in issues)


def test_detect_dm_sort_operator():
    rows = [_dm_row("SORT [order_date]")]
    issues = detect_dm_issues(rows)
    assert any(i.code == "sort_cost" for i in issues)


def test_build_dm_suggestions_dedup():
    """同类 issue 多次出现,建议只生成一条"""
    from app.services.slow_sql import Issue
    issues = [
        Issue(severity="warning", code="full_table_scan", message="t1"),
        Issue(severity="warning", code="full_table_scan", message="t2"),
    ]
    sugs = build_dm_suggestions(issues)
    add_idx_count = sum(1 for s in sugs if s.code == "add_index")
    assert add_idx_count == 1


# ─── analyze_sql 端到端 dispatch — DM 不走 Oracle PLAN_TABLE ─────────────


@pytest.fixture
def dm_datasource(isolated_storage):
    """sandbox DM ds"""
    from app.services.repositories import datasource_store
    return datasource_store.create(DataSourceCreate(
        name="mock-dm",
        db_type=DatabaseType.DM,
        host="localhost", port=5236,
        database="DAMENG", username="u", password="p",
        **make_sandbox_datasource_kwargs(),
    ))


def test_dm_analyze_uses_explain_select_not_plan_table(
    isolated_storage, dm_datasource, monkeypatch,
):
    """DM analyze 必须用 EXPLAIN SELECT,不调用 _fetch_oracle_plan。"""
    captured = {}

    def fake_fetch_rows(source, sql, max_rows=None):
        captured["sql"] = sql
        return [_dm_row("CSCN2 [ods.t1]")]

    monkeypatch.setattr(svc, "fetch_rows", fake_fetch_rows)

    # 同时拦截 _fetch_oracle_plan — 一旦被调用立刻 fail
    def boom(*a, **kw):
        raise AssertionError("DM should NOT call _fetch_oracle_plan")

    monkeypatch.setattr(svc, "_fetch_oracle_plan", boom)

    result = analyze_sql(dm_datasource.id, "SELECT * FROM ods.t1 WHERE id=1")

    assert result["dialect"] == "dm"
    assert captured["sql"].upper().startswith("EXPLAIN ")
    # 必须不是 PLAN_TABLE 路径
    assert "PLAN_TABLE" not in captured["sql"].upper()
    assert any(i["code"] == "full_table_scan" for i in result["issues"])


def test_dm_analyze_dml_blocked_by_sql_guard(isolated_storage, dm_datasource):
    """DM analyze 入口的 SQL Guard 必须拦 DML/DDL。"""
    from app.services.slow_sql import SlowSqlError
    with pytest.raises((SlowSqlError, ValueError)):
        analyze_sql(dm_datasource.id, "DELETE FROM ods.t1")


# ─── endpoint policy:prod DM allow_dm_explain flag 控制 ─────────────────


def test_endpoint_dm_prod_denied_without_flag(client, isolated_storage):
    """prod DM 没 allow_dm_explain → analyze 403"""
    from app.services.repositories import datasource_store
    ds = datasource_store.create(DataSourceCreate(
        name="prod-dm",
        db_type=DatabaseType.DM,
        host="x", port=5236, database="X", username="x", password="x",
        environment="prod", allow_dm_explain=False, allow_explain=False,
    ))
    r = client.post(
        "/api/slow-sql/analyze",
        json={"sql": "SELECT * FROM t", "datasource_id": ds.id},
    )
    assert r.status_code == 403, r.text
    assert "DM" in r.text or "allow_dm_explain" in r.text


def test_endpoint_dm_prod_allowed_with_flag(client, isolated_storage, monkeypatch):
    """prod DM allow_dm_explain=True → analyze 走通(monkeypatch fetch_rows)"""
    from app.services.repositories import datasource_store
    ds = datasource_store.create(DataSourceCreate(
        name="prod-dm-ok",
        db_type=DatabaseType.DM,
        host="x", port=5236, database="X", username="x", password="x",
        environment="prod", allow_dm_explain=True,
    ))
    monkeypatch.setattr(
        svc, "fetch_rows",
        lambda source, sql, max_rows=None: [_dm_row("SSEK2 [...]")],
    )
    r = client.post(
        "/api/slow-sql/analyze",
        json={"sql": "SELECT * FROM t WHERE id=1", "datasource_id": ds.id},
    )
    assert r.status_code == 200, r.text
    assert r.json()["dialect"] == "dm"
