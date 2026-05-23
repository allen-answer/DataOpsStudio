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
    assert body == {"enabled": False, "enrolled": False, "recovery_codes_remaining": 0}


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
    assert status["enabled"] is False
    assert status["enrolled"] is True
    assert status["recovery_codes_remaining"] == 0  # verify 才生成


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
    body = resp.json()
    assert body["ok"] is True
    # 首次启用,返 10 个 recovery codes
    assert len(body["recovery_codes"]) == 10
    # 现在 enabled
    status = client.get("/api/auth/mfa/status").json()
    assert status["enabled"] is True
    assert status["enrolled"] is True
    assert status["recovery_codes_remaining"] == 10


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
    status = client.get("/api/auth/mfa/status").json()
    assert status["enabled"] is False
    assert status["enrolled"] is False
    # disable 必清空 recovery codes —— 绑死在旧 secret 上,留着没意义
    assert status["recovery_codes_remaining"] == 0


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
    update_user_mfa(
        admin.id,
        secret_encrypted=encrypt_mfa_secret(s),
        enabled=True,
        recovery_codes_hashed=["$2b$10$abcfake", "$2b$10$xyzfake"],
    )
    me = client.get("/api/auth/me").json()
    assert me.get("mfa_enabled") is True
    assert me.get("mfa_secret_encrypted", "") == ""  # _redact 抹掉了
    assert me.get("password_hash", "") == ""
    # recovery code hash 也是 secret —— _redact 必清(防字典攻击锁定哈希后离线 brute)
    assert me.get("mfa_recovery_codes_hashed", []) == []


# ─── Recovery codes ─────────────────────────────────────────────────────────


def test_generate_recovery_codes_returns_10_distinct_formatted():
    from app.services.mfa import generate_recovery_codes

    plain, hashed = generate_recovery_codes()
    assert len(plain) == 10
    assert len(hashed) == 10
    # 全部去重
    assert len(set(plain)) == 10
    assert len(set(hashed)) == 10
    # 格式 `XXXXX-XXXXX`,只含 alphabet 字符 + 1 个分隔符
    for code in plain:
        assert len(code) == 11  # 10 char + 1 dash
        assert code[5] == "-"
        body = code.replace("-", "")
        assert all(ch in "ABCDEFGHJKMNPQRSTUVWXYZ23456789" for ch in body)
    # 哈希不等于明文
    for p, h in zip(plain, hashed):
        assert h != p
        assert h.startswith("$2b$")  # bcrypt prefix


def test_verify_and_consume_recovery_code_matches_and_pops(client, isolated_storage):
    """成功验:存在的明文 code 被 normalize 后 bcrypt 匹配 → 落盘里少掉那条 hash。"""
    from app.services.mfa import (
        generate_recovery_codes,
        verify_and_consume_recovery_code,
    )

    admin = find_user_by_username("admin")
    plain, hashed = generate_recovery_codes()
    update_user_mfa(admin.id, recovery_codes_hashed=hashed)

    # 用第一个 code(带分隔符)验过
    assert verify_and_consume_recovery_code(admin.id, plain[0]) is True
    # 落盘 list 少了一条
    fresh = find_user_by_username("admin")
    assert len(fresh.mfa_recovery_codes_hashed) == 9
    # 同一 code 再验失败(single-use)
    assert verify_and_consume_recovery_code(admin.id, plain[0]) is False


def test_verify_recovery_code_accepts_normalized_input(client_admin):
    """`abcde-fghjk` / `ABCDE FGHJK` / `ABCDEFGHJK` 三种写法都能验过。"""
    from app.services.mfa import (
        generate_recovery_codes,
        verify_and_consume_recovery_code,
    )

    admin = find_user_by_username("admin")
    plain, hashed = generate_recovery_codes()
    update_user_mfa(admin.id, recovery_codes_hashed=hashed)

    # plain[0] = "ABCDE-FGHJK" 形式;试小写 + 空白替代分隔符
    body = plain[0].replace("-", "")
    assert verify_and_consume_recovery_code(admin.id, body.lower()) is True

    # 再生成一组,试空格分隔
    plain2, hashed2 = generate_recovery_codes()
    update_user_mfa(admin.id, recovery_codes_hashed=hashed2)
    spaced = plain2[0][:5] + "   " + plain2[0][6:]   # 替 `-` 为多空格
    assert verify_and_consume_recovery_code(admin.id, spaced) is True


def test_verify_recovery_code_wrong_returns_false(client_admin):
    from app.services.mfa import (
        generate_recovery_codes,
        verify_and_consume_recovery_code,
    )

    admin = find_user_by_username("admin")
    _plain, hashed = generate_recovery_codes()
    update_user_mfa(admin.id, recovery_codes_hashed=hashed)

    assert verify_and_consume_recovery_code(admin.id, "ZZZZZ-ZZZZZ") is False
    assert verify_and_consume_recovery_code(admin.id, "") is False
    # 无 codes 的用户
    update_user_mfa(admin.id, recovery_codes_hashed=[])
    assert verify_and_consume_recovery_code(admin.id, "ANY-CODE") is False


def test_regenerate_recovery_codes_requires_otp_and_replaces_old(client):
    """regenerate-recovery-codes:必须当前 OTP,旧 codes 全失效,返 10 新 codes。"""
    admin = find_user_by_username("admin")
    s = generate_secret()
    from app.services.mfa import generate_recovery_codes, verify_and_consume_recovery_code

    old_plain, old_hashed = generate_recovery_codes()
    update_user_mfa(
        admin.id, secret_encrypted=encrypt_mfa_secret(s), enabled=True,
        recovery_codes_hashed=old_hashed,
    )
    code = pyotp.TOTP(s).now()
    resp = client.post("/api/auth/mfa/recovery-codes/regenerate", json={"code": code})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert len(body["recovery_codes"]) == 10
    # 旧 codes 全失效
    assert verify_and_consume_recovery_code(admin.id, old_plain[0]) is False
    # 状态显示还剩 10
    assert client.get("/api/auth/mfa/status").json()["recovery_codes_remaining"] == 10


def test_regenerate_recovery_codes_wrong_otp_401(client):
    admin = find_user_by_username("admin")
    s = generate_secret()
    update_user_mfa(admin.id, secret_encrypted=encrypt_mfa_secret(s), enabled=True)
    resp = client.post("/api/auth/mfa/recovery-codes/regenerate", json={"code": "000000"})
    assert resp.status_code == 401


def test_regenerate_recovery_codes_mfa_disabled_400(client):
    """MFA 未开启就 regenerate → 400(没意义)。"""
    resp = client.post("/api/auth/mfa/recovery-codes/regenerate", json={"code": "123456"})
    assert resp.status_code == 400


def test_challenge_with_recovery_code_issues_access_token(client_anon, client_admin):
    """登录两步流第二步:用 recovery_code 替代 6 位 OTP,换正式 token + 消费一个 code。"""
    admin = find_user_by_username("admin")
    s = generate_secret()
    from app.services.mfa import generate_recovery_codes

    plain, hashed = generate_recovery_codes()
    update_user_mfa(
        admin.id, secret_encrypted=encrypt_mfa_secret(s), enabled=True,
        recovery_codes_hashed=hashed,
    )

    login_resp = client_anon.post(
        "/api/auth/login", json={"username": "admin", "password": "admin"},
    )
    mfa_token = login_resp.json()["mfa_token"]
    assert mfa_token

    # 用 recovery code 走 challenge
    chal_resp = client_anon.post(
        "/api/auth/mfa/challenge",
        json={"mfa_token": mfa_token, "recovery_code": plain[0]},
    )
    assert chal_resp.status_code == 200, chal_resp.text
    body = chal_resp.json()
    assert body["access_token"]
    assert body["user"]["username"] == "admin"
    # code 已消费 —— 剩 9 个
    fresh = find_user_by_username("admin")
    assert len(fresh.mfa_recovery_codes_hashed) == 9


def test_challenge_with_used_recovery_code_401(client_anon, client_admin):
    """同一 recovery code 用两次:第二次 401(已 single-use 删掉)。"""
    admin = find_user_by_username("admin")
    s = generate_secret()
    from app.services.mfa import generate_recovery_codes

    plain, hashed = generate_recovery_codes()
    update_user_mfa(
        admin.id, secret_encrypted=encrypt_mfa_secret(s), enabled=True,
        recovery_codes_hashed=hashed,
    )

    def _login_and_chal_with(code):
        lr = client_anon.post(
            "/api/auth/login", json={"username": "admin", "password": "admin"},
        )
        token = lr.json()["mfa_token"]
        return client_anon.post(
            "/api/auth/mfa/challenge",
            json={"mfa_token": token, "recovery_code": code},
        )

    # 第一次 OK
    assert _login_and_chal_with(plain[0]).status_code == 200
    # 第二次同一 code 401
    second = _login_and_chal_with(plain[0])
    assert second.status_code == 401
    assert "无效或已用过" in second.json().get("detail", "")


def test_challenge_with_garbage_recovery_code_401(client_anon, client_admin):
    admin = find_user_by_username("admin")
    s = generate_secret()
    update_user_mfa(admin.id, secret_encrypted=encrypt_mfa_secret(s), enabled=True)

    lr = client_anon.post(
        "/api/auth/login", json={"username": "admin", "password": "admin"},
    )
    token = lr.json()["mfa_token"]
    resp = client_anon.post(
        "/api/auth/mfa/challenge",
        json={"mfa_token": token, "recovery_code": "ZZZZZ-ZZZZZ"},
    )
    assert resp.status_code == 401


def test_first_time_verify_returns_codes_re_verify_does_not(client):
    """首次 enroll-verify 返 10 codes;已启用账号再走 verify 不重置 codes(返空 list)。"""
    client.post("/api/auth/mfa/enroll")
    admin = find_user_by_username("admin")
    secret = decrypt_mfa_secret(admin.mfa_secret_encrypted)
    code = pyotp.TOTP(secret).now()
    first = client.post("/api/auth/mfa/verify", json={"code": code})
    assert len(first.json()["recovery_codes"]) == 10
    # 再 verify 一次
    code2 = pyotp.TOTP(secret).now()
    second = client.post("/api/auth/mfa/verify", json={"code": code2})
    assert second.status_code == 200
    assert second.json()["recovery_codes"] == []   # 已启用,不重发 codes
    # 还是 10 个
    assert client.get("/api/auth/mfa/status").json()["recovery_codes_remaining"] == 10
