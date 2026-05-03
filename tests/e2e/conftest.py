"""e2e (Playwright 浏览器) 测试 fixture。

跑法：
    pip install playwright pytest-playwright
    playwright install chromium
    # 后端必须已经在跑（docker compose up -d 或本地 uvicorn）
    pytest tests/e2e/

环境变量：
    E2E_BASE_URL  浏览器要去的根 URL，默认 http://localhost:8010

默认 pytest 不收集 tests/e2e/（看仓库根 conftest.py 的 collect_ignore）—
只有显式指定 `pytest tests/e2e/` 才跑。这样 unit / integration 测试不会
依赖浏览器和 chromium 二进制。
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session")
def base_url() -> str:
    return os.environ.get("E2E_BASE_URL", "http://localhost:8010")


@pytest.fixture(scope="session")
def browser_type_launch_args():
    """禁用 chromium 自动 proxy 探测。docker 容器在用户机器上经常被 daemon
    注入 HTTP_PROXY，chromium 把 docker 内部 hostname `app:8010` 也走 host
    proxy → SSL_PROTOCOL_ERROR。--no-proxy-server 显式 bypass 所有 proxy。"""
    return {"args": ["--no-proxy-server", "--no-sandbox"]}


@pytest.fixture(scope="session")
def admin_token(base_url) -> str:
    """D-MVP 启用了 auth gate —— 所有 protected 路由都需要 token。
    e2e 测试用 admin/admin 登录拿一次 token，session 内复用。"""
    import urllib.request
    import json
    req = urllib.request.Request(
        f"{base_url}/api/auth/login",
        data=json.dumps({"username": "admin", "password": "admin"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    return data["access_token"]


@pytest.fixture
def page_with_error_capture(page, base_url, admin_token):
    """page 加上 "任何 uncaught 运行时异常都让测试失败" 的兜底。

    踩过的 bug（template 访问 undefined.xxx 让 DetailView 整体空白）在
    page.on('pageerror') 里能直接看到 throw 出来的 TypeError。

    D-MVP 后：page 第一次 goto 前注入 admin token 到 localStorage，
    auth gate 直接放行 —— 否则所有路由都被守卫跳到 /login。"""
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))

    # 在 origin 上预设 localStorage —— 必须先 navigate 到一个非空白页才能
    # 写 localStorage。打开 /spa/ 根，然后写 token，下一次 goto 就带 token。
    page.goto(f"{base_url}/spa/")
    page.evaluate(
        "(token) => { localStorage.setItem('dataops.token', token); "
        "localStorage.setItem('dataops.user', JSON.stringify({id:'admin',username:'admin',role:'admin',display_name:'admin'})); }",
        admin_token,
    )

    yield page

    if errors:
        pytest.fail(
            "浏览器抛了 uncaught 运行时异常：\n" + "\n---\n".join(errors)
        )
