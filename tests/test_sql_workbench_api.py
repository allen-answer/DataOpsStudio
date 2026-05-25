"""SQL Workbench API 集成测试 —— 走 FastAPI TestClient,验证:
- 401 / 403 鉴权
- consoles CRUD
- execute 走 sql_guard
- history 落盘 + 自动归 user
- metadata stub Phase 1 返回空 + phase 标记
"""
from __future__ import annotations

import pytest

from app.sqlide.storage import sql_workbench_store


@pytest.fixture(autouse=True)
def _isolate_sql_workbench_store(isolated_storage, monkeypatch):
    """conftest.isolated_storage 没 patch 我们的 store —— 单独 redirect。"""
    monkeypatch.setattr(sql_workbench_store, "path", isolated_storage["cfg"] / "sql_workbench.json")
    sql_workbench_store.invalidate_cache()


def _create_mysql_ds(client) -> str:
    """建一个 demo MySQL ds 给后续测试用。返 id。"""
    r = client.post("/api/datasources", json={
        "name": "demo",
        "db_type": "MySQL",
        "host": "localhost",
        "port": 3306,
        "database": "demo",
        "username": "u",
        "password": "p",
        "environment": "sandbox",
        "environment_verified": True,
        "allow_select": True,
    })
    assert r.status_code == 200, r.text
    return r.json()["id"]


# ─── 鉴权 ──────────────────────────────────────────────────────────────────

def test_anon_blocked(client_anon):
    assert client_anon.get("/api/sql-workbench/consoles").status_code == 401


def test_viewer_blocked(client_viewer):
    # editor 门槛 → viewer 403
    assert client_viewer.get("/api/sql-workbench/consoles").status_code == 403


# ─── consoles CRUD ─────────────────────────────────────────────────────────

def test_list_consoles_empty(client_editor):
    r = client_editor.get("/api/sql-workbench/consoles")
    assert r.status_code == 200
    assert r.json() == {"items": []}


def test_create_console_minimal(client_editor):
    r = client_editor.post("/api/sql-workbench/consoles", json={"name": "tab-1"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "tab-1"
    assert body["id"]
    assert body["owner_user_id"]  # 自动归当前用户


def test_create_console_with_unauthorized_datasource(client_editor, client_admin):
    """editor 试图把 console 绑到不在自己项目的 ds → 403"""
    # admin 创建一个 ds 在另一项目 X
    r = client_admin.post("/api/projects", json={"id": "secret", "name": "secret"})
    assert r.status_code == 200, r.text
    r = client_admin.post("/api/datasources", json={
        "name": "boss", "db_type": "MySQL", "host": "x", "port": 1, "database": "x",
        "username": "u", "password": "p", "project_id": "secret",
    })
    assert r.status_code == 200, r.text
    ds_id = r.json()["id"]
    # editor 默认无 project 关联,所以拿 secret 项目的 ds → 403
    r = client_editor.post("/api/sql-workbench/consoles", json={"name": "x", "datasource_id": ds_id})
    assert r.status_code == 403


def test_update_console(client_editor):
    cid = client_editor.post("/api/sql-workbench/consoles", json={"name": "t"}).json()["id"]
    r = client_editor.put(f"/api/sql-workbench/consoles/{cid}", json={"sql": "SELECT 1"})
    assert r.status_code == 200
    assert r.json()["sql"] == "SELECT 1"


def test_delete_console(client_editor):
    cid = client_editor.post("/api/sql-workbench/consoles", json={"name": "t"}).json()["id"]
    assert client_editor.delete(f"/api/sql-workbench/consoles/{cid}").status_code == 200
    # 二次删 → 404
    assert client_editor.delete(f"/api/sql-workbench/consoles/{cid}").status_code == 404


def test_console_owner_isolation(client_editor, client_admin):
    """admin 跟 editor 各自看自己的 console,不互相串。"""
    client_editor.post("/api/sql-workbench/consoles", json={"name": "editor-tab"})
    client_admin.post("/api/sql-workbench/consoles", json={"name": "admin-tab"})
    editor_items = client_editor.get("/api/sql-workbench/consoles").json()["items"]
    admin_items = client_admin.get("/api/sql-workbench/consoles").json()["items"]
    assert [c["name"] for c in editor_items] == ["editor-tab"]
    assert [c["name"] for c in admin_items] == ["admin-tab"]


# ─── execute ───────────────────────────────────────────────────────────────

def test_execute_blocks_dml(client_admin, monkeypatch):
    ds_id = _create_mysql_ds(client_admin)
    # fetch_rows 不应被调到
    monkeypatch.setattr(
        "app.sqlide.executor.fetch_rows_with_schema",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("should be blocked")),
    )
    r = client_admin.post("/api/sql-workbench/execute", json={
        "datasource_id": ds_id, "sql": "UPDATE users SET x=1"
    })
    assert r.status_code == 200  # success=False envelope
    body = r.json()
    assert body["success"] is False
    assert "Forbidden" in body["error"] or "SELECT" in body["error"]


def test_execute_success_records_history(client_admin, monkeypatch):
    ds_id = _create_mysql_ds(client_admin)
    from app.dbclients.factory import QueryRows
    monkeypatch.setattr(
        "app.sqlide.executor.fetch_rows_with_schema",
        lambda *a, **kw: QueryRows(rows=[{"x": 1}], columns=["x"], raw_columns=["x"], warnings=[]),
    )
    r = client_admin.post("/api/sql-workbench/execute", json={
        "datasource_id": ds_id, "sql": "SELECT 1 AS x"
    })
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["columns"] == ["x"]
    assert body["rows"] == [[1]]
    assert body["row_count"] == 1
    assert body["truncated"] is False

    # history 落了
    h = client_admin.get("/api/sql-workbench/history").json()["items"]
    assert len(h) == 1
    assert h[0]["sql"] == "SELECT 1 AS x"
    assert h[0]["success"] is True


def test_execute_records_history_on_failure(client_admin, monkeypatch):
    ds_id = _create_mysql_ds(client_admin)
    from app.dbclients.factory import DbClientError
    def _fail(*a, **kw):
        raise DbClientError("table not found")
    monkeypatch.setattr("app.sqlide.executor.fetch_rows_with_schema", _fail)
    r = client_admin.post("/api/sql-workbench/execute", json={
        "datasource_id": ds_id, "sql": "SELECT * FROM missing"
    })
    body = r.json()
    assert body["success"] is False
    assert "table not found" in body["error"]
    # 失败也落 history
    h = client_admin.get("/api/sql-workbench/history").json()["items"]
    assert len(h) == 1
    assert h[0]["success"] is False


def test_execute_blocks_when_allow_select_false(client_admin, monkeypatch):
    # 建 ds 但 allow_select=False
    r = client_admin.post("/api/datasources", json={
        "name": "locked", "db_type": "MySQL", "host": "x", "port": 3306, "database": "x",
        "username": "u", "password": "p", "allow_select": False,
        "environment": "prod", "environment_verified": True,
    })
    ds_id = r.json()["id"]
    monkeypatch.setattr(
        "app.sqlide.executor.fetch_rows_with_schema",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("should not reach")),
    )
    r = client_admin.post("/api/sql-workbench/execute", json={
        "datasource_id": ds_id, "sql": "SELECT 1"
    })
    assert r.status_code == 403
    assert "allow_select" in r.json().get("detail", "")


# ─── history ───────────────────────────────────────────────────────────────

def test_history_filter_by_datasource(client_admin, monkeypatch):
    from app.dbclients.factory import QueryRows
    monkeypatch.setattr(
        "app.sqlide.executor.fetch_rows_with_schema",
        lambda *a, **kw: QueryRows(rows=[], columns=[], raw_columns=[], warnings=[]),
    )
    ds_a = _create_mysql_ds(client_admin)
    r = client_admin.post("/api/datasources", json={
        "name": "demo2", "db_type": "MySQL", "host": "y", "port": 3306, "database": "y",
        "username": "u", "password": "p", "allow_select": True,
        "environment": "sandbox", "environment_verified": True,
    })
    ds_b = r.json()["id"]
    client_admin.post("/api/sql-workbench/execute", json={"datasource_id": ds_a, "sql": "SELECT 1"})
    client_admin.post("/api/sql-workbench/execute", json={"datasource_id": ds_b, "sql": "SELECT 2"})

    filtered = client_admin.get(f"/api/sql-workbench/history?datasource_id={ds_b}").json()["items"]
    assert len(filtered) == 1
    assert filtered[0]["datasource_id"] == ds_b
    assert filtered[0]["sql"] == "SELECT 2"


# ─── metadata stubs (Phase 3 真实化) ───────────────────────────────────────

def test_metadata_schemas_returns_envelope(client_admin, monkeypatch):
    """Phase 3:metadata 真接 introspect。fetch_rows 不真打 DB,mock 一下。"""
    ds_id = _create_mysql_ds(client_admin)
    monkeypatch.setattr(
        "app.dbclients.factory.fetch_rows",
        lambda *a, **kw: [{"name": "myapp"}, {"name": "information_schema"}],
    )
    r = client_admin.get(f"/api/sql-workbench/metadata/schemas?datasource_id={ds_id}")
    assert r.status_code == 200
    body = r.json()
    # information_schema 系统库被过滤
    assert body["items"] == [{"name": "myapp"}]


def test_metadata_tables_filters_by_schema(client_admin, monkeypatch):
    ds_id = _create_mysql_ds(client_admin)
    monkeypatch.setattr(
        "app.dbclients.factory.fetch_rows",
        lambda *a, **kw: [{"name": "users"}, {"name": "orders"}],
    )
    r = client_admin.get(f"/api/sql-workbench/metadata/tables?datasource_id={ds_id}&schema=myapp")
    assert r.status_code == 200
    body = r.json()
    assert [t["name"] for t in body["items"]] == ["users", "orders"]


def test_metadata_unsupported_dbtype_returns_empty(client_admin, monkeypatch):
    """introspect 不支持的 db_type 返空 items 而非 500。"""
    r = client_admin.post("/api/datasources", json={
        "name": "pgx", "db_type": "MySQL", "host": "x", "port": 1, "database": "x",
        "username": "u", "password": "p", "allow_select": True,
        "environment": "sandbox", "environment_verified": True,
    })
    ds_id = r.json()["id"]
    # mock fetch_rows 抛错(模拟驱动错)→ endpoint 应该捕获返 error 字段
    def _fail(*a, **kw):
        raise RuntimeError("driver not installed")
    monkeypatch.setattr("app.dbclients.factory.fetch_rows", _fail)
    r = client_admin.get(f"/api/sql-workbench/metadata/schemas?datasource_id={ds_id}")
    assert r.status_code == 200
    assert r.json()["items"] == []
    assert "driver not installed" in r.json().get("error", "")
