"""认证 / 用户 / 项目 / 审计日志 测试。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.services import auth as auth_svc


@pytest.fixture
def client(isolated_storage, monkeypatch):
    """每个 case 隔离的 TestClient。bootstrap 一个 admin / 一个 editor / 一个 viewer。"""
    # users.json 已被 isolated_storage patch 到 tmp，直接调 bootstrap 重新建
    auth_svc.bootstrap_default_admin()

    # 建测试用 editor / viewer
    import uuid
    from datetime import datetime
    import json
    from app.utils.paths import USERS_FILE
    raw = json.loads(USERS_FILE.read_text(encoding="utf-8"))
    raw.append({
        "id": uuid.uuid4().hex,
        "username": "alice",
        "password_hash": auth_svc.hash_password("alice123"),
        "role": "editor",
        "display_name": "Alice",
        "created_at": datetime.now().isoformat(timespec="seconds"),
    })
    raw.append({
        "id": uuid.uuid4().hex,
        "username": "bob",
        "password_hash": auth_svc.hash_password("bob123"),
        "role": "viewer",
        "display_name": "Bob",
        "created_at": datetime.now().isoformat(timespec="seconds"),
    })
    USERS_FILE.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    auth_svc.user_store.invalidate_cache()

    from main import app
    return TestClient(app)


def _login(client, username, password):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


# ─── login ────────────────────────────────────────────────────────────────────


def test_login_admin_returns_token_and_user(client):
    data = _login(client, "admin", "admin")
    assert data["token_type"] == "bearer"
    assert data["access_token"]
    assert data["user"]["username"] == "admin"
    assert data["user"]["role"] == "admin"
    # password_hash 必须脱敏
    assert data["user"].get("password_hash", "") == ""


def test_login_wrong_password_401(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "WRONG"})
    assert r.status_code == 401


def test_login_unknown_user_401(client):
    r = client.post("/api/auth/login", json={"username": "ghost", "password": "x"})
    assert r.status_code == 401


# ─── /me ──────────────────────────────────────────────────────────────────────


def test_me_returns_current_user(client):
    token = _login(client, "alice", "alice123")["access_token"]
    r = client.get("/api/auth/me", headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["username"] == "alice"


def test_me_without_token_401(client):
    r = client.get("/api/auth/me")
    assert r.status_code == 401


def test_me_invalid_token_401(client):
    r = client.get("/api/auth/me", headers=_auth("garbage"))
    assert r.status_code == 401


# ─── User CRUD（admin only） ──────────────────────────────────────────────────


def test_list_users_requires_admin(client):
    editor_token = _login(client, "alice", "alice123")["access_token"]
    r = client.get("/api/users", headers=_auth(editor_token))
    assert r.status_code == 403


def test_list_users_admin_ok(client):
    admin_token = _login(client, "admin", "admin")["access_token"]
    r = client.get("/api/users", headers=_auth(admin_token))
    assert r.status_code == 200
    usernames = {u["username"] for u in r.json()}
    assert {"admin", "alice", "bob"}.issubset(usernames)


def test_create_user_admin_only(client):
    admin_token = _login(client, "admin", "admin")["access_token"]
    r = client.post("/api/users", headers=_auth(admin_token), json={
        "username": "charlie", "password": "char1234", "role": "editor",
    })
    assert r.status_code == 200
    # 新用户能登录
    _login(client, "charlie", "char1234")


def test_user_can_change_own_password(client):
    alice_token = _login(client, "alice", "alice123")["access_token"]
    me = client.get("/api/auth/me", headers=_auth(alice_token)).json()
    r = client.put(f"/api/users/{me['id']}", headers=_auth(alice_token), json={
        "password": "newpass1",
    })
    assert r.status_code == 200
    _login(client, "alice", "newpass1")


def test_non_admin_cannot_change_role(client):
    alice_token = _login(client, "alice", "alice123")["access_token"]
    me = client.get("/api/auth/me", headers=_auth(alice_token)).json()
    r = client.put(f"/api/users/{me['id']}", headers=_auth(alice_token), json={
        "role": "admin",
    })
    assert r.status_code == 403


def test_admin_cannot_delete_self(client):
    admin_token = _login(client, "admin", "admin")["access_token"]
    me = client.get("/api/auth/me", headers=_auth(admin_token)).json()
    r = client.delete(f"/api/users/{me['id']}", headers=_auth(admin_token))
    assert r.status_code == 400


# ─── Project CRUD ─────────────────────────────────────────────────────────────


def test_editor_can_create_project(client):
    alice_token = _login(client, "alice", "alice123")["access_token"]
    r = client.post("/api/projects", headers=_auth(alice_token), json={
        "name": "Project A", "description": "test", "members": [],
    })
    assert r.status_code == 200
    project = r.json()
    assert project["name"] == "Project A"
    # owner 自动放进 members
    me = client.get("/api/auth/me", headers=_auth(alice_token)).json()
    assert me["id"] in project["members"]


def test_viewer_cannot_create_project(client):
    bob_token = _login(client, "bob", "bob123")["access_token"]
    r = client.post("/api/projects", headers=_auth(bob_token), json={
        "name": "P", "members": [],
    })
    assert r.status_code == 403


def test_list_projects_filters_by_membership(client):
    alice_token = _login(client, "alice", "alice123")["access_token"]
    bob_token = _login(client, "bob", "bob123")["access_token"]
    admin_token = _login(client, "admin", "admin")["access_token"]
    # alice 建一个项目
    client.post("/api/projects", headers=_auth(alice_token), json={
        "name": "Alice's", "members": [],
    })
    # bob 看不到（不是 member）
    r = client.get("/api/projects", headers=_auth(bob_token))
    assert r.status_code == 200
    assert r.json() == []
    # admin 能看到全部
    r = client.get("/api/projects", headers=_auth(admin_token))
    assert len(r.json()) == 1
    # alice 自己能看到
    r = client.get("/api/projects", headers=_auth(alice_token))
    assert len(r.json()) == 1


def test_only_owner_or_admin_can_delete_project(client):
    alice_token = _login(client, "alice", "alice123")["access_token"]
    bob_token = _login(client, "bob", "bob123")["access_token"]
    admin_token = _login(client, "admin", "admin")["access_token"]
    project = client.post("/api/projects", headers=_auth(alice_token), json={
        "name": "P", "members": [],
    }).json()
    # bob 删不了
    r = client.delete(f"/api/projects/{project['id']}", headers=_auth(bob_token))
    assert r.status_code == 403
    # admin 可以删（即使不是 owner）
    r = client.delete(f"/api/projects/{project['id']}", headers=_auth(admin_token))
    assert r.status_code == 200


# ─── 审计日志 ────────────────────────────────────────────────────────────────


def test_audit_log_records_mutations(client):
    admin_token = _login(client, "admin", "admin")["access_token"]
    # 跑一个 mutation：建项目
    client.post("/api/projects", headers=_auth(admin_token), json={
        "name": "Audit test", "members": [],
    })
    # 查日志
    r = client.get("/api/audit-logs?limit=10", headers=_auth(admin_token))
    assert r.status_code == 200
    logs = r.json()["logs"]
    # 至少应记录 POST /api/projects（最近的）+ 之前测试的 login（POST /api/auth/login）
    project_logs = [log for log in logs if log["path"] == "/api/projects" and log["method"] == "POST"]
    assert project_logs, f"未找到建项目的审计日志: {logs}"
    assert project_logs[0]["username"] == "admin"


def test_audit_log_admin_only(client):
    bob_token = _login(client, "bob", "bob123")["access_token"]
    r = client.get("/api/audit-logs", headers=_auth(bob_token))
    assert r.status_code == 403
