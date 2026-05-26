"""Wave 1:生产模式安全加固单测,覆盖 deep-research 报告 #8 / #9 / #10 / #11。

设计:
- #8 / #9 的 fail-fast 涉及模块顶层 RuntimeError,用 subprocess 完全隔离环境,
  避免污染同进程 sys.modules(`app/api/auth.py` import 了 `from app.services.auth import ...`,
  pop services.auth 会让 api.auth 持有 stale 引用)。
- #10 / #11 是普通 env-aware 端点行为,in-process 跑即可。
"""
from __future__ import annotations

import subprocess
import sys
import textwrap


# ─── #8: JWT_SECRET 生产 fail-fast ──────────────────────────────────────────

def _run_python(code: str, env: dict[str, str]) -> subprocess.CompletedProcess:
    """干净子进程跑 code,完全控制 env。返回 CompletedProcess。"""
    import os
    full_env = {**os.environ, **env}
    # 清掉测试不需要的 secret env(避免 host 跟 CI 不一致)
    for k in ["DATAOPS_JWT_SECRET", "DATAOPS_ENV", "DATAOPS_BOOTSTRAP_ADMIN_ONCE",
              "DATAOPS_ADMIN_PASSWORD"]:
        if k not in env:
            full_env.pop(k, None)
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, env=full_env,
        encoding="utf-8", errors="replace",
        cwd=".",
    )


def test_jwt_secret_required_in_prod():
    r = _run_python(
        "import app.services.auth",
        env={"DATAOPS_ENV": "prod"},
    )
    assert r.returncode != 0
    assert "DATAOPS_JWT_SECRET is required in production" in (r.stderr or "")


def test_jwt_secret_dev_fallback_still_works():
    r = _run_python(
        "import app.services.auth as a; "
        "assert a.JWT_SECRET == 'dev-only-jwt-secret-change-me-in-prod'; "
        "assert a.IS_PROD is False; "
        "print('OK')",
        env={},  # 无 prod env
    )
    assert r.returncode == 0, r.stderr
    assert "OK" in r.stdout


def test_jwt_secret_prod_with_explicit_secret_ok():
    r = _run_python(
        "import app.services.auth as a; "
        "assert a.IS_PROD is True; "
        "assert a.JWT_SECRET == 'x' * 64; "
        "print('OK')",
        env={"DATAOPS_ENV": "production", "DATAOPS_JWT_SECRET": "x" * 64},
    )
    assert r.returncode == 0, r.stderr
    assert "OK" in r.stdout


# ─── #9: admin 自举生产硬规则 ─────────────────────────────────────────────────

_BOOTSTRAP_SCRIPT = textwrap.dedent("""
    import sys, tempfile, pathlib
    tmp = pathlib.Path(tempfile.mkdtemp()) / 'users.json'
    import app.services.auth as a
    a.user_store.path = tmp
    a.user_store.invalidate_cache()
    try:
        a.bootstrap_default_admin()
        users = a.user_store.list()
        if not users:
            print('NO_USERS')
            sys.exit(2)
        # 输出: <username>|<password_ok>
        pwd = '__PASSWORD__'
        ok = a.verify_password(pwd, users[0].password_hash)
        print(f'{users[0].username}|{ok}')
    except RuntimeError as e:
        print(f'RUNTIME_ERROR: {e}', file=sys.stderr)
        sys.exit(3)
""")


def test_bootstrap_prod_without_explicit_flag_refused():
    r = _run_python(
        _BOOTSTRAP_SCRIPT.replace("__PASSWORD__", "ignored"),
        env={"DATAOPS_ENV": "prod", "DATAOPS_JWT_SECRET": "x" * 64},
    )
    assert r.returncode == 3, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert "Refusing to auto-bootstrap admin in production" in (r.stderr or "")


def test_bootstrap_prod_with_flag_but_no_password_refused():
    r = _run_python(
        _BOOTSTRAP_SCRIPT.replace("__PASSWORD__", "ignored"),
        env={
            "DATAOPS_ENV": "prod",
            "DATAOPS_JWT_SECRET": "x" * 64,
            "DATAOPS_BOOTSTRAP_ADMIN_ONCE": "true",
        },
    )
    assert r.returncode == 3
    assert "DATAOPS_ADMIN_PASSWORD is required" in (r.stderr or "")


def test_bootstrap_prod_with_explicit_creds_creates_admin():
    r = _run_python(
        _BOOTSTRAP_SCRIPT.replace("__PASSWORD__", "StrongProd!2026"),
        env={
            "DATAOPS_ENV": "prod",
            "DATAOPS_JWT_SECRET": "x" * 64,
            "DATAOPS_BOOTSTRAP_ADMIN_ONCE": "true",
            "DATAOPS_ADMIN_PASSWORD": "StrongProd!2026",
        },
    )
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert "admin|True" in r.stdout


def test_bootstrap_dev_default_admin_admin():
    r = _run_python(
        _BOOTSTRAP_SCRIPT.replace("__PASSWORD__", "admin"),
        env={},  # 无 prod
    )
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert "admin|True" in r.stdout


# ─── #10: refresh_token 默认不返 body ─────────────────────────────────────────

def test_refresh_for_body_default_empty(monkeypatch):
    monkeypatch.delenv("DATAOPS_RETURN_REFRESH_TOKEN_IN_BODY", raising=False)
    import app.api.auth as auth_api
    refresh, ttl = auth_api._refresh_for_body("real-token", 3600)
    assert refresh == ""
    assert ttl == 0


def test_refresh_for_body_env_opt_in(monkeypatch):
    monkeypatch.setenv("DATAOPS_RETURN_REFRESH_TOKEN_IN_BODY", "true")
    import app.api.auth as auth_api
    refresh, ttl = auth_api._refresh_for_body("real-token", 3600)
    assert refresh == "real-token"
    assert ttl == 3600


def test_login_e2e_cookie_present_body_empty(client_anon, monkeypatch):
    """登录成功后 cookie 必有 token,body 内 refresh_token 为空。"""
    monkeypatch.delenv("DATAOPS_RETURN_REFRESH_TOKEN_IN_BODY", raising=False)
    r = client_anon.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["access_token"]
    # body 默认不含 refresh_token
    assert body.get("refresh_token") == ""
    assert body.get("refresh_expires_in") == 0
    # cookie 应有
    assert "dataops_refresh" in r.cookies


# ─── #11: /results 老路径默认 410 ──────────────────────────────────────────

def test_legacy_results_path_returns_410_by_default(client, monkeypatch):
    monkeypatch.delenv("DATAOPS_DISABLE_LEGACY_RESULT_PATH", raising=False)
    r = client.get("/results/anything.json")
    assert r.status_code == 410


def test_legacy_results_path_can_be_reenabled_by_env(client, monkeypatch):
    monkeypatch.setenv("DATAOPS_DISABLE_LEGACY_RESULT_PATH", "false")
    r = client.get("/results/nonexistent-file.json")
    assert r.status_code == 404
