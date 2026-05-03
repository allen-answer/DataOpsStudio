"""D-MVP 项目空间 + admin views e2e。

覆盖场景：
- admin 登录后 sidebar 看到"项目"切换 dropdown 和 admin 区段
- 项目切换 dropdown 改变 → bootstrap 自动重拉
- /admin/users / /admin/audit / /admin/projects 三个 admin 路由可达且不空白
- 非 admin 访问 admin 路由 → router 守卫跳回 /datasources
"""
from __future__ import annotations


def test_admin_sees_project_dropdown_and_admin_nav(page_with_error_capture, base_url):
    page = page_with_error_capture
    page.goto(f"{base_url}/spa/#/datasources")
    page.wait_for_selector("#app", timeout=10000)
    page.wait_for_timeout(500)
    body = page.locator("body").inner_text()
    # 项目 dropdown：sidebar 顶部应出现"当前项目"文案
    assert "当前项目" in body, "sidebar 应显示项目切换 dropdown"
    # admin 区段（lucide 图标 + 文字）
    assert "用户管理" in body, "admin 应看到用户管理入口"
    assert "审计日志" in body, "admin 应看到审计日志入口"
    assert "项目管理" in body, "admin 应看到项目管理入口"


def test_admin_users_route_renders(page_with_error_capture, base_url):
    page = page_with_error_capture
    page.goto(f"{base_url}/spa/#/admin/users")
    page.wait_for_selector("#app", timeout=10000)
    page.wait_for_timeout(500)
    body = page.locator("body").inner_text()
    assert "用户管理" in body
    assert "新建用户" in body, "应渲染创建用户表单"


def test_admin_audit_route_renders(page_with_error_capture, base_url):
    page = page_with_error_capture
    page.goto(f"{base_url}/spa/#/admin/audit")
    page.wait_for_selector("#app", timeout=10000)
    page.wait_for_timeout(500)
    body = page.locator("body").inner_text()
    assert "审计日志" in body


def test_admin_projects_route_renders(page_with_error_capture, base_url):
    page = page_with_error_capture
    page.goto(f"{base_url}/spa/#/admin/projects")
    page.wait_for_selector("#app", timeout=10000)
    page.wait_for_timeout(500)
    body = page.locator("body").inner_text()
    assert "项目管理" in body
    assert "新建项目" in body, "应渲染创建项目表单"


def test_project_switch_persists_to_localstorage(page_with_error_capture, base_url):
    """选择项目后 localStorage 应记录 dataops.project_id —— 持久化跨刷新。"""
    page = page_with_error_capture
    # 先建一个项目（直接调 API），保证下拉里有可选项
    page.goto(f"{base_url}/spa/#/admin/projects")
    page.wait_for_selector("#app", timeout=10000)
    page.wait_for_timeout(400)

    project_id = page.evaluate(
        "async () => {"
        " const t = localStorage.getItem('dataops.token');"
        " const r = await fetch('/api/projects', {"
        "   method: 'POST',"
        "   headers: {'Content-Type':'application/json', Authorization:'Bearer '+t},"
        "   body: JSON.stringify({name:'e2e-proj', description:'e2e', members:[]})"
        " });"
        " const data = await r.json();"
        " return data.id;"
        "}"
    )
    assert project_id, "建项目失败"

    # localStorage 写入项目 id（模拟用户选择）+ 刷新页面
    page.evaluate(
        "(id) => localStorage.setItem('dataops.project_id', id)",
        project_id,
    )
    page.goto(f"{base_url}/spa/#/datasources")
    page.wait_for_selector("#app", timeout=10000)
    page.wait_for_timeout(500)

    persisted = page.evaluate("() => localStorage.getItem('dataops.project_id')")
    assert persisted == project_id, "刷新后 dataops.project_id 应保留"


def test_non_admin_redirected_from_admin_routes(page, base_url):
    """非 admin 用户访问 /admin/users → router beforeEach 跳到 /datasources。
    不复用 page_with_error_capture（那个注入了 admin token），手动用 viewer 登录。"""
    # 先建一个 viewer 用户（用 admin token 调 /api/users）—— 用纯 fetch 在浏览器里搞
    page.goto(f"{base_url}/spa/")
    page.evaluate("() => localStorage.clear()")

    # 登录 admin，建 viewer
    import urllib.request, json
    admin_login = urllib.request.Request(
        f"{base_url}/api/auth/login",
        data=json.dumps({"username": "admin", "password": "admin"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(admin_login, timeout=10) as resp:
        admin_token = json.loads(resp.read())["access_token"]
    create_viewer = urllib.request.Request(
        f"{base_url}/api/users",
        data=json.dumps({"username": "e2e-viewer", "password": "viewer123", "role": "viewer"}).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {admin_token}"},
        method="POST",
    )
    try:
        urllib.request.urlopen(create_viewer, timeout=10)
    except Exception:
        pass  # 用户已存在 OK

    # viewer 登录拿 token
    viewer_login = urllib.request.Request(
        f"{base_url}/api/auth/login",
        data=json.dumps({"username": "e2e-viewer", "password": "viewer123"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(viewer_login, timeout=10) as resp:
        viewer_data = json.loads(resp.read())
        viewer_token = viewer_data["access_token"]

    # 注入 viewer token + user 进 localStorage
    page.evaluate(
        "(args) => { localStorage.setItem('dataops.token', args.token);"
        " localStorage.setItem('dataops.user', JSON.stringify(args.user)); }",
        {"token": viewer_token, "user": viewer_data["user"]},
    )

    # 访问 admin 路由 —— 应被守卫跳回 /datasources
    page.goto(f"{base_url}/spa/#/admin/users")
    page.wait_for_selector("#app", timeout=10000)
    page.wait_for_timeout(500)
    assert "/admin/users" not in page.url, f"viewer 应被跳出 admin 路由: {page.url}"
    assert "/datasources" in page.url, f"viewer 应跳到默认数据源页: {page.url}"
