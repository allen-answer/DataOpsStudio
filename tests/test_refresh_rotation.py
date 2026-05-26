"""refresh token rotation 测试 —— service 层 + login/refresh/logout 端点 +
关键的 reuse detection 性质。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from jose import jwt

from app.services.refresh import (
    _ALG,
    _PURPOSE,
    _refresh_secret,
    issue_refresh_token,
    prune_refresh_tokens,
    revoke_refresh_chain,
    rotate_refresh_token,
    verify_refresh_token,
)


# ─── issue / verify ────────────────────────────────────────────────────────


def test_issue_verify_roundtrip(isolated_storage):
    tok, jti, ttl = issue_refresh_token("u-1")
    assert tok and jti and ttl > 0
    verified = verify_refresh_token(tok)
    assert verified == ("u-1", jti)


def test_verify_rejects_garbage(isolated_storage):
    assert verify_refresh_token("") is None
    assert verify_refresh_token("not-a-jwt") is None


def test_verify_rejects_tampered(isolated_storage):
    tok, _, _ = issue_refresh_token("u-1")
    assert verify_refresh_token(tok[:-3] + "xyz") is None


def test_verify_rejects_wrong_purpose(isolated_storage):
    now = datetime.now(timezone.utc)
    fake = jwt.encode(
        {"sub": "u-1", "purpose": "login", "jti": "j-1",
         "exp": int((now + timedelta(hours=1)).timestamp())},
        _refresh_secret(), algorithm=_ALG,
    )
    assert verify_refresh_token(fake) is None


def test_verify_rejects_expired(isolated_storage):
    # 签发一个真的 token,然后手动让 DB 记录 exp 在过去
    from app.services import sqlite_store
    tok, jti, _ = issue_refresh_token("u-1")
    past = int((datetime.now(timezone.utc) - timedelta(minutes=1)).timestamp())
    with sqlite_store.connect() as conn:
        conn.execute("UPDATE refresh_tokens SET exp = ? WHERE jti = ?", (past, jti))
    assert verify_refresh_token(tok) is None


def test_verify_rejects_unknown_jti(isolated_storage):
    # 拿对的密钥签一个 DB 里没有的 jti
    now = datetime.now(timezone.utc)
    fake = jwt.encode(
        {"sub": "u-1", "purpose": _PURPOSE, "jti": "jti-not-in-db",
         "exp": int((now + timedelta(hours=1)).timestamp())},
        _refresh_secret(), algorithm=_ALG,
    )
    assert verify_refresh_token(fake) is None


# ─── rotation ──────────────────────────────────────────────────────────────


def test_rotate_issues_new_pair_and_marks_old(isolated_storage):
    from app.services import sqlite_store
    old_tok, old_jti, _ = issue_refresh_token("u-1")
    rotated = rotate_refresh_token(old_tok)
    assert rotated is not None
    user_id, new_tok, new_jti, ttl = rotated
    assert user_id == "u-1"
    assert new_jti != old_jti
    assert ttl > 0
    # 新 token 可验
    assert verify_refresh_token(new_tok) == ("u-1", new_jti)
    # DB 里老 token 的 replaced_by 已填新 jti
    with sqlite_store.connect() as conn:
        row = conn.execute(
            "SELECT replaced_by FROM refresh_tokens WHERE jti = ?", (old_jti,),
        ).fetchone()
    assert row[0] == new_jti


# ─── REUSE DETECTION（refresh rotation 的核心安全性质）──────────────────────


def test_reuse_detection_revokes_only_this_branch(isolated_storage):
    """rotate 过一次的 old token 再被用 → 视为盗用,**只** revoke 这条 chain,
    同用户的其他独立 chain(多设备 / 多 tab / Playwright verify 等场景)不动。

    向前 + 向后传播:从被重放的 jti 出发,顺 replaced_by 链 + 找 predecessor,
    把整条 chain 上的 jti 全部 revoke,但不杀 user 名下别的 root chain。
    """
    from app.services import sqlite_store
    # u-1 同时有两个 active refresh chain(模拟同账号两个独立 session
    # 比如桌面浏览器 + Playwright verify)
    old_tok, old_jti, _ = issue_refresh_token("u-1")
    other_tok, other_jti, _ = issue_refresh_token("u-1")

    # session A 正常 rotation:old → new
    rotated = rotate_refresh_token(old_tok)
    assert rotated is not None
    _, _, new_jti, _ = rotated

    # 攻击者用 old 再 rotate → 应被识别为重放
    assert rotate_refresh_token(old_tok) is None

    # 这条 chain(old → new)都被 revoke
    with sqlite_store.connect() as conn:
        rows = conn.execute(
            "SELECT jti, revoked_at FROM refresh_tokens WHERE user_id = ?", ("u-1",),
        ).fetchall()
    revoked = {r[0]: r[1] for r in rows}
    assert revoked[old_jti] is not None, "重放的 jti 应被 revoke"
    assert revoked[new_jti] is not None, "顺向 chain 的后继也应被 revoke"

    # **关键**:other(独立 chain)**不受影响**,仍能 verify + 继续用
    assert revoked[other_jti] is None
    assert verify_refresh_token(other_tok) is not None
    # 还能 rotate 出新对
    assert rotate_refresh_token(other_tok) is not None


def test_revoke_branch_walks_both_directions(isolated_storage):
    """revoke_refresh_branch 从中间节点出发,向前 + 向后 + 全 revoke。"""
    from app.services import refresh
    from app.services import sqlite_store
    # 制造 chain: A → B → C(三代)
    a_tok, a_jti, _ = issue_refresh_token("u-1")
    rotated_b = rotate_refresh_token(a_tok)  # A → B
    assert rotated_b is not None
    _, b_tok, b_jti, _ = rotated_b
    rotated_c = rotate_refresh_token(b_tok)  # B → C
    assert rotated_c is not None
    _, _, c_jti, _ = rotated_c

    # 另一条独立 chain
    other_tok, other_jti, _ = issue_refresh_token("u-1")

    # 从 chain 中间(B)出发 revoke branch
    n = refresh.revoke_refresh_branch(b_jti)
    assert n >= 1

    with sqlite_store.connect() as conn:
        rows = conn.execute(
            "SELECT jti, revoked_at FROM refresh_tokens WHERE user_id = ?", ("u-1",),
        ).fetchall()
    rev = {r[0]: r[1] is not None for r in rows}
    # A/B/C 整条 chain 都 revoke
    assert rev[a_jti] is True
    assert rev[b_jti] is True
    assert rev[c_jti] is True
    # 另一条独立 chain 不动
    assert rev[other_jti] is False


def test_verify_revoked_token_returns_none(isolated_storage):
    tok, _, _ = issue_refresh_token("u-1")
    revoke_refresh_chain("u-1")
    assert verify_refresh_token(tok) is None


def test_revoke_chain_only_touches_user(isolated_storage):
    a_tok, _, _ = issue_refresh_token("u-a")
    b_tok, _, _ = issue_refresh_token("u-b")
    revoke_refresh_chain("u-a")
    assert verify_refresh_token(a_tok) is None
    assert verify_refresh_token(b_tok) is not None  # 别人不受影响


# ─── prune ──────────────────────────────────────────────────────────────────


def test_prune_removes_expired(isolated_storage):
    from app.services import sqlite_store
    _, jti, _ = issue_refresh_token("u-1")
    past = int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp())
    with sqlite_store.connect() as conn:
        conn.execute("UPDATE refresh_tokens SET exp = ? WHERE jti = ?", (past, jti))
    removed = prune_refresh_tokens()
    assert removed == 1


# ─── ttl=0 关闭 refresh ────────────────────────────────────────────────────


def test_issue_disabled_by_env(monkeypatch, isolated_storage):
    monkeypatch.setenv("DATAOPS_REFRESH_TTL_SECONDS", "0")
    tok, jti, ttl = issue_refresh_token("u-1")
    assert tok == "" and jti == "" and ttl == 0


# ─── 端点 ──────────────────────────────────────────────────────────────────


def test_login_returns_refresh_token(client_anon):
    resp = client_anon.post(
        "/api/auth/login", json={"username": "admin", "password": "admin"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["refresh_expires_in"] >= 7 * 24 * 3600


def test_refresh_endpoint_rotates(client_anon):
    login = client_anon.post(
        "/api/auth/login", json={"username": "admin", "password": "admin"},
    ).json()
    old_refresh = login["refresh_token"]

    resp = client_anon.post(
        "/api/auth/refresh", json={"refresh_token": old_refresh},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["refresh_token"] != old_refresh
    assert body["user"]["username"] == "admin"


def test_refresh_endpoint_rejects_old_after_rotation(client_anon):
    """rotation 后老 refresh 不能再用 —— 重放检测 401 + chain 整体 revoke。"""
    login = client_anon.post(
        "/api/auth/login", json={"username": "admin", "password": "admin"},
    ).json()
    old_refresh = login["refresh_token"]

    # 正常 rotation
    rotated = client_anon.post(
        "/api/auth/refresh", json={"refresh_token": old_refresh},
    )
    assert rotated.status_code == 200
    new_refresh = rotated.json()["refresh_token"]

    # 拿老 refresh 再试 → 401 + 触发 chain revoke
    reuse = client_anon.post(
        "/api/auth/refresh", json={"refresh_token": old_refresh},
    )
    assert reuse.status_code == 401

    # 新 refresh 也被连带 revoke —— 用户必须重新登录
    after_reuse = client_anon.post(
        "/api/auth/refresh", json={"refresh_token": new_refresh},
    )
    assert after_reuse.status_code == 401


def test_refresh_endpoint_empty_400(client_anon):
    assert client_anon.post("/api/auth/refresh", json={}).status_code == 400


def test_refresh_endpoint_bad_token_401(client_anon):
    assert client_anon.post(
        "/api/auth/refresh", json={"refresh_token": "bad"},
    ).status_code == 401


def test_logout_revokes_refresh_chain(client):
    # client 已带 admin token; 看 login 返回的 refresh
    login = client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin"},
    ).json()
    refresh = login["refresh_token"]
    assert refresh

    # 登出
    assert client.post("/api/auth/logout").status_code == 200

    # 老 refresh 不能再换新 access
    resp = client.post("/api/auth/refresh", json={"refresh_token": refresh})
    assert resp.status_code == 401


# ─── HttpOnly cookie 路径 ──────────────────────────────────────────────────


def test_login_sets_httponly_refresh_cookie(client_anon):
    """login 成功 → Set-Cookie 含 dataops_refresh + HttpOnly + SameSite=strict。"""
    resp = client_anon.post(
        "/api/auth/login", json={"username": "admin", "password": "admin"},
    )
    assert resp.status_code == 200
    set_cookie = resp.headers.get("set-cookie") or ""
    assert "dataops_refresh=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=strict" in set_cookie
    assert "Path=/" in set_cookie
    # TestClient http://testserver,Secure 不该开（否则浏览器不送）
    assert "Secure" not in set_cookie
    # cookie value 跟 body 的 refresh_token 一致
    body = resp.json()
    expected = body["refresh_token"]
    assert f"dataops_refresh={expected}" in set_cookie


def test_refresh_via_cookie_only_no_body_succeeds(client_anon):
    """前端走 HttpOnly cookie 路径:body 空,refresh 仅靠 cookie。"""
    login = client_anon.post(
        "/api/auth/login", json={"username": "admin", "password": "admin"},
    )
    # TestClient 自动持久 cookie,下一个请求会带上
    assert "dataops_refresh" in login.cookies or "dataops_refresh" in login.headers.get("set-cookie", "")
    # body 空 dict —— 全靠 cookie
    resp = client_anon.post("/api/auth/refresh", json={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["access_token"]
    assert body["refresh_token"]  # 新 refresh token


def test_logout_clears_refresh_cookie(client_anon):
    login = client_anon.post(
        "/api/auth/login", json={"username": "admin", "password": "admin"},
    )
    assert login.status_code == 200
    # 用 access token 登出
    token = login.json()["access_token"]
    logout = client_anon.post(
        "/api/auth/logout", headers={"Authorization": f"Bearer {token}"},
    )
    assert logout.status_code == 200
    # logout 响应必须 Set-Cookie 清掉 refresh(max-age=0)
    sc = logout.headers.get("set-cookie") or ""
    assert "dataops_refresh=" in sc
    assert "Max-Age=0" in sc


def test_refresh_body_takes_precedence_over_cookie(client_anon, isolated_storage):
    """body 显式提供 refresh_token 时优先于 cookie —— 保证 reuse detection 仍按
    body 携带的那条走完整 rotation/reuse 语义(避免 cookie shadow 掉显式调用)。"""
    login = client_anon.post(
        "/api/auth/login", json={"username": "admin", "password": "admin"},
    ).json()
    refresh_a = login["refresh_token"]
    # client_anon.cookies 已含 refresh_a;rotation 用 body 显式给 → 拿 refresh_b
    rotated = client_anon.post(
        "/api/auth/refresh", json={"refresh_token": refresh_a},
    )
    assert rotated.status_code == 200
    refresh_b = rotated.json()["refresh_token"]
    # 重用 refresh_a(body 显式),cookie 已是 refresh_b 也不该 shadow → reuse 检出 401
    reuse = client_anon.post(
        "/api/auth/refresh", json={"refresh_token": refresh_a},
    )
    assert reuse.status_code == 401
    # refresh_b 也被连带 revoke
    after = client_anon.post(
        "/api/auth/refresh", json={"refresh_token": refresh_b},
    )
    assert after.status_code == 401
