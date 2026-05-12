"""Slow SQL analyze tests（Phase 12 切片 6）。

scope:
- 纯规则函数（detect_issues / build_suggestions）—— 不接 DB
- analyze_sql 端到端（mock fetch_rows）
- /api/slow-sql/analyze endpoint via TestClient
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.models.common import DatabaseType
from app.services.slow_sql import (
    Issue,
    SlowSqlError,
    analyze_sql,
    build_suggestions,
    detect_issues,
)


# ─── 纯规则：detect_issues ──────────────────────────────────────────────────


def _row(**kw: Any) -> dict[str, Any]:
    """构造一条 EXPLAIN row（用 mysql 列名）。"""
    return {
        "id": 1,
        "select_type": "SIMPLE",
        "table": "",
        "type": "",
        "possible_keys": None,
        "key": None,
        "key_len": None,
        "ref": None,
        "rows": 0,
        "Extra": "",
        **kw,
    }


def test_detect_type_all_flags_full_scan():
    issues = detect_issues([_row(table="orders", type="ALL", rows=5000)])
    codes = [i.code for i in issues]
    assert "full_table_scan" in codes


def test_detect_filesort_flags_order_by():
    issues = detect_issues([_row(table="orders", type="ref", Extra="Using filesort")])
    codes = [i.code for i in issues]
    assert "filesort" in codes


def test_detect_using_temporary_flags_group_by():
    issues = detect_issues([_row(table="orders", type="ref", Extra="Using temporary; Using filesort")])
    codes = [i.code for i in issues]
    # 一条 Extra 同时触发两条规则
    assert "using_temporary" in codes
    assert "filesort" in codes


def test_detect_high_row_scan_when_index():
    issues = detect_issues([_row(table="big", type="index", rows=50000)])
    codes = [i.code for i in issues]
    assert "high_row_scan" in codes


def test_detect_high_row_scan_skipped_for_ref():
    """type=ref 已走索引定位，rows>10k 不该再报 high_row_scan。"""
    issues = detect_issues([_row(table="big", type="ref", rows=50000)])
    codes = [i.code for i in issues]
    assert "high_row_scan" not in codes


def test_detect_clean_plan_yields_no_issues():
    issues = detect_issues([_row(table="small", type="const", rows=1, Extra="")])
    assert issues == []


def test_detect_handles_multiple_tables():
    issues = detect_issues([
        _row(table="orders", type="ALL", rows=10000),
        _row(table="users", type="ALL", rows=200),
    ])
    tables = sorted({i.table for i in issues if i.code == "full_table_scan"})
    assert tables == ["orders", "users"]


def test_detect_lowercase_extra_field_name():
    """有些 driver 用 `extra` 而非 `Extra` 当列名 —— 应该都识别。"""
    issues = detect_issues([_row(table="t", type="ref", extra="Using filesort", Extra="")])
    assert any(i.code == "filesort" for i in issues)


def test_detect_rows_unparseable_treated_as_zero():
    issues = detect_issues([_row(table="t", type="ALL", rows="N/A")])
    # full_table_scan 仍命中（不依赖 rows）；high_row_scan 不触发
    codes = [i.code for i in issues]
    assert "full_table_scan" in codes
    assert "high_row_scan" not in codes


# ─── 纯规则：build_suggestions ──────────────────────────────────────────────


def test_suggestions_one_per_table_for_full_scan():
    issues = [
        Issue(severity="warning", code="full_table_scan", message="m", table="orders"),
        Issue(severity="warning", code="full_table_scan", message="m", table="orders"),
    ]
    sugg = build_suggestions(issues)
    # 同表只产 1 条 add_index
    assert sum(1 for s in sugg if s.code == "add_index") == 1


def test_suggestions_per_table_for_different_tables():
    issues = [
        Issue(severity="warning", code="full_table_scan", message="m", table="orders"),
        Issue(severity="warning", code="full_table_scan", message="m", table="users"),
    ]
    sugg = build_suggestions(issues)
    assert sum(1 for s in sugg if s.code == "add_index") == 2


def test_suggestions_for_filesort():
    issues = [Issue(severity="warning", code="filesort", message="m", table="orders")]
    sugg = build_suggestions(issues)
    assert any(s.code == "order_by_index" for s in sugg)


def test_suggestions_for_using_temporary_dedup():
    """Using temporary 是单一全局建议（不按表分），多 row 命中只产 1 条。"""
    issues = [
        Issue(severity="warning", code="using_temporary", message="m", table="a"),
        Issue(severity="warning", code="using_temporary", message="m", table="b"),
    ]
    sugg = build_suggestions(issues)
    assert sum(1 for s in sugg if s.code == "group_by_index") == 1


def test_suggestions_for_high_row_scan():
    issues = [Issue(severity="warning", code="high_row_scan", message="m", table="big")]
    sugg = build_suggestions(issues)
    assert any(s.code == "narrow_scan" for s in sugg)


def test_suggestions_empty_for_no_issues():
    assert build_suggestions([]) == []


# ─── analyze_sql 端到端（mock datasource + fetch_rows） ──────────────────────


@pytest.fixture
def mysql_datasource(isolated_storage):
    """注一份 MySQL datasource 到 isolated 的 task_store / datasource_store。"""
    from app.models.datasource import DataSourceCreate
    from app.services.repositories import datasource_store

    return datasource_store.create(DataSourceCreate(
        name="mock-mysql",
        db_type=DatabaseType.MYSQL,
        host="localhost", port=3306,
        database="demo", username="u", password="p",
    ))


def test_analyze_sql_returns_full_envelope(isolated_storage, mysql_datasource, monkeypatch):
    canned_plan = [
        _row(table="orders", type="ALL", rows=5000),
        _row(table="users", type="ref", rows=100, Extra="Using filesort"),
    ]
    from app.services import slow_sql as svc
    monkeypatch.setattr(svc, "fetch_rows", lambda src, sql, max_rows=None: canned_plan)

    result = analyze_sql(mysql_datasource.id, "SELECT * FROM orders o JOIN users u ON o.uid = u.id ORDER BY u.name")
    assert result["dialect"] == "mysql"
    assert result["explain_sql"].startswith("EXPLAIN ")
    assert len(result["plan"]) == 2
    issue_codes = [i["code"] for i in result["issues"]]
    assert "full_table_scan" in issue_codes
    assert "filesort" in issue_codes
    sugg_codes = [s["code"] for s in result["suggestions"]]
    assert "add_index" in sugg_codes
    assert "order_by_index" in sugg_codes


def test_analyze_sql_strips_trailing_semicolon(isolated_storage, mysql_datasource, monkeypatch):
    captured = {}
    from app.services import slow_sql as svc

    def fake(src, sql, max_rows=None):
        captured["sql"] = sql
        return []

    monkeypatch.setattr(svc, "fetch_rows", fake)
    analyze_sql(mysql_datasource.id, "SELECT 1;")
    # EXPLAIN prepend 时去掉了末尾分号
    assert captured["sql"] == "EXPLAIN SELECT 1"


def test_analyze_sql_rejects_non_select(isolated_storage, mysql_datasource):
    with pytest.raises(ValueError, match="Only SELECT/WITH"):
        analyze_sql(mysql_datasource.id, "DELETE FROM orders WHERE id=1")


def test_analyze_sql_rejects_empty_datasource_id(isolated_storage):
    with pytest.raises(SlowSqlError, match="datasource_id"):
        analyze_sql("", "SELECT 1")


def test_analyze_sql_404_on_unknown_datasource(isolated_storage):
    with pytest.raises(SlowSqlError, match="not found"):
        analyze_sql("no-such", "SELECT 1")


def test_analyze_sql_rejects_non_mysql(isolated_storage, monkeypatch):
    from app.models.datasource import DataSourceCreate
    from app.services.repositories import datasource_store

    oracle_ds = datasource_store.create(DataSourceCreate(
        name="oracle-1", db_type=DatabaseType.ORACLE,
        host="localhost", port=1521, database="ORCL", username="u", password="p",
    ))
    with pytest.raises(SlowSqlError, match="MySQL only"):
        analyze_sql(oracle_ds.id, "SELECT 1")


def test_analyze_sql_wraps_driver_error(isolated_storage, mysql_datasource, monkeypatch):
    from app.services import slow_sql as svc

    def boom(*a, **kw):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(svc, "fetch_rows", boom)
    with pytest.raises(SlowSqlError, match="EXPLAIN failed"):
        analyze_sql(mysql_datasource.id, "SELECT 1")


# ─── /api/slow-sql/analyze endpoint ─────────────────────────────────────────


@pytest.fixture
def client(isolated_storage):
    from main import app
    return TestClient(app)


def test_endpoint_happy_path(client, mysql_datasource, monkeypatch):
    from app.services import slow_sql as svc
    monkeypatch.setattr(svc, "fetch_rows", lambda src, sql, max_rows=None: [
        _row(table="orders", type="ALL", rows=8000),
    ])
    r = client.post(
        "/api/slow-sql/analyze",
        json={"sql": "SELECT * FROM orders", "datasource_id": mysql_datasource.id},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dialect"] == "mysql"
    assert any(i["code"] == "full_table_scan" for i in body["issues"])


def test_endpoint_400_on_unknown_datasource(client, isolated_storage):
    r = client.post(
        "/api/slow-sql/analyze",
        json={"sql": "SELECT 1", "datasource_id": "no-such"},
    )
    assert r.status_code == 400


def test_endpoint_400_on_bad_sql(client, mysql_datasource):
    r = client.post(
        "/api/slow-sql/analyze",
        json={"sql": "DELETE FROM users", "datasource_id": mysql_datasource.id},
    )
    assert r.status_code == 400
    body = r.json()
    assert "sql validation failed" in str(body) or "SELECT" in str(body)


def test_endpoint_422_on_missing_field(client):
    r = client.post("/api/slow-sql/analyze", json={"sql": ""})
    assert r.status_code in (400, 422)
