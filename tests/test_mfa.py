"""MFA (TOTP) 测试 —— service 函数 + enroll/verify/disable/status 端点 +
login 两步流 + challenge 端点。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pyotp
from jose import jwt

from app.services.auth import find_user_by_username
from app.services.mfa import (
    _challenge_secret,
    _MFA_CHALLENGE_ALG,
    _MFA_CHALLENGE_PURPOSE,
    decrypt_mfa_secret,
    encrypt_mfa_secret,
    generate_secret,
    issue_mfa_challenge_token,
    provisioning_uri,
    update_user_mfa,
    verify_mfa_challenge_token,
    verify_totp,
)


# ─── service 层：pure ───────────────────────────────────────────────────────


def test_generate_secret_is_base32_32_chars():
    s = generate_secret()
    assert len(s) == 32
    # base32 字母表
    assert all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567" for c in s)


def test_provisioning_uri_well_formed():
    s = generate_secret()
    uri = provisioning_uri(s, "admin")
    assert uri.startswith("otpauth://totp/")
    assert "DataOpsStudio" in uri
    assert "admin" in uri
    assert f"secret={s}" in uri


def test_verify_totp_accepts_current_code():
    s = generate_secret()
    code = pyotp.TOTP(s).now()
    assert verify_totp(s, code) is True


def test_verify_totp_rejects_wrong_code():
    s = generate_secret()
    assert verify_totp(s, "000000") is False
    assert verify_totp(s, "abc") is False
    assert verify_totp(s, "") is False
    assert verify_totp("", "123456") is False


def test_verify_totp_tolerates_drift_one_window(monkeypatch):
    # valid_window=1：前后各 30s 容忍 —— 模拟 30s 前那个时段的 code 仍接受
    s = generate_secret()
    totp = pyotp.TOTP(s)
    past_code = totp.at(datetime.now(timezone.utc) - timedelta(seconds=30))
    assert verify_totp(s, past_code) is True


# ─── secret 加解密往返 ────────────────────────────────────────────────────


def test_mfa_secret_encrypt_decrypt_roundtrip(isolated_storage):
    s = generate_secret()
    enc = encrypt_mfa_secret(s)
    assert enc != s  # 落盘不是明文
    assert decrypt_mfa_secret(enc) == s


def test_decrypt_empty_returns_empty(isolated_storage):
    assert decrypt_mfa_secret("") == ""


# ─── mfa_challenge token ────────────────────────────────────────────────────


def test_issue_verify_challenge_token_roundtrip():
    tok, ttl = issue_mfa_challenge_token("u-123")
    assert ttl == 300
    assert verify_mfa_challenge_token(tok) == "u-123"


def test_verify_challenge_rejects_garbage_and_empty():
    assert verify_mfa_challenge_token("") is None
    assert verify_mfa_challenge_token("not-a-jwt") is None


def test_verify_challenge_rejects_wrong_purpose():
    now = datetime.now(timezone.utc)
    fake = jwt.encode(
        {"sub": "u-1", "purpose": "login",
         "exp": int((now + timedelta(minutes=5)).timestamp())},
        _challenge_secret(), algorithm=_MFA_CHALLENGE_ALG,
    )
    assert verify_mfa_challenge_token(fake) is None


def test_verify_challenge_rejects_expired():
    now = datetime.now(timezone.utc)
    expired = jwt.encode(
        {"sub": "u-1", "purpose": _MFA_CHALLENGE_PURPOSE,
         "exp": int((now - timedelta(seconds=1)).timestamp())},
        _challenge_secret(), algorithm=_MFA_CHALLENGE_ALG,
    )
    assert verify_mfa_challenge_token(expired) is None


# ─── /api/auth/mfa/status ───────────────────────────────────────────────────


def test_status_initial_state(client):
    resp = client.get("/api/auth/mfa/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"enabled": False, "enrolled": False}


def test_status_requires_login(client_anon):
    assert client_anon.get("/api/auth/mfa/status").status_code == 401


# ─── /api/auth/mfa/enroll ───────────────────────────────────────────────────


def test_enroll_returns_secret_and_uri(client):
    resp = client.post("/api/auth/mfa/enroll")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["secret"]) == 32
    assert body["provisioning_uri"].startswith("otpauth://totp/")
    assert body["verified"] is False
    # 落盘了 secret，但还没 enable
    status = client.get("/api/auth/mfa/status").json()
    assert status == {"enabled": False, "enrolled": True}


def test_enroll_when_already_enabled_returns_409(client):
    # 直接把 admin 标 enabled（绕开 verify）
    admin = find_user_by_username("admin")
    s = generate_secret()
    update_user_mfa(admin.id, secret_encrypted=encrypt_mfa_secret(s), enabled=True)
    resp = client.post("/api/auth/mfa/enroll")
    assert resp.status_code == 409


# ─── /api/auth/mfa/verify ───────────────────────────────────────────────────


def test_verify_with_correct_code_enables_mfa(client):
    client.post("/api/auth/mfa/enroll")
    admin = find_user_by_username("admin")
    secret = decrypt_mfa_secret(admin.mfa_secret_encrypted)
    code = pyotp.TOTP(secret).now()
    resp = client.post("/api/auth/mfa/verify", json={"code": code})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    # 现在 enabled
    assert client.get("/api/auth/mfa/status").json() == {"enabled": True, "enrolled": True}


def test_verify_with_wrong_code_401(client):
    client.post("/api/auth/mfa/enroll")
    resp = client.post("/api/auth/mfa/verify", json={"code": "000000"})
    assert resp.status_code == 401
    assert client.get("/api/auth/mfa/status").json()["enabled"] is False


def test_verify_before_enroll_400(client):
    resp = client.post("/api/auth/mfa/verify", json={"code": "123456"})
    assert resp.status_code == 400


def test_verify_empty_code_400(client):
    client.post("/api/auth/mfa/enroll")
    resp = client.post("/api/auth/mfa/verify", json={})
    assert resp.status_code == 400


# ─── /api/auth/mfa/disable ──────────────────────────────────────────────────


def test_disable_with_correct_code_clears_mfa(client):
    admin = find_user_by_username("admin")
    s = generate_secret()
    update_user_mfa(admin.id, secret_encrypted=encrypt_mfa_secret(s), enabled=True)
    code = pyotp.TOTP(s).now()
    resp = client.post("/api/auth/mfa/disable", json={"code": code})
    assert resp.status_code == 200
    assert client.get("/api/auth/mfa/status").json() == {"enabled": False, "enrolled": False}


def test_disable_with_wrong_code_401_and_stays_enabled(client):
    admin = find_user_by_username("admin")
    s = generate_secret()
    update_user_mfa(admin.id, secret_encrypted=encrypt_mfa_secret(s), enabled=True)
    resp = client.post("/api/auth/mfa/disable", json={"code": "000000"})
    assert resp.status_code == 401
    assert client.get("/api/auth/mfa/status").json()["enabled"] is True


def test_disable_when_not_enabled_400(client):
    resp = client.post("/api/auth/mfa/disable", json={"code": "123456"})
    assert resp.status_code == 400


# ─── 登录两步流：login + /api/auth/mfa/challenge ───────────────────────────


def test_login_without_mfa_returns_access_token(client_anon):
    resp = client_anon.post(
        "/api/auth/login",
        json={"username": "admin", "password": "admin"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"]
    assert body.get("mfa_required") is False
    assert body["user"]["username"] == "admin"


def test_login_with_mfa_returns_challenge(client_anon, client_admin):
    # client_admin fixture 触发 bootstrap，然后我们用直 store update 开 MFA
    admin = find_user_by_username("admin")
    s = generate_secret()
    update_user_mfa(admin.id, secret_encrypted=encrypt_mfa_secret(s), enabled=True)

    resp = client_anon.post(
        "/api/auth/login",
        json={"username": "admin", "password": "admin"},
    )
    assert resp.status_code == 200
    body = resp.json()
    # 关键：access_token 空，mfa_required True
    assert body["mfa_required"] is True
    assert body["mfa_token"]
    assert not body["access_token"]
    assert body["user"] is None  # 不泄露 user 信息


def test_challenge_with_correct_code_issues_access_token(client_anon, client_admin):
    admin = find_user_by_username("admin")
    s = generate_secret()
    update_user_mfa(admin.id, secret_encrypted=encrypt_mfa_secret(s), enabled=True)

    login_resp = client_anon.post(
        "/api/auth/login",
        json={"username": "admin", "password": "admin"},
    )
    mfa_token = login_resp.json()["mfa_token"]

    code = pyotp.TOTP(s).now()
    resp = client_anon.post(
        "/api/auth/mfa/challenge",
        json={"mfa_token": mfa_token, "code": code},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"]
    assert body["mfa_required"] is False
    assert body["user"]["username"] == "admin"


def test_challenge_with_wrong_code_401(client_anon, client_admin):
    admin = find_user_by_username("admin")
    s = generate_secret()
    update_user_mfa(admin.id, secret_encrypted=encrypt_mfa_secret(s), enabled=True)

    login_resp = client_anon.post(
        "/api/auth/login",
        json={"username": "admin", "password": "admin"},
    )
    mfa_token = login_resp.json()["mfa_token"]

    resp = client_anon.post(
        "/api/auth/mfa/challenge",
        json={"mfa_token": mfa_token, "code": "000000"},
    )
    assert resp.status_code == 401


def test_challenge_with_bad_mfa_token_401(client_anon):
    resp = client_anon.post(
        "/api/auth/mfa/challenge",
        json={"mfa_token": "not-a-real-token", "code": "123456"},
    )
    assert resp.status_code == 401


def test_challenge_empty_payload_400(client_anon):
    resp = client_anon.post("/api/auth/mfa/challenge", json={})
    assert resp.status_code == 400


# ─── _redact 不泄露 MFA secret ──────────────────────────────────────────────


def test_me_endpoint_does_not_leak_mfa_secret(client):
    admin = find_user_by_username("admin")
    s = generate_secret()
    update_user_mfa(admin.id, secret_encrypted=encrypt_mfa_secret(s), enabled=True)
    me = client.get("/api/auth/me").json()
    assert me.get("mfa_enabled") is True
    assert me.get("mfa_secret_encrypted", "") == ""  # _redact 抹掉了
    assert me.get("password_hash", "") == ""
