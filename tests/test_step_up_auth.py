"""step-up 再认证测试 —— verify-password 端点 + 含密码导出强制 step-up。"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from jose import jwt

from app.services.auth import (
    JWT_ALG,
    JWT_SECRET,
    find_user_by_username,
)


def _old_admin_token(secs_ago: int = 600) -> str:
    """手签一个 iat 在过去的 admin token —— 用来模拟「最近认证超时」。"""
    admin = find_user_by_username("admin")
    now = int(datetime.now(timezone.utc).timestamp())
    payload = {
        "sub": admin.id, "username": admin.username, "role": admin.role,
        "jti": uuid.uuid4().hex,
        "iat": now - secs_ago,
        "exp": now + 3600,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


# ─── /api/auth/verify-password ──────────────────────────────────────────────


def test_verify_password_correct_issues_new_token(client):
    resp = client.post("/api/auth/verify-password", json={"password": "admin"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"] and len(body["access_token"]) > 50
    assert body["token_type"] == "bearer"
    assert body["expires_in"] > 0


def test_verify_password_wrong_returns_401(client):
    resp = client.post("/api/auth/verify-password", json={"password": "not-the-password"})
    assert resp.status_code == 401


def test_verify_password_empty_returns_401(client):
    resp = client.post("/api/auth/verify-password", json={})
    assert resp.status_code == 401


def test_verify_password_requires_login(client_anon):
    resp = client_anon.post("/api/auth/verify-password", json={"password": "admin"})
    assert resp.status_code == 401


# ─── /config/export step-up gate ───────────────────────────────────────────


def test_export_with_passwords_fresh_login_passes(client):
    """admin 刚登录（iat 新鲜）→ /config/export?include_passwords=true 200。"""
    resp = client.get("/config/export?include_passwords=true")
    assert resp.status_code == 200
    assert resp.json()["passwords_included"] is True


def test_export_with_passwords_old_token_triggers_step_up(client):
    """admin 老 token（iat 600s 前）→ include_passwords=true 触发 step_up_required。"""
    old = _old_admin_token(secs_ago=600)
    resp = client.get(
        "/config/export?include_passwords=true",
        headers={"Authorization": f"Bearer {old}"},
    )
    assert resp.status_code == 403
    detail = str(resp.json().get("detail") or "")
    assert "step_up_required" in detail


def test_export_no_passwords_old_token_still_passes(client):
    """老 token + 不含密码导出 → 200（step-up 只在 include_passwords=true 触发）。"""
    old = _old_admin_token(secs_ago=600)
    resp = client.get(
        "/config/export",
        headers={"Authorization": f"Bearer {old}"},
    )
    assert resp.status_code == 200
    assert resp.json()["passwords_included"] is False


def test_step_up_then_retry_export_succeeds(client):
    """完整流程：老 token 含密码导出 403 → verify-password 拿新 token → 重试 200。"""
    old = _old_admin_token(secs_ago=600)
    # 1) 老 token 含密码导出被拦
    resp1 = client.get(
        "/config/export?include_passwords=true",
        headers={"Authorization": f"Bearer {old}"},
    )
    assert resp1.status_code == 403

    # 2) 拿当前合法 client token（fixture 的 admin）调 verify-password 换新 token
    verify = client.post("/api/auth/verify-password", json={"password": "admin"})
    assert verify.status_code == 200
    new_token = verify.json()["access_token"]

    # 3) 用新 token 重试 —— iat 是 verify 那一刻，远新于 300s → 通过
    resp2 = client.get(
        "/config/export?include_passwords=true",
        headers={"Authorization": f"Bearer {new_token}"},
    )
    assert resp2.status_code == 200
    assert resp2.json()["passwords_included"] is True
