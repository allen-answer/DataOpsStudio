"""浏览器 smoke：catch 这次踩过的"render-time throw 让组件空白"那种 bug.

之前两次 bug（paramsAreReal / currentWorkflow.name）都是 Vue 运行时抛错让
组件渲染失败，pytest 和 npm build 都 catch 不到。这里用 Playwright 真把
SPA 拉起来，监听 pageerror / console.error，凡有异常就 fail。

不依赖 fixture / mock —— 直接打实际 :8010 后端和 SPA。如果浏览器看到
WorkflowDetailView 空白，这里测试也会空白；如果引发异常，page_with_error_capture
fixture 会捕获并失败。

跑前提：
    1. docker compose up -d  / 本地 uvicorn 跑在 :8010
    2. static/spa 已 build
"""
from __future__ import annotations

import pytest


def test_spa_loads_without_errors(page_with_error_capture, base_url):
    """打开 /spa 应当渲染出 #app 内容，且无 console.error / pageerror."""
    page = page_with_error_capture
    page.goto(f"{base_url}/spa")
    page.wait_for_selector("#app", timeout=10000)
    # SPA 渲染完后 #app 必有内容（DataOps Studio 标题、数据源 view 之类）
    inner = page.locator("#app").inner_text(timeout=5000)
    assert inner.strip(), "#app 渲染后内容为空——SPA 完全没起来"


def test_workflow_detail_subnav_does_not_blank_out(page_with_error_capture, base_url):
    """这次 bug 的回归测试：用户从顶部 subnav 直接点「作业流详情」tab
    （没经列表页"详情"按钮），不应该空白。

    之前 selectedWorkflowId 初始 'new' + currentWorkflow=undefined →
    `{{ currentWorkflow.name }}` throw → DetailView 整体空白。Slice C 后
    我们加了 currentWorkflow?.xxx 防御 + WorkflowView guard：点 detail 时
    库里有作业流就自动选第一个。这里钉住该行为。"""
    page = page_with_error_capture
    page.goto(f"{base_url}/spa")
    page.wait_for_selector("#app", timeout=10000)

    # 进 "作业流" view
    page.get_by_role("button", name="作业流").first.click()
    # 点 subnav 中的 "作业流详情"
    page.get_by_role("button", name="作业流详情").click()
    # detail 区应有内容（要么是真实作业流的 header / DAG / 元数据，要么是
    # 引导文案"请先从总览选择"——都不是空白）
    page.wait_for_timeout(500)   # 让 reactive 更新一帧
    body_text = page.locator("body").inner_text()
    assert ("DAG" in body_text
            or "请先从「作业流总览」中选择" in body_text
            or "新建作业流" in body_text
            or "节点配置" in body_text), \
        f"WorkflowDetailView 区域看起来空白：\n{body_text[:500]}"


def test_run_detail_subnav_with_no_run_shows_notice(page_with_error_capture, base_url):
    """没看过任何 run 的情况下点「运行详情」subnav，应当 setNotice 提示而
    不是切到一个空白 RunView。WorkflowView.goSubPage 的 guard 钉住此行为。"""
    page = page_with_error_capture
    page.goto(f"{base_url}/spa")
    page.wait_for_selector("#app", timeout=10000)
    page.get_by_role("button", name="作业流").first.click()

    page.get_by_role("button", name="运行详情").click()
    # 不应当切到一个无内容的 run 详情页 —— guard 通过 setNotice 提示后停在
    # 当前页（list 页），所以页面里应仍能看到 list 页的元素。
    page.wait_for_timeout(500)
    body_text = page.locator("body").inner_text()
    assert "运行详情" in body_text   # subnav 还在
    # 如果错误地切走了，list 页特征就消失了
    assert ("作业流总览" in body_text), \
        f"点运行详情没 guard 住——可能切到空白页了：\n{body_text[:500]}"
