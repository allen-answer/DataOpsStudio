"""Auth rate limit 测试 —— pure service + endpoint 集成。"""
from __future__ import annotations

import pytest


# ─── RateLimiter pure ───────────────────────────────────────────────────────


def test_ratelimiter_allows_up_to_limit():
    from app.services.rate_limit import RateLimiter

    rl = RateLimiter(limit=3, window_seconds=60)
    for _ in range(3):
        assert rl.check("k").allowed is True
    # 第 4 次拦
    r = rl.check("k")
    assert r.allowed is False
    assert r.retry_after > 0


def test_ratelimiter_different_keys_independent():
    from app.services.rate_limit import RateLimiter

    rl = RateLimiter(limit=2, window_seconds=60)
    assert rl.check("a").allowed
    assert rl.check("a").allowed
    assert rl.check("a").allowed is False  # a 达上限
    # b 独立计数
    assert rl.check("b").allowed
    assert rl.check("b").allowed


def test_ratelimiter_window_expiry(monkeypatch):
    """戳超出 window 后 popleft → 重新有额度。"""
    from app.services import rate_limit as rl_mod

    fake_now = {"t": 1000.0}
    monkeypatch.setattr(rl_mod, "monotonic", lambda: fake_now["t"])
    rl = rl_mod.RateLimiter(limit=2, window_seconds=10)

    rl.check("k")
    rl.check("k")
    assert rl.check("k").allowed is False
    # 跨过窗口
    fake_now["t"] += 11
    # 之前两个戳都过期了 —— 全清
    assert rl.check("k").allowed is True


def test_ratelimiter_remaining_count():
    from app.services.rate_limit import RateLimiter

    rl = RateLimiter(limit=5, window_seconds=60)
    r = rl.check("k")
    assert r.remaining == 4
    rl.check("k")
    r = rl.check("k")
    assert r.remaining == 2


def test_ratelimiter_reset():
    from app.services.rate_limit import RateLimiter

    rl = RateLimiter(limit=2, window_seconds=60)
    rl.check("k")
    rl.check("k")
    assert rl.check("k").allowed is False
    rl.reset("k")
    assert rl.check("k").allowed is True


def test_ratelimiter_reset_all():
    from app.services.rate_limit import RateLimiter

    rl = RateLimiter(limit=1, window_seconds=60)
    rl.check("a")
    rl.check("b")
    assert rl.check("a").allowed is False
    assert rl.check("b").allowed is False
    rl.reset()  # 不传 key = 全清
    assert rl.check("a").allowed is True
    assert rl.check("b").allowed is True


def test_ratelimiter_bad_constructor_args():
    from app.services.rate_limit import RateLimiter

    with pytest.raises(ValueError):
        RateLimiter(limit=0, window_seconds=60)
    with pytest.raises(ValueError):
        RateLimiter(limit=-1, window_seconds=60)
    with pytest.raises(ValueError):
        RateLimiter(limit=5, window_seconds=0)


# ─── client_ip_from_request / mask 辅助 ─────────────────────────────────────


def test_client_ip_prefers_xff_first_hop():
    from app.services.rate_limit import client_ip_from_request

    # 模拟一个带 XFF 的 starlette Request —— 用 dict-like header 假对象
    class FakeReq:
        headers = {"x-forwarded-for": "203.0.113.7, 10.0.0.1"}
        class client:
            host = "127.0.0.1"
    assert client_ip_from_request(FakeReq()) == "203.0.113.7"


def test_client_ip_fallback_to_request_client_host():
    from app.services.rate_limit import client_ip_from_request

    class FakeReq:
        headers = {}
        class client:
            host = "127.0.0.1"
    assert client_ip_from_request(FakeReq()) == "127.0.0.1"


def test_mask_ip_v4():
    from app.services.rate_limit import _mask_ip

    assert _mask_ip("203.0.113.7") == "203.0.x.x"
    assert _mask_ip("unknown") == "unknown"
    assert _mask_ip("") == "unknown"


def test_mask_username_short_and_long():
    from app.services.rate_limit import _mask_username

    assert _mask_username("ab") == "***"
    assert _mask_username("abcd") == "***"
    assert _mask_username("adminuser") == "ad***er"
    assert _mask_username(None) == "-"


# ─── /api/auth/login endpoint 集成 ──────────────────────────────────────────


def test_login_rate_limit_per_ip_429_after_threshold(client_anon, monkeypatch):
    """同一 IP 错密码 11 次 → 第 11 次拿 429 + Retry-After。"""
    # 把 IP 限速调到一个好测的数(5);user 限速调更高免得先撞它
    monkeypatch.setattr("app.services.rate_limit._LOGIN_LIMIT_PER_MIN", 5)
    monkeypatch.setattr("app.services.rate_limit._USER_LIMIT_PER_MIN", 100)
    # 重建 limiter(模块加载时初始化的实例还是用旧 limit)
    from app.services import rate_limit as rl
    rl.login_ip_limiter = rl.RateLimiter(5, rl._WINDOW_SECONDS)
    rl.login_user_limiter = rl.RateLimiter(100, rl._WINDOW_SECONDS)

    # 第 1-5 次:错密码 401
    for i in range(5):
        r = client_anon.post(
            "/api/auth/login", json={"username": "nosuch", "password": "wrong"},
        )
        assert r.status_code == 401, f"attempt {i+1} should be 401: {r.text}"

    # 第 6 次:被 IP 限速拦,429
    r = client_anon.post(
        "/api/auth/login", json={"username": "nosuch", "password": "wrong"},
    )
    assert r.status_code == 429, r.text
    assert "Retry-After" in r.headers
    assert int(r.headers["Retry-After"]) >= 1
    assert "登录尝试过于频繁" in r.json().get("detail", "")


def test_login_rate_limit_per_username_429(client_anon, monkeypatch):
    """user 限速比 IP 严格 → 同一 username 在 limit 以下次数后 429,detail 区分。"""
    monkeypatch.setattr("app.services.rate_limit._LOGIN_LIMIT_PER_MIN", 100)
    monkeypatch.setattr("app.services.rate_limit._USER_LIMIT_PER_MIN", 3)
    from app.services import rate_limit as rl
    rl.login_ip_limiter = rl.RateLimiter(100, rl._WINDOW_SECONDS)
    rl.login_user_limiter = rl.RateLimiter(3, rl._WINDOW_SECONDS)

    for _ in range(3):
        r = client_anon.post(
            "/api/auth/login", json={"username": "admin", "password": "wrong"},
        )
        assert r.status_code == 401

    r = client_anon.post(
        "/api/auth/login", json={"username": "admin", "password": "wrong"},
    )
    assert r.status_code == 429
    # detail 区分账号级 vs IP 级
    assert "该账号" in r.json().get("detail", "")


def test_login_rate_limit_case_insensitive_username(client_anon, monkeypatch):
    """`Admin` / `admin` / `ADMIN` 统一归一化大小写,共享同一计数。"""
    monkeypatch.setattr("app.services.rate_limit._LOGIN_LIMIT_PER_MIN", 100)
    monkeypatch.setattr("app.services.rate_limit._USER_LIMIT_PER_MIN", 2)
    from app.services import rate_limit as rl
    rl.login_ip_limiter = rl.RateLimiter(100, rl._WINDOW_SECONDS)
    rl.login_user_limiter = rl.RateLimiter(2, rl._WINDOW_SECONDS)

    assert client_anon.post(
        "/api/auth/login", json={"username": "admin", "password": "x"},
    ).status_code == 401
    assert client_anon.post(
        "/api/auth/login", json={"username": "Admin", "password": "x"},
    ).status_code == 401
    # 第 3 次大写也应该走同一计数 → 429
    r = client_anon.post(
        "/api/auth/login", json={"username": "ADMIN", "password": "x"},
    )
    assert r.status_code == 429


def test_login_rate_limit_does_not_block_correct_password_within_limit(client_anon, monkeypatch):
    """对的密码登录次数计入,但仍 < limit → 正常拿 access_token。"""
    monkeypatch.setattr("app.services.rate_limit._LOGIN_LIMIT_PER_MIN", 5)
    monkeypatch.setattr("app.services.rate_limit._USER_LIMIT_PER_MIN", 5)
    from app.services import rate_limit as rl
    rl.login_ip_limiter = rl.RateLimiter(5, rl._WINDOW_SECONDS)
    rl.login_user_limiter = rl.RateLimiter(5, rl._WINDOW_SECONDS)

    r = client_anon.post(
        "/api/auth/login", json={"username": "admin", "password": "admin"},
    )
    assert r.status_code == 200
    assert r.json()["access_token"]


def test_login_rate_limit_disabled_by_env(client_anon, monkeypatch):
    """DATAOPS_RATELIMIT_ENFORCE=false → check_auth_rate_limit 返 None,无 429。"""
    monkeypatch.setattr("app.services.rate_limit._RATELIMIT_ENFORCE", False)
    # 即便 limit 极低,关掉就不该拦
    monkeypatch.setattr("app.services.rate_limit._LOGIN_LIMIT_PER_MIN", 1)
    monkeypatch.setattr("app.services.rate_limit._USER_LIMIT_PER_MIN", 1)
    from app.services import rate_limit as rl
    rl.login_ip_limiter = rl.RateLimiter(1, rl._WINDOW_SECONDS)
    rl.login_user_limiter = rl.RateLimiter(1, rl._WINDOW_SECONDS)

    for _ in range(5):
        r = client_anon.post(
            "/api/auth/login", json={"username": "nosuch", "password": "wrong"},
        )
        assert r.status_code == 401  # 401 不是 429
