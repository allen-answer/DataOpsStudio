"""Phase 14 P1-2: plan_history + diff_plans 测试。"""
from __future__ import annotations

from app.services import plan_history


# ─── normalize / hash ───────────────────────────────────────────────────────


def test_sql_hash_collapses_whitespace():
    """格式变化(空白 / 缩进 / 换行)→ 同 hash"""
    a = plan_history.sql_hash("SELECT id FROM t WHERE id > 0")
    b = plan_history.sql_hash("SELECT  id\n  FROM   t\n  WHERE id > 0;")
    assert a == b


def test_sql_hash_preserves_case_sensitivity():
    """大小写不同 → 不同 hash(可能是有意义的 column 名)"""
    a = plan_history.sql_hash("select * from T")
    b = plan_history.sql_hash("select * from t")
    assert a != b


def test_sql_hash_semantic_change_different():
    """改 WHERE 条件 → 不同 hash"""
    a = plan_history.sql_hash("SELECT id FROM t WHERE id > 0")
    b = plan_history.sql_hash("SELECT id FROM t WHERE id > 100")
    assert a != b


# ─── save / list / get ─────────────────────────────────────────────────────


def test_save_and_list_basic(isolated_storage):
    plan = [{"id": 1, "type": "ALL", "rows": 1_000_000, "Extra": "Using filesort"}]
    issues = [{"code": "full_table_scan", "severity": "high"}]
    suggestions = [{"action": "add_index", "columns": ["id"]}]
    pid = plan_history.save_plan(
        datasource_id="ds-1", dialect="mysql",
        sql_text="SELECT * FROM t", plan=plan,
        issues=issues, suggestions=suggestions,
    )
    assert pid > 0
    items = plan_history.list_plans_for_sql(
        "ds-1", plan_history.sql_hash("SELECT * FROM t"),
    )
    assert len(items) == 1
    assert items[0]["id"] == pid
    assert items[0]["plan"] == plan
    assert items[0]["issues"] == issues


def test_list_plans_for_sql_orders_by_ts_desc(isolated_storage):
    """最新的在前"""
    import time
    pid1 = plan_history.save_plan(
        datasource_id="ds", dialect="mysql", sql_text="SELECT 1",
        plan=[], issues=[], suggestions=[],
    )
    time.sleep(1.1)  # ts iso second precision,sleep > 1s
    pid2 = plan_history.save_plan(
        datasource_id="ds", dialect="mysql", sql_text="SELECT 1",
        plan=[], issues=[], suggestions=[],
    )
    items = plan_history.list_plans_for_sql("ds", plan_history.sql_hash("SELECT 1"))
    assert [it["id"] for it in items] == [pid2, pid1]


def test_list_plans_for_scenario(isolated_storage):
    """scenario_id + workload_name 维度查询"""
    plan_history.save_plan(
        datasource_id="ds", dialect="mysql", sql_text="SELECT 1",
        plan=[], issues=[], suggestions=[],
        scenario_id="scen-a", workload_name="slow-query-1",
    )
    plan_history.save_plan(
        datasource_id="ds", dialect="mysql", sql_text="SELECT 2",
        plan=[], issues=[], suggestions=[],
        scenario_id="scen-a", workload_name="slow-query-2",
    )
    # 同 scenario 不同 workload → 2 条
    assert len(plan_history.list_plans_for_scenario("scen-a")) == 2
    # 限定 workload → 1 条
    assert len(plan_history.list_plans_for_scenario("scen-a", "slow-query-1")) == 1


# ─── diff_plans ────────────────────────────────────────────────────────────


def test_diff_plans_max_rows_improvement():
    """老 1M rows / 新 100 rows → diff 标记改善"""
    a = {"dialect": "mysql", "plan": [{"rows": 1_000_000, "type": "ALL"}], "issues": []}
    b = {"dialect": "mysql", "plan": [{"rows": 100, "type": "range"}], "issues": []}
    d = plan_history.diff_plans(a, b)
    assert d["rows_delta"]["a"] == 1_000_000
    assert d["rows_delta"]["b"] == 100
    assert d["rows_delta"]["change"] == -999_900
    assert "改善" in d["summary"]


def test_diff_plans_type_change_detected():
    a = {"dialect": "mysql", "plan": [{"type": "ALL"}], "issues": []}
    b = {"dialect": "mysql", "plan": [{"type": "range"}], "issues": []}
    d = plan_history.diff_plans(a, b)
    assert len(d["type_changes"]) == 1
    assert d["type_changes"][0] == {"idx": 0, "from": "ALL", "to": "range"}


def test_diff_plans_extra_changes_detected():
    """Extra 列拆 token,告诉 caller 哪些消了 / 哪些新增了"""
    a = {"dialect": "mysql", "plan": [{"Extra": "Using where; Using filesort"}], "issues": []}
    b = {"dialect": "mysql", "plan": [{"Extra": "Using where; Using index"}], "issues": []}
    d = plan_history.diff_plans(a, b)
    assert len(d["extra_changes"]) == 1
    assert "Using filesort" in d["extra_changes"][0]["removed"]
    assert "Using index" in d["extra_changes"][0]["added"]


def test_diff_plans_issues_resolved_and_introduced():
    a = {"dialect": "mysql", "plan": [],
         "issues": [{"code": "full_table_scan"}, {"code": "filesort"}]}
    b = {"dialect": "mysql", "plan": [],
         "issues": [{"code": "high_row_scan"}]}  # full_table_scan / filesort 修了,引入新的
    d = plan_history.diff_plans(a, b)
    assert set(d["issues_resolved"]) == {"full_table_scan", "filesort"}
    assert d["issues_introduced"] == ["high_row_scan"]


def test_diff_plans_oracle_uses_cardinality_and_operation():
    """Oracle plan 字段不同:operation 替代 type,cardinality 替代 rows"""
    a = {"dialect": "oracle", "plan": [
        {"operation": "TABLE ACCESS", "cardinality": 1_000_000},
    ], "issues": []}
    b = {"dialect": "oracle", "plan": [
        {"operation": "INDEX RANGE SCAN", "cardinality": 100},
    ], "issues": []}
    d = plan_history.diff_plans(a, b)
    assert d["rows_delta"]["a"] == 1_000_000
    assert d["type_changes"][0]["from"] == "TABLE ACCESS"
    assert d["type_changes"][0]["to"] == "INDEX RANGE SCAN"


def test_diff_plans_no_change():
    a = {"dialect": "mysql", "plan": [{"type": "range", "rows": 100}], "issues": []}
    b = {"dialect": "mysql", "plan": [{"type": "range", "rows": 100}], "issues": []}
    d = plan_history.diff_plans(a, b)
    assert "无实质变化" in d["summary"]
    assert d["type_changes"] == []
    assert d["extra_changes"] == []


def test_diff_plans_handles_none_safely():
    """plan_a / plan_b 为 None 不该崩"""
    d = plan_history.diff_plans(None, None)
    assert d["rows_delta"]["a"] == 0
    assert d["rows_delta"]["b"] == 0


# ─── endpoint integration ─────────────────────────────────────────────────


def test_plan_history_endpoint_requires_datasource_and_hash(client, isolated_storage):
    """Phase 14 #3:plan-history 现要求同时给 datasource_id + sql_hash。
    scenario_id-only 模式已禁用以防跨项目泄露。"""
    r = client.get("/api/slow-sql/plan-history")
    assert r.status_code == 400


def test_plan_history_scenario_id_only_rejected(client, isolated_storage):
    """Phase 14 #3:scenario_id-only 查询禁用 → 400 + 解释文案"""
    r = client.get("/api/slow-sql/plan-history?scenario_id=orders-recon")
    assert r.status_code == 400
    body = r.json()
    detail = str(body)
    assert "datasource_id" in detail
    assert "sql_hash" in detail


def test_plan_history_endpoint_uses_datasource_and_hash(client, isolated_storage):
    """正常路径:datasource_id + sql_hash → 200"""
    from app.models import DataSourceCreate, DatabaseType
    from app.models.datasource import make_sandbox_datasource_kwargs
    from app.services.repositories import datasource_store

    ds = datasource_store.create(DataSourceCreate(
        name="ds", db_type=DatabaseType.MYSQL, host="x", port=3306,
        database="d", username="u", password="p",
        **make_sandbox_datasource_kwargs(),
    ))
    pid = plan_history.save_plan(
        datasource_id=ds.id, dialect="mysql", sql_text="SELECT 1",
        plan=[], issues=[], suggestions=[],
    )
    assert pid is not None
    sql_hash = plan_history.sql_hash("SELECT 1")
    r = client.get(
        f"/api/slow-sql/plan-history?datasource_id={ds.id}&sql_hash={sql_hash}"
    )
    assert r.status_code == 200, r.text
    assert len(r.json()["items"]) >= 1


def test_verify_endpoint_rejects_cross_project(client_editor, isolated_storage):
    """Phase 14 #3:editor 传别人项目 id 走 verify 应被 403,在 _load_or_404 之前先校 project_id"""
    # editor 默认不挂任何 project(conftest._bootstrap_users 建 editor 不入项目),
    # 传一个随便的 project_id 应该被 can_access_project 拒
    r = client_editor.get("/api/scenarios/orders-recon-mvp/verify?project_id=other-proj")
    assert r.status_code == 403, r.text
    assert "无权" in str(r.json()) or "403" in str(r.json())


def test_plan_diff_endpoint_404_when_missing(client, isolated_storage):
    r = client.get("/api/slow-sql/plan-diff?plan_a_id=9999&plan_b_id=9998")
    assert r.status_code == 404


def test_plan_diff_endpoint_returns_diff(client, isolated_storage):
    """端到端:存两条 + 调 diff endpoint → 拿到 diff 结果"""
    from app.models import DataSourceCreate, DatabaseType
    from app.services.repositories import datasource_store

    ds = datasource_store.create(DataSourceCreate(
        name="ds", db_type=DatabaseType.MYSQL, host="x", port=3306,
        database="d", username="u", password="p",
    ))
    pid_a = plan_history.save_plan(
        datasource_id=ds.id, dialect="mysql", sql_text="SELECT 1",
        plan=[{"type": "ALL", "rows": 1000}],
        issues=[{"code": "full_table_scan"}], suggestions=[],
    )
    pid_b = plan_history.save_plan(
        datasource_id=ds.id, dialect="mysql", sql_text="SELECT 1",
        plan=[{"type": "range", "rows": 10}],
        issues=[], suggestions=[],
    )
    r = client.get(f"/api/slow-sql/plan-diff?plan_a_id={pid_a}&plan_b_id={pid_b}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["diff"]["rows_delta"]["change"] < 0  # 改善
    assert "full_table_scan" in body["diff"]["issues_resolved"]
