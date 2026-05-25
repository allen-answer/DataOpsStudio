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
    """注一份 MySQL datasource 到 isolated 的 task_store / datasource_store。

    Phase 14 #3:显式标 sandbox + allow_* 全开,绕过 operation_policy 拒绝。
    """
    from app.models.datasource import DataSourceCreate, make_sandbox_datasource_kwargs
    from app.services.repositories import datasource_store

    return datasource_store.create(DataSourceCreate(
        name="mock-mysql",
        db_type=DatabaseType.MYSQL,
        host="localhost", port=3306,
        database="demo", username="u", password="p",
        **make_sandbox_datasource_kwargs(),
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


def test_analyze_sql_rejects_unsupported_dialect(isolated_storage, monkeypatch):
    """切片 16 起 oracle / dm 支持；DB2 / 未来其他方言仍拒。"""
    from app.models.datasource import DataSourceCreate
    from app.services.repositories import datasource_store

    db2_ds = datasource_store.create(DataSourceCreate(
        name="db2-1", db_type=DatabaseType.DB2,
        host="localhost", port=50000, database="SAMPLE", username="u", password="p",
    ))
    with pytest.raises(SlowSqlError, match="mysql / oracle / dm"):
        analyze_sql(db2_ds.id, "SELECT 1")


def test_analyze_sql_wraps_driver_error(isolated_storage, mysql_datasource, monkeypatch):
    from app.services import slow_sql as svc

    def boom(*a, **kw):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(svc, "fetch_rows", boom)
    with pytest.raises(SlowSqlError, match="EXPLAIN failed"):
        analyze_sql(mysql_datasource.id, "SELECT 1")


# ─── /api/slow-sql/analyze endpoint ─────────────────────────────────────────


# `client` fixture 来自 conftest.py（admin-authed）。


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


def test_endpoint_404_on_unknown_datasource(client, isolated_storage):
    """切到 404 —— 项目级授权层先校验 datasource 是否存在（datasource_id 找不到
    跟 SQL 报错语义不同），符合标准 HTTP 语义。"""
    r = client.post(
        "/api/slow-sql/analyze",
        json={"sql": "SELECT 1", "datasource_id": "no-such"},
    )
    assert r.status_code == 404


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


# ─── enrich_via_ai：单测（mock _call_ai） ────────────────────────────────────


@pytest.fixture
def ai_provider_off(isolated_storage, monkeypatch):
    """默认 provider=off，让 enrich 走降级分支。"""
    from app.services import lineage_ai as svc
    from app.services.lineage_ai import LineageAIConfig

    monkeypatch.setattr(svc, "_config", lambda: LineageAIConfig(provider="off"))
    return None


@pytest.fixture
def ai_provider_on(isolated_storage, monkeypatch):
    """注一个 fake openai provider + mock _call_ai 的返回。"""
    from app.services import lineage_ai as svc
    from app.services.lineage_ai import LineageAIConfig

    monkeypatch.setattr(svc, "_config", lambda: LineageAIConfig(
        provider="openai", model="gpt-fake", api_key="sk-test", base_url="https://x/v1",
    ))


def test_enrich_via_ai_provider_off_returns_disabled(ai_provider_off):
    from app.services.slow_sql import enrich_via_ai

    res = enrich_via_ai(
        sql="SELECT 1",
        plan=[],
        issues=[],
        suggestions=[],
        expected_optimizations=["create index idx_x on t(c)"],
    )
    assert res.ok is False
    assert "未启用" in res.error
    # expected_optimizations 不为空时 missing 默认填全集
    assert res.expected_coverage["missing"] == ["create index idx_x on t(c)"]


def test_enrich_via_ai_happy_path(ai_provider_on, monkeypatch):
    from app.api import ai_utils
    from app.services.slow_sql import enrich_via_ai

    captured = {}

    def fake_call(provider_name, config, system_prompt, user_payload):
        captured["provider"] = provider_name
        captured["payload_keys"] = sorted(user_payload.keys())
        return {
            "summary": "全表扫描导致主要开销",
            "issue_review": [
                {"code": "full_table_scan", "verdict": "confirmed", "rationale": "rows≈8000 且无 key"},
            ],
            "extra_suggestions": [
                {"message": "LEFT JOIN 改 INNER JOIN", "sql": "", "confidence": "medium"},
                {"message": "为 ods.orders.created_at 加索引",
                 "sql": "CREATE INDEX idx_orders_created_at ON ods.orders(created_at)",
                 "confidence": "high"},
            ],
            "expected_coverage": {
                "matched": ["create index idx_orders_created_at on ods.orders(created_at)"],
                "missing": ["rewrite subquery as derived table + JOIN"],
                "coverage_pct": 33.3,
            },
        }

    monkeypatch.setattr(ai_utils, "_call_ai", fake_call)
    # service 里 lazy import，monkeypatch ai_utils 模块属性后 import 会拿到 patched

    res = enrich_via_ai(
        sql="SELECT * FROM ods.orders",
        plan=[{"table": "orders", "type": "ALL", "rows": 8000}],
        issues=[{"code": "full_table_scan", "table": "orders"}],
        suggestions=[{"code": "add_index", "message": "..."}],
        expected_optimizations=[
            "create index idx_orders_created_at on ods.orders(created_at)",
            "rewrite subquery as derived table + JOIN",
            "convert LEFT JOIN to INNER JOIN",
        ],
    )
    assert res.ok is True
    assert "全表扫描" in res.summary
    assert len(res.issue_review) == 1
    assert res.issue_review[0]["verdict"] == "confirmed"
    assert len(res.extra_suggestions) == 2
    assert res.expected_coverage["coverage_pct"] == 33.3
    assert "rewrite subquery" in res.expected_coverage["missing"][0]
    # provider name + payload keys 都正确路由进去
    assert captured["provider"] == "openai"
    assert set(captured["payload_keys"]) >= {"sql", "plan", "rule_issues", "rule_suggestions",
                                              "expected_optimizations"}


def test_enrich_via_ai_filters_non_dict_items(ai_provider_on, monkeypatch):
    """LLM 偶尔返字符串列表 / null，应过滤成空 list 不抛。"""
    from app.api import ai_utils
    from app.services.slow_sql import enrich_via_ai

    monkeypatch.setattr(ai_utils, "_call_ai", lambda *a, **kw: {
        "summary": "ok",
        "issue_review": ["bad", {"code": "x", "verdict": "confirmed"}],
        "extra_suggestions": None,  # null
        "expected_coverage": {},
    })

    res = enrich_via_ai(sql="SELECT 1", plan=[], issues=[], suggestions=[])
    assert res.ok is True
    assert len(res.issue_review) == 1  # 字符串被过滤
    assert res.extra_suggestions == []  # null 转空 list


def test_enrich_via_ai_call_error_wrapped(ai_provider_on, monkeypatch):
    from app.api import ai_utils
    from app.services.slow_sql import enrich_via_ai

    def boom(*a, **kw):
        raise RuntimeError("network down")

    monkeypatch.setattr(ai_utils, "_call_ai", boom)

    res = enrich_via_ai(sql="SELECT 1", plan=[], issues=[], suggestions=[])
    assert res.ok is False
    assert "network down" in res.error
    assert res.elapsed_seconds >= 0  # mocked 抛错耗时 < 1ms 可能 round 到 0


def test_enrich_via_ai_coverage_backfilled_from_matched(ai_provider_on, monkeypatch):
    """LLM 漏 coverage_pct 但给了 matched + 有 expected，按比例反算。"""
    from app.api import ai_utils
    from app.services.slow_sql import enrich_via_ai

    monkeypatch.setattr(ai_utils, "_call_ai", lambda *a, **kw: {
        "summary": "",
        "issue_review": [],
        "extra_suggestions": [],
        "expected_coverage": {"matched": ["a", "b"], "missing": ["c", "d"]},  # 漏 pct
    })

    res = enrich_via_ai(
        sql="SELECT 1", plan=[], issues=[], suggestions=[],
        expected_optimizations=["a", "b", "c", "d"],
    )
    assert res.expected_coverage["coverage_pct"] == 50.0  # 2/4


def test_enrich_via_ai_coverage_pct_clamped(ai_provider_on, monkeypatch):
    """LLM 给非法 pct（150 / -5 / "abc"），统一 clamp 到 [0, 100]。"""
    from app.api import ai_utils
    from app.services.slow_sql import enrich_via_ai

    for raw_pct, expected in [(150, 100.0), (-5, 0.0), ("abc", 0.0), (None, 0.0)]:
        monkeypatch.setattr(ai_utils, "_call_ai", lambda *a, **kw: {
            "summary": "", "issue_review": [], "extra_suggestions": [],
            "expected_coverage": {"matched": [], "missing": [], "coverage_pct": raw_pct},
        })
        res = enrich_via_ai(sql="SELECT 1", plan=[], issues=[], suggestions=[])
        assert res.expected_coverage["coverage_pct"] == expected, f"raw={raw_pct}"


def test_enrich_via_ai_plan_truncated_when_huge(ai_provider_on, monkeypatch):
    """大 plan 切半防 token 爆。"""
    from app.api import ai_utils
    from app.services.slow_sql import enrich_via_ai

    captured = {}

    def fake(provider_name, config, system_prompt, user_payload):
        captured["plan_len"] = len(user_payload["plan"])
        captured["truncated"] = user_payload.get("plan_truncated", False)
        return {"summary": "", "issue_review": [], "extra_suggestions": [], "expected_coverage": {}}

    monkeypatch.setattr(ai_utils, "_call_ai", fake)
    # 50 行每条 ~150 字符 → 总 ~7500 chars > 4000 默认上限
    big_plan = [{"table": f"t{i}", "extra_info": "x" * 100} for i in range(50)]
    enrich_via_ai(sql="SELECT 1", plan=big_plan, issues=[], suggestions=[])
    assert captured["truncated"] is True
    assert captured["plan_len"] < 50


# ─── /api/slow-sql/enrich endpoint ──────────────────────────────────────────


def test_enrich_endpoint_provider_off_returns_200_ok_false(client, ai_provider_off):
    r = client.post(
        "/api/slow-sql/enrich",
        json={"sql": "SELECT 1", "plan": [], "issues": [], "suggestions": []},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "未启用" in body["error"]


def test_enrich_endpoint_happy_path(client, ai_provider_on, monkeypatch):
    from app.api import ai_utils
    monkeypatch.setattr(ai_utils, "_call_ai", lambda *a, **kw: {
        "summary": "fake summary",
        "issue_review": [{"code": "full_table_scan", "verdict": "confirmed", "rationale": "..."}],
        "extra_suggestions": [{"message": "加索引", "sql": "CREATE INDEX ...", "confidence": "high"}],
        "expected_coverage": {"matched": ["x"], "missing": [], "coverage_pct": 100.0},
    })

    r = client.post(
        "/api/slow-sql/enrich",
        json={
            "sql": "SELECT 1",
            "plan": [{"table": "t", "type": "ALL", "rows": 5000}],
            "issues": [{"code": "full_table_scan", "table": "t"}],
            "suggestions": [],
            "expected_optimizations": ["x"],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["summary"] == "fake summary"
    assert body["expected_coverage"]["coverage_pct"] == 100.0


def test_enrich_endpoint_422_on_missing_sql(client):
    r = client.post("/api/slow-sql/enrich", json={"plan": [], "issues": []})
    assert r.status_code in (400, 422)
