"""项目级授权测试 —— 直接 datasource_id 接口的越权防护。

凡是直接接收 datasource_id 执行 SQL / EXPLAIN / 预览 / introspect 的
endpoint 都必须走 `require_datasource_access`：

- editor / viewer 只能访问 owned/member 项目下的 datasource，或者
  全局 datasource（project_id 为空）
- admin 全权

本测试集覆盖：
- /api/preview/rows  (uploads.py, SQL kind)
- /api/preview/columns (uploads.py, SQL kind)
- /api/tasks/{id}/preview override_datasource_id 路径 (tasks.py)
- /api/assets/introspect/{name} (assets.py)
- 跨多角色：global datasource / ProjectA datasource / ProjectB datasource
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.services import auth as auth_svc


@pytest.fixture
def client(isolated_storage):
    """bootstrap admin / alice(editor) / bob(editor) 三人 + ProjectA(alice owner) +
    三种 datasource（global / ProjectA / ProjectB）。"""
    auth_svc.bootstrap_default_admin()

    from app.utils.paths import USERS_FILE
    raw = json.loads(USERS_FILE.read_text(encoding="utf-8"))
    for username, role in [("alice", "editor"), ("bob", "editor")]:
        raw.append({
            "id": uuid.uuid4().hex,
            "username": username,
            "password_hash": auth_svc.hash_password(f"{username}123"),
            "role": role,
            "display_name": username.title(),
            "created_at": datetime.now().isoformat(timespec="seconds"),
        })
    USERS_FILE.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    auth_svc.user_store.invalidate_cache()

    from main import app
    return TestClient(app)


def _login(client, username, password):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def ctx(client):
    """建好两个 project + 三个 datasource，返回 dict 给测试用。"""
    admin = _login(client, "admin", "admin")
    alice = _login(client, "alice", "alice123")
    bob = _login(client, "bob", "bob123")

    # alice 建 ProjectA（owner=alice，member 只有自己）
    pa = client.post("/api/projects", headers=_auth(alice), json={
        "name": "ProjectA", "members": [],
    }).json()
    # bob 建 ProjectB
    pb = client.post("/api/projects", headers=_auth(bob), json={
        "name": "ProjectB", "members": [],
    }).json()

    # 三个 datasource
    ds_global = client.post("/api/datasources", headers=_auth(admin), json={
        "name": "DS-Global", "db_type": "MySQL", "host": "h0", "port": 3306,
    }).json()
    ds_a = client.post("/api/datasources", headers=_auth(admin), json={
        "name": "DS-A", "db_type": "MySQL", "host": "h1", "port": 3306,
        "project_id": pa["id"],
    }).json()
    ds_b = client.post("/api/datasources", headers=_auth(admin), json={
        "name": "DS-B", "db_type": "MySQL", "host": "h2", "port": 3306,
        "project_id": pb["id"],
    }).json()

    return {
        "admin": admin,
        "alice": alice,
        "bob": bob,
        "project_a": pa,
        "project_b": pb,
        "ds_global": ds_global,
        "ds_a": ds_a,
        "ds_b": ds_b,
    }


def _stub_fetchers(monkeypatch):
    """所有命中 db 的调用 monkeypatch 成空结果，避免依赖真实连接。"""
    from app.api import uploads as uploads_api
    monkeypatch.setattr(uploads_api, "fetch_rows", lambda *a, **k: [])

    class _Details:
        columns = []
        warnings = []

    monkeypatch.setattr(uploads_api, "fetch_column_details", lambda *a, **k: _Details())

    from app.api import tasks as tasks_api

    class _RowsResult:
        columns = []
        rows = []
        warnings = []

    monkeypatch.setattr(tasks_api, "fetch_rows_with_schema", lambda *a, **k: _RowsResult())


# ─── /api/preview/rows ───────────────────────────────────────────────────────


def test_alice_cannot_preview_rows_for_project_b_datasource(client, ctx, monkeypatch):
    _stub_fetchers(monkeypatch)
    r = client.post(
        "/api/preview/rows",
        headers=_auth(ctx["alice"]),
        json={
            "kind": "sql",
            "datasource_id": ctx["ds_b"]["id"],
            "sql": "SELECT 1",
        },
    )
    assert r.status_code == 403, r.text


def test_alice_can_preview_rows_for_project_a_datasource(client, ctx, monkeypatch):
    _stub_fetchers(monkeypatch)
    r = client.post(
        "/api/preview/rows",
        headers=_auth(ctx["alice"]),
        json={
            "kind": "sql",
            "datasource_id": ctx["ds_a"]["id"],
            "sql": "SELECT 1",
        },
    )
    assert r.status_code == 200, r.text


def test_alice_can_preview_rows_for_global_datasource(client, ctx, monkeypatch):
    _stub_fetchers(monkeypatch)
    r = client.post(
        "/api/preview/rows",
        headers=_auth(ctx["alice"]),
        json={
            "kind": "sql",
            "datasource_id": ctx["ds_global"]["id"],
            "sql": "SELECT 1",
        },
    )
    assert r.status_code == 200, r.text


def test_admin_can_preview_rows_for_any_datasource(client, ctx, monkeypatch):
    _stub_fetchers(monkeypatch)
    for ds_id in (ctx["ds_global"]["id"], ctx["ds_a"]["id"], ctx["ds_b"]["id"]):
        r = client.post(
            "/api/preview/rows",
            headers=_auth(ctx["admin"]),
            json={
                "kind": "sql",
                "datasource_id": ds_id,
                "sql": "SELECT 1",
            },
        )
        assert r.status_code == 200, f"{ds_id}: {r.text}"


def test_preview_rows_unknown_datasource_404(client, ctx, monkeypatch):
    _stub_fetchers(monkeypatch)
    r = client.post(
        "/api/preview/rows",
        headers=_auth(ctx["admin"]),
        json={"kind": "sql", "datasource_id": "ghost", "sql": "SELECT 1"},
    )
    assert r.status_code == 404, r.text


# ─── /api/preview/columns ────────────────────────────────────────────────────


def test_alice_cannot_preview_columns_for_project_b_datasource(client, ctx, monkeypatch):
    _stub_fetchers(monkeypatch)
    r = client.post(
        "/api/preview/columns",
        headers=_auth(ctx["alice"]),
        json={"kind": "sql", "datasource_id": ctx["ds_b"]["id"], "sql": "SELECT 1"},
    )
    assert r.status_code == 403, r.text


def test_alice_can_preview_columns_for_project_a_datasource(client, ctx, monkeypatch):
    _stub_fetchers(monkeypatch)
    r = client.post(
        "/api/preview/columns",
        headers=_auth(ctx["alice"]),
        json={"kind": "sql", "datasource_id": ctx["ds_a"]["id"], "sql": "SELECT 1"},
    )
    assert r.status_code == 200, r.text


# ─── /api/tasks/{id}/preview (override_datasource_id) ────────────────────────


def test_alice_cannot_use_task_preview_override_to_project_b_datasource(
    client, ctx, monkeypatch,
):
    """task 自己属于 ProjectA，alice 是 owner —— 但她仍然不能把
    override_datasource_id 指向 ProjectB 的 datasource。"""
    _stub_fetchers(monkeypatch)

    task = client.post(
        "/api/tasks",
        headers=_auth(ctx["alice"]),
        json={
            "name": "t-override",
            "source_id": ctx["ds_a"]["id"],
            "target_id": ctx["ds_a"]["id"],
            "sql_mode": "single",
            "source_sql": "SELECT 1 AS id",
            "key_columns": ["id"],
            "project_id": ctx["project_a"]["id"],
        },
    ).json()

    r = client.post(
        f"/api/tasks/{task['id']}/preview",
        headers=_auth(ctx["alice"]),
        json={
            "side": "source",
            "datasource_id": ctx["ds_b"]["id"],
            "sql": "SELECT 1",
        },
    )
    assert r.status_code == 403, r.text


def test_alice_can_use_task_preview_without_override(client, ctx, monkeypatch):
    """非 override 路径下，alice 用自己 ProjectA 的 datasource 仍能正常预览。"""
    _stub_fetchers(monkeypatch)

    task = client.post(
        "/api/tasks",
        headers=_auth(ctx["alice"]),
        json={
            "name": "t-self",
            "source_id": ctx["ds_a"]["id"],
            "target_id": ctx["ds_a"]["id"],
            "sql_mode": "single",
            "source_sql": "SELECT 1 AS id",
            "key_columns": ["id"],
            "project_id": ctx["project_a"]["id"],
        },
    ).json()

    r = client.post(
        f"/api/tasks/{task['id']}/preview",
        headers=_auth(ctx["alice"]),
        json={"side": "source"},
    )
    assert r.status_code == 200, r.text


# ─── /api/assets/introspect/{name} ───────────────────────────────────────────


def test_alice_cannot_introspect_project_b_datasource(client, ctx, monkeypatch):
    from app.services import datasource_introspect as di
    monkeypatch.setattr(di, "introspect_columns", lambda *a, **k: [])

    r = client.get(
        f"/api/assets/introspect/ods.t1?datasource_id={ctx['ds_b']['id']}",
        headers=_auth(ctx["alice"]),
    )
    assert r.status_code == 403, r.text


def test_alice_can_introspect_project_a_datasource(client, ctx, monkeypatch):
    from app.services import datasource_introspect as di
    monkeypatch.setattr(di, "introspect_columns", lambda *a, **k: [])

    r = client.get(
        f"/api/assets/introspect/ods.t1?datasource_id={ctx['ds_a']['id']}",
        headers=_auth(ctx["alice"]),
    )
    assert r.status_code == 200, r.text


def test_admin_can_introspect_any_datasource(client, ctx, monkeypatch):
    from app.services import datasource_introspect as di
    monkeypatch.setattr(di, "introspect_columns", lambda *a, **k: [])

    for ds_id in (ctx["ds_global"]["id"], ctx["ds_a"]["id"], ctx["ds_b"]["id"]):
        r = client.get(
            f"/api/assets/introspect/ods.t1?datasource_id={ds_id}",
            headers=_auth(ctx["admin"]),
        )
        assert r.status_code == 200, f"{ds_id}: {r.text}"
