"""SQL Workbench v0.2 测试 —— format / explain / executions(cancel + poll)。"""
from __future__ import annotations

import time

import pytest

from app.dbclients.factory import DbClientError, QueryRows
from app.sqlide.storage import sql_workbench_store


@pytest.fixture(autouse=True)
def _isolate_sql_workbench_store(isolated_storage, monkeypatch):
    monkeypatch.setattr(sql_workbench_store, "path", isolated_storage["cfg"] / "sql_workbench.json")
    sql_workbench_store.invalidate_cache()
    # runtime in-memory map 重置
    from app.sqlide import runtime as _runtime
    _runtime._reset_for_tests()


def _create_ds(client, **overrides) -> str:
    body = {
        "name": "demo", "db_type": "MySQL", "host": "localhost", "port": 3306,
        "database": "demo", "username": "u", "password": "p",
        "environment": "sandbox", "environment_verified": True, "allow_select": True,
    }
    body.update(overrides)
    r = client.post("/api/datasources", json=body)
    assert r.status_code == 200, r.text
    return r.json()["id"]


# ─── /format ────────────────────────────────────────────────────────────────

def test_format_simple_select(client_admin):
    r = client_admin.post("/api/sql-workbench/format", json={
        "sql": "select * from user_info where id=1",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert "SELECT" in body["formatted_sql"]  # sqlglot pretty default uppercase
    assert "FROM user_info" in body["formatted_sql"]
    assert body["dialect"] == "mysql"


def test_format_dialect_dm_maps_to_oracle(client_admin):
    ds_id = _create_ds(client_admin, db_type="DM")
    r = client_admin.post("/api/sql-workbench/format", json={
        "datasource_id": ds_id, "sql": "select sysdate from dual",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["dialect"] == "oracle"  # DM → oracle


def test_format_invalid_sql_returns_error_not_overwrite(client_admin):
    r = client_admin.post("/api/sql-workbench/format", json={
        "sql": ")(*&^%$ not valid sql"
    })
    assert r.status_code == 200
    body = r.json()
    # 失败时 formatted_sql 必须为空,不覆盖原 SQL
    assert body["success"] is False
    assert body["formatted_sql"] == ""
    assert body["error"]


def test_format_empty_sql(client_admin):
    # "   " 长度 3 过 min_length,format_sql 内部 strip 后判空 → success=False
    r = client_admin.post("/api/sql-workbench/format", json={"sql": "   "})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert "为空" in body["error"]


# ─── /explain ───────────────────────────────────────────────────────────────

def test_explain_mysql_success(client_admin, monkeypatch):
    ds_id = _create_ds(client_admin)
    # mock EXPLAIN 返回(MySQL 经典 EXPLAIN 行)
    monkeypatch.setattr(
        "app.sqlide.explain.fetch_rows_with_schema",
        lambda *a, **kw: QueryRows(
            rows=[{"id": 1, "select_type": "SIMPLE", "table": "users", "type": "ALL", "rows": 1000, "Extra": "Using where"}],
            columns=["id", "select_type", "table", "type", "rows", "Extra"],
            raw_columns=["id", "select_type", "table", "type", "rows", "Extra"],
            warnings=[],
        ),
    )
    r = client_admin.post("/api/sql-workbench/explain", json={
        "datasource_id": ds_id, "sql": "SELECT * FROM users",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["dialect"] == "mysql"
    assert "EXPLAIN" in body["explain_sql"]
    assert len(body["rows"]) == 1
    assert body["rows"][0][3] == "ALL"  # type=ALL


def test_explain_oracle_unsupported(client_admin):
    ds_id = _create_ds(client_admin, db_type="Oracle")
    r = client_admin.post("/api/sql-workbench/explain", json={
        "datasource_id": ds_id, "sql": "SELECT 1 FROM dual",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert body["unsupported"] is True
    assert "Oracle" in body["error"] or "DM" in body["error"]


def test_explain_dm_unsupported(client_admin):
    ds_id = _create_ds(client_admin, db_type="DM")
    r = client_admin.post("/api/sql-workbench/explain", json={
        "datasource_id": ds_id, "sql": "SELECT 1",
    })
    body = r.json()
    assert body["success"] is False
    assert body["unsupported"] is True


def test_explain_blocks_dml(client_admin, monkeypatch):
    ds_id = _create_ds(client_admin)
    monkeypatch.setattr(
        "app.sqlide.explain.fetch_rows_with_schema",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("should be blocked")),
    )
    r = client_admin.post("/api/sql-workbench/explain", json={
        "datasource_id": ds_id, "sql": "DELETE FROM users",
    })
    body = r.json()
    assert body["success"] is False
    assert body["unsupported"] is False  # sql_guard 拦截不算 unsupported


def test_explain_driver_error(client_admin, monkeypatch):
    ds_id = _create_ds(client_admin)
    def _fail(*a, **kw): raise DbClientError("syntax")
    monkeypatch.setattr("app.sqlide.explain.fetch_rows_with_schema", _fail)
    r = client_admin.post("/api/sql-workbench/explain", json={
        "datasource_id": ds_id, "sql": "SELECT * FROM xxx",
    })
    body = r.json()
    assert body["success"] is False
    assert "syntax" in (body["error"] or "")


# ─── executions —— execute 返 execution_id + poll + cancel ──────────────────

def test_execute_returns_execution_id_and_status(client_admin, monkeypatch):
    ds_id = _create_ds(client_admin)
    monkeypatch.setattr(
        "app.sqlide.executor.fetch_rows_with_schema",
        lambda *a, **kw: QueryRows(rows=[{"x": 1}], columns=["x"], raw_columns=["x"], warnings=[]),
    )
    r = client_admin.post("/api/sql-workbench/execute", json={
        "datasource_id": ds_id, "sql": "SELECT 1 AS x",
    })
    assert r.status_code == 200
    body = r.json()
    assert "execution_id" in body
    # v0.5:status 闭集 pending/running/success/failed/cancelled
    assert body["status"] in ("success", "running", "pending")
    # 快查询 sync_wait 内完成 —— body 会平铺含 success/columns/rows
    if body["status"] == "success":
        assert body["success"] is True
        assert body["columns"] == ["x"]


def test_get_execution_status_after_done(client_admin, monkeypatch):
    ds_id = _create_ds(client_admin)
    monkeypatch.setattr(
        "app.sqlide.executor.fetch_rows_with_schema",
        lambda *a, **kw: QueryRows(rows=[{"x": 1}], columns=["x"], raw_columns=["x"], warnings=[]),
    )
    r = client_admin.post("/api/sql-workbench/execute", json={
        "datasource_id": ds_id, "sql": "SELECT 1",
    })
    exe_id = r.json()["execution_id"]

    # 给 worker 时间写 result
    time.sleep(0.3)

    r2 = client_admin.get(f"/api/sql-workbench/executions/{exe_id}")
    assert r2.status_code == 200
    body = r2.json()
    assert body["execution_id"] == exe_id
    assert body["status"] in ("success", "running", "pending")
    if body["status"] == "success":
        assert body.get("success") is True


def test_get_execution_owner_only(client_admin, client_editor, monkeypatch):
    ds_id = _create_ds(client_admin)
    monkeypatch.setattr(
        "app.sqlide.executor.fetch_rows_with_schema",
        lambda *a, **kw: QueryRows(rows=[], columns=[], raw_columns=[], warnings=[]),
    )
    r = client_admin.post("/api/sql-workbench/execute", json={
        "datasource_id": ds_id, "sql": "SELECT 1",
    })
    exe_id = r.json()["execution_id"]

    # editor 查 admin 的 → 403
    r2 = client_editor.get(f"/api/sql-workbench/executions/{exe_id}")
    assert r2.status_code == 403


def test_cancel_execution_marks_request(client_admin, monkeypatch):
    """长查询 + cancel —— 由于驱动不支持真中途取消,worker 完成后再 check
    cancel_requested,丢弃结果展示 cancelled。"""
    ds_id = _create_ds(client_admin)
    # 让 worker 慢一点跑完 —— 模拟长查询
    def _slow(*a, **kw):
        time.sleep(0.5)
        return QueryRows(rows=[{"x": 1}], columns=["x"], raw_columns=["x"], warnings=[])
    monkeypatch.setattr("app.sqlide.executor.fetch_rows_with_schema", _slow)
    r = client_admin.post("/api/sql-workbench/execute", json={
        "datasource_id": ds_id, "sql": "SELECT 1",
    })
    body = r.json()
    exe_id = body["execution_id"]
    # sync_wait 内不一定完成 —— 大概率 running
    # cancel
    r2 = client_admin.post(f"/api/sql-workbench/executions/{exe_id}/cancel")
    assert r2.status_code == 200
    assert r2.json()["cancel_requested"] is True

    # 等 worker 跑完(本来 0.5s)
    time.sleep(1.0)
    r3 = client_admin.get(f"/api/sql-workbench/executions/{exe_id}")
    body3 = r3.json()
    # worker 完成时 check cancel → 标 cancelled,result 不展示
    assert body3["status"] == "cancelled"
    assert "result" not in body3 or body3.get("rows") is None or "rows" not in body3


def test_cancel_missing_execution(client_admin):
    r = client_admin.post("/api/sql-workbench/executions/nonexistent/cancel")
    assert r.status_code == 404


def test_cancel_already_done_returns_409(client_admin, monkeypatch):
    ds_id = _create_ds(client_admin)
    monkeypatch.setattr(
        "app.sqlide.executor.fetch_rows_with_schema",
        lambda *a, **kw: QueryRows(rows=[], columns=[], raw_columns=[], warnings=[]),
    )
    r = client_admin.post("/api/sql-workbench/execute", json={
        "datasource_id": ds_id, "sql": "SELECT 1",
    })
    exe_id = r.json()["execution_id"]
    time.sleep(0.3)  # 等 worker 完成

    r2 = client_admin.post(f"/api/sql-workbench/executions/{exe_id}/cancel")
    assert r2.status_code == 409  # 已 done 不能再 cancel


def test_cancel_cross_user_forbidden(client_admin, client_editor, monkeypatch):
    ds_id = _create_ds(client_admin)
    def _slow(*a, **kw):
        time.sleep(0.5)
        return QueryRows(rows=[], columns=[], raw_columns=[], warnings=[])
    monkeypatch.setattr("app.sqlide.executor.fetch_rows_with_schema", _slow)
    r = client_admin.post("/api/sql-workbench/execute", json={
        "datasource_id": ds_id, "sql": "SELECT 1",
    })
    exe_id = r.json()["execution_id"]
    # editor 想 cancel admin 的 → 403
    r2 = client_editor.post(f"/api/sql-workbench/executions/{exe_id}/cancel")
    assert r2.status_code == 403


def test_history_written_after_execution_completes(client_admin, monkeypatch):
    """异步路径下,worker 完成后客户端 GET execution → endpoint trigger 写 history。"""
    ds_id = _create_ds(client_admin)
    monkeypatch.setattr(
        "app.sqlide.executor.fetch_rows_with_schema",
        lambda *a, **kw: QueryRows(rows=[{"x": 42}], columns=["x"], raw_columns=["x"], warnings=[]),
    )
    r = client_admin.post("/api/sql-workbench/execute", json={
        "datasource_id": ds_id, "sql": "SELECT 42",
    })
    exe_id = r.json()["execution_id"]
    time.sleep(0.3)
    client_admin.get(f"/api/sql-workbench/executions/{exe_id}")  # trigger history write
    h = client_admin.get("/api/sql-workbench/history").json()["items"]
    assert any(e["sql"] == "SELECT 42" for e in h)


# ─── v0.5 状态机闭集 + timeout + 长查询 cancel ──────────────────────────


def test_status_enum_v05_closed_set(client_admin, monkeypatch):
    """v0.5 status 闭集只能是 pending / running / success / failed / cancelled。"""
    ds_id = _create_ds(client_admin)
    monkeypatch.setattr(
        "app.sqlide.executor.fetch_rows_with_schema",
        lambda *a, **kw: QueryRows(rows=[{"x": 1}], columns=["x"], raw_columns=["x"], warnings=[]),
    )
    r = client_admin.post("/api/sql-workbench/execute", json={
        "datasource_id": ds_id, "sql": "SELECT 1",
    })
    body = r.json()
    assert body["status"] in ("pending", "running", "success", "failed", "cancelled")
    # done 在 v0.5 不再产生
    assert body["status"] != "done"


def test_long_query_cancel_yields_cancelled_with_reason_user(client_admin, monkeypatch):
    """长查询期间用户主动 cancel → status=cancelled + reason=user。"""
    ds_id = _create_ds(client_admin)

    def _slow(*a, **kw):
        # 真实长查询 —— 但 worker 内部已经 check cancel_requested,所以一旦标
        # cancel,即使 fetch 仍跑完也丢弃结果
        time.sleep(1.0)
        return QueryRows(rows=[], columns=[], raw_columns=[], warnings=[])
    monkeypatch.setattr("app.sqlide.executor.fetch_rows_with_schema", _slow)

    r = client_admin.post("/api/sql-workbench/execute", json={
        "datasource_id": ds_id, "sql": "SELECT 1",
    })
    exe_id = r.json()["execution_id"]
    # 立即 cancel
    time.sleep(0.1)
    rc = client_admin.post(f"/api/sql-workbench/executions/{exe_id}/cancel")
    assert rc.status_code == 200
    # 等 worker 完成
    time.sleep(1.2)
    r2 = client_admin.get(f"/api/sql-workbench/executions/{exe_id}")
    body = r2.json()
    assert body["status"] == "cancelled"
    assert body.get("cancel_reason") == "user"


def test_timeout_seconds_triggers_auto_cancel_with_reason_timeout(client_admin, monkeypatch):
    """timeout_seconds 到时后端自动 cancel + reason=timeout。"""
    ds_id = _create_ds(client_admin)

    def _slow(*a, **kw):
        time.sleep(2.0)  # 故意比 timeout 长
        return QueryRows(rows=[], columns=[], raw_columns=[], warnings=[])
    monkeypatch.setattr("app.sqlide.executor.fetch_rows_with_schema", _slow)

    r = client_admin.post("/api/sql-workbench/execute", json={
        "datasource_id": ds_id, "sql": "SELECT 1",
        "timeout_seconds": 1,   # 1 秒超时
    })
    assert r.status_code == 200
    exe_id = r.json()["execution_id"]
    # 等超时触发 + worker 完成
    time.sleep(2.5)
    r2 = client_admin.get(f"/api/sql-workbench/executions/{exe_id}")
    body = r2.json()
    assert body["status"] == "cancelled", body
    assert body.get("cancel_reason") == "timeout"
    assert body.get("timeout_seconds") == 1


def test_timeout_seconds_clamped_to_max_3600(client_admin, monkeypatch):
    """timeout_seconds 超过 3600 时返 422(Pydantic 验证)。"""
    ds_id = _create_ds(client_admin)
    r = client_admin.post("/api/sql-workbench/execute", json={
        "datasource_id": ds_id, "sql": "SELECT 1",
        "timeout_seconds": 9999,
    })
    assert r.status_code == 422


def test_timeout_seconds_must_be_positive(client_admin, monkeypatch):
    """timeout_seconds=0 / 负数返 422。"""
    ds_id = _create_ds(client_admin)
    r = client_admin.post("/api/sql-workbench/execute", json={
        "datasource_id": ds_id, "sql": "SELECT 1",
        "timeout_seconds": 0,
    })
    assert r.status_code == 422


def test_history_records_cancel_reason(client_admin, monkeypatch):
    """cancelled 走 history,error 字段含 reason(user / timeout)。"""
    ds_id = _create_ds(client_admin)

    def _slow(*a, **kw):
        time.sleep(2.0)
        return QueryRows(rows=[], columns=[], raw_columns=[], warnings=[])
    monkeypatch.setattr("app.sqlide.executor.fetch_rows_with_schema", _slow)

    # 1 秒 timeout → 自动 cancel
    r = client_admin.post("/api/sql-workbench/execute", json={
        "datasource_id": ds_id, "sql": "SELECT 'timeout_test'",
        "timeout_seconds": 1,
    })
    exe_id = r.json()["execution_id"]
    time.sleep(2.5)
    client_admin.get(f"/api/sql-workbench/executions/{exe_id}")  # trigger history write

    h = client_admin.get("/api/sql-workbench/history").json()["items"]
    matched = [e for e in h if "timeout_test" in e["sql"]]
    assert matched, "history missing cancelled entry"
    assert "cancelled" in (matched[0].get("error") or "")
    assert "timeout" in (matched[0].get("error") or "")
