"""JWT token 吊销 / 真 logout 测试。

`isolated_storage` 把 SQLite 重定向到 tmp —— 吊销表落 tmp 不污染真库。
"""
from __future__ import annotations

from datetime import datetime, timezone

from jose import jwt

from app.models import User
from app.services.auth import (
    JWT_ALG,
    JWT_SECRET,
    create_access_token,
    decode_access_token,
    find_user_by_username,
    is_token_revoked,
    prune_revoked_tokens,
    revoke_token,
)


def _user(uid: str = "u1", role: str = "admin") -> User:
    return User(
        id=uid, username="t", password_hash="", role=role,
        display_name="t", created_at="2026-01-01T00:00:00",
    )


# ─── jti claim ──────────────────────────────────────────────────────────────


def test_token_carries_jti():
    token, _ = create_access_token(_user())
    payload = decode_access_token(token)
    assert payload is not None
    assert payload.get("jti")


def test_each_token_has_unique_jti():
    a = decode_access_token(create_access_token(_user())[0])
    b = decode_access_token(create_access_token(_user())[0])
    assert a["jti"] != b["jti"]


# ─── revoke_token / is_token_revoked / prune ────────────────────────────────


def test_revoke_then_is_revoked(isolated_storage):
    revoke_token("jti-abc", exp=9_999_999_999, user_id="u1")
    assert is_token_revoked("jti-abc") is True
    assert is_token_revoked("jti-never-revoked") is False


def test_is_revoked_handles_empty(isolated_storage):
    assert is_token_revoked(None) is False
    assert is_token_revoked("") is False


def test_revoke_empty_jti_is_noop(isolated_storage):
    revoke_token("", exp=9_999_999_999)  # 不抛
    assert is_token_revoked("") is False


def test_revoke_is_idempotent(isolated_storage):
    revoke_token("dup", exp=9_999_999_999)
    revoke_token("dup", exp=9_999_999_999)  # INSERT OR IGNORE，不报 PK 冲突
    assert is_token_revoked("dup") is True


def test_prune_removes_only_expired(isolated_storage):
    revoke_token("expired", exp=1)                 # 1970 —— 早过期
    revoke_token("fresh", exp=9_999_999_999)       # 远未来
    removed = prune_revoked_tokens()
    assert removed == 1
    assert is_token_revoked("expired") is False
    assert is_token_revoked("fresh") is True


# ─── logout 端点 ────────────────────────────────────────────────────────────


def test_logout_invalidates_current_token(client):
    assert client.get("/api/auth/me").status_code == 200
    assert client.post("/api/auth/logout").status_code == 200
    # 同一 token 已被吊销 —— 后续请求 401
    assert client.get("/api/auth/me").status_code == 401


def test_logout_requires_login(client_anon):
    assert client_anon.post("/api/auth/logout").status_code == 401


def test_revoked_token_rejected_on_business_endpoint(client):
    assert client.get("/api/tasks").status_code == 200
    client.post("/api/auth/logout")
    assert client.get("/api/tasks").status_code == 401


def test_legacy_token_without_jti_still_valid(client):
    """本次改动前签发的 token 无 jti claim —— 不应被误判吊销，平滑过渡。"""
    admin = find_user_by_username("admin")
    now = int(datetime.now(timezone.utc).timestamp())
    legacy = jwt.encode(
        {
            "sub": admin.id, "username": admin.username, "role": admin.role,
            "iat": now, "exp": now + 3600,
        },
        JWT_SECRET, algorithm=JWT_ALG,
    )
    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {legacy}"})
    assert resp.status_code == 200
