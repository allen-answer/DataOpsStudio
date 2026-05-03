"""auth gate 前端 e2e。

不复用 page_with_error_capture（它会自动注入 token）—— 这里要测无 token 的
原始体验：守卫跳 login + 表单提交 + 注销。
"""
from __future__ import annotations


def test_unauthenticated_redirects_to_login(page, base_url):
    """无 token 访问 /datasources，守卫应跳 /login?redirect=..."""
    # 先把 localStorage 清干净（pytest-playwright 给的 page 是新 context）
    page.goto(f"{base_url}/spa/")
    page.evaluate("() => localStorage.clear()")
    page.goto(f"{base_url}/spa/#/datasources")
    page.wait_for_selector("#app", timeout=10000)
    page.wait_for_timeout(400)
    # hash 现在应该是 #/login（带 redirect query）
    assert "/login" in page.url, f"未守卫到 /login: {page.url}"
    body = page.locator("body").inner_text()
    assert "登录" in body or "DataOps Studio" in body


def test_login_form_submits_and_navigates(page, base_url):
    page.goto(f"{base_url}/spa/")
    page.evaluate("() => localStorage.clear()")
    page.goto(f"{base_url}/spa/#/login")
    page.wait_for_selector("input[autocomplete='username']", timeout=10000)
    page.fill("input[autocomplete='username']", "admin")
    page.fill("input[autocomplete='current-password']", "admin")
    page.click("button[type='submit']")
    page.wait_for_timeout(1000)
    # 登录成功 → 跳到 /datasources（默认 redirect target）
    assert "/datasources" in page.url, f"登录后未跳转: {page.url}"
    # localStorage 应保存 token
    has_token = page.evaluate("() => !!localStorage.getItem('dataops.token')")
    assert has_token, "登录成功后 localStorage.dataops.token 应存在"


def test_login_wrong_password_shows_error(page, base_url):
    page.goto(f"{base_url}/spa/")
    page.evaluate("() => localStorage.clear()")
    page.goto(f"{base_url}/spa/#/login")
    page.wait_for_selector("input[autocomplete='username']", timeout=10000)
    page.fill("input[autocomplete='username']", "admin")
    page.fill("input[autocomplete='current-password']", "WRONG-pwd")
    page.click("button[type='submit']")
    page.wait_for_timeout(800)
    body = page.locator("body").inner_text()
    assert "用户名或密码错误" in body or "登录失败" in body


def test_logout_clears_token_and_redirects(page, base_url, admin_token):
    """注销按钮点击后 token 清掉 + 跳回 /login。"""
    # 预设 token 进 protected 页
    page.goto(f"{base_url}/spa/")
    page.evaluate(
        "(t) => { localStorage.setItem('dataops.token', t); "
        "localStorage.setItem('dataops.user', JSON.stringify({id:'a',username:'admin',role:'admin'})); }",
        admin_token,
    )
    page.goto(f"{base_url}/spa/#/datasources")
    page.wait_for_selector("#app", timeout=10000)
    page.wait_for_timeout(500)

    # 打开 TopBar 用户菜单 + 注销
    # 用户头像按钮的 title 含用户名，先把菜单展开 —— 直接点带 admin 字样的按钮
    page.get_by_title("admin (admin)").click()
    page.wait_for_timeout(300)
    page.get_by_role("button", name="注销").click()
    page.wait_for_timeout(500)

    # 应跳到 /login，token 也清了
    assert "/login" in page.url, f"注销后未跳转 login: {page.url}"
    has_token = page.evaluate("() => !!localStorage.getItem('dataops.token')")
    assert not has_token, "注销后 localStorage.dataops.token 应被清"
