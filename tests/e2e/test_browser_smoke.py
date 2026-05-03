"""浏览器 smoke：catch render-time throw 让组件空白那种 bug.

Vue 运行时抛错让组件渲染失败，pytest 和 npm build 都 catch 不到。这里用
Playwright 真把 SPA 拉起来，监听 pageerror / console.error，凡有异常就 fail。

不依赖 fixture / mock —— 直接打实际 :8010 后端和 SPA。如果浏览器看到空白
组件，page_with_error_capture fixture 会捕获并失败。

跑前提：
    1. docker compose up -d  / 本地 uvicorn 跑在 :8010
    2. static/spa 已 build

Phase 1 引入 vue-router (hash mode) 之后，view 切换走 #/<path>，不再是
顶部 button click。所以这里直接 page.goto 各路由，不用模拟 click。
"""
from __future__ import annotations


def test_spa_loads_without_errors(page_with_error_capture, base_url):
    """打开 /spa 应当渲染出 #app 内容，且无 console.error / pageerror."""
    page = page_with_error_capture
    page.goto(f"{base_url}/spa")
    page.wait_for_selector("#app", timeout=10000)
    inner = page.locator("#app").inner_text(timeout=5000)
    assert inner.strip(), "#app 渲染后内容为空——SPA 完全没起来"


def test_workflow_routes_do_not_blank_out(page_with_error_capture, base_url):
    """进作业流 list / detail 路由都不应空白或抛错。

    历史 bug 复现：selectedWorkflowId 初始 'new' + currentWorkflow=undefined
    → `{{ currentWorkflow.name }}` throw → DetailView 整体空白。这里逐个
    路由打开等渲染稳定，page_with_error_capture 收 pageerror。"""
    page = page_with_error_capture
    for path in ("/workflows", "/workflows/new"):
        page.goto(f"{base_url}/spa/#{path}")
        page.wait_for_selector("#app", timeout=10000)
        page.wait_for_timeout(400)
        body_text = page.locator("body").inner_text()
        assert body_text.strip(), f"路由 {path} 渲染后页面空白"


def test_lineage_route_loads(page_with_error_capture, base_url):
    """血缘工作台 lazy-load LineageGraph chunk，进来不应抛 module 错误。"""
    page = page_with_error_capture
    page.goto(f"{base_url}/spa/#/lineage")
    page.wait_for_selector("#app", timeout=10000)
    page.wait_for_timeout(600)
    body_text = page.locator("body").inner_text()
    assert "血缘" in body_text or "lineage" in body_text.lower(), \
        f"血缘页未渲染：\n{body_text[:300]}"


def test_data_compare_route_loads(page_with_error_capture, base_url):
    """数据对比页：步骤工作台 + 任务列表。"""
    page = page_with_error_capture
    page.goto(f"{base_url}/spa/#/data-compare")
    page.wait_for_selector("#app", timeout=10000)
    page.wait_for_timeout(400)
    body_text = page.locator("body").inner_text()
    assert "数据对比" in body_text or "数据来源" in body_text, \
        f"数据对比页未渲染：\n{body_text[:300]}"


def test_history_route_loads(page_with_error_capture, base_url):
    """执行历史页：表格 sticky header 渲染（codex 修复后）。"""
    page = page_with_error_capture
    page.goto(f"{base_url}/spa/#/history")
    page.wait_for_selector("#app", timeout=10000)
    page.wait_for_timeout(400)
    body_text = page.locator("body").inner_text()
    assert "执行历史" in body_text or "运行 ID" in body_text, \
        f"执行历史页未渲染：\n{body_text[:300]}"
