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


@pytest.fixture
def page_with_error_capture(page, base_url):
    """page 加上 "任何 uncaught 运行时异常都让测试失败" 的兜底。

    踩过的 bug（template 访问 undefined.xxx 让 DetailView 整体空白）在
    page.on('pageerror') 里能直接看到 throw 出来的 TypeError —— 比看 UI
    空白判断更早、信号更准。

    只收 pageerror（真正的 uncaught throw），不收 console.error：mount 时
    短暂的 fetch race / 网络 transient 错经常打到 console.error，但不是
    渲染层 bug，会误报。"""
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))

    yield page

    if errors:
        pytest.fail(
            "浏览器抛了 uncaught 运行时异常：\n" + "\n---\n".join(errors)
        )
