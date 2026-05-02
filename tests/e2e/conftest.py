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


@pytest.fixture
def page_with_error_capture(page, base_url):
    """page 加上"任何运行时异常都让测试失败"的兜底。

    这次踩过的 bug（template 访问 undefined.xxx 让 DetailView 整体空白）
    在 page.on('pageerror') 里能直接看到 throw 出来的 TypeError —— 比看
    UI 空白判断更早、信号更准。"""
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.on("console", lambda msg: errors.append(str(msg)) if msg.type == "error" else None)

    yield page

    if errors:
        pytest.fail(
            "浏览器抛了运行时错误 / console.error：\n" + "\n---\n".join(errors)
        )
