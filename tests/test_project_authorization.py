"""项目级资源授权契约测试 —— docs/PROJECT_AUTHORIZATION.md 的可执行版本。

构造两个互相隔离的项目 A / B + 一组全局资源，验证：
- editor / viewer 看不到 / 改不了 / 跑不了 / 下载不了对方项目的资源
- admin 不受项目限制，能看到全部
- project_id="" 的全局资源对所有登录用户可见
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from main import app


# ─── 辅助 ─────────────────────────────────────────────────────────────────────


def _login(username: str, password: str) -> TestClient:
    tc = TestClient(app)
    r = tc.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, f"login {username}: {r.status_code} {r.text}"
    tc.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    return tc


def _create_user(admin: TestClient, username: str, role: str) -> str:
    r = admin.post("/api/users", json={
        "username": username, "password": "pw1234", "role": role, "display_name": username,
    })
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _create_project(admin: TestClient, name: str, member_ids: list[str]) -> str:
    r = admin.post("/api/projects", json={"name": name, "members": member_ids})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _create_datasource(client: TestClient, name: str, project_id: str) -> str:
    r = client.post("/api/datasources", json={
        "name": name, "db_type": "MySQL", "host": "h", "port": 3306,
        "database": "d", "username": "u", "password": "p", "project_id": project_id,
    })
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _create_task(client: TestClient, name: str, ds_id: str, project_id: str) -> str:
    r = client.post("/api/tasks", json={
        "name": name, "source_id": ds_id, "target_id": ds_id,
        "source_sql": "SELECT 1 AS id", "key_columns": ["id"], "project_id": project_id,
    })
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _write_result(results_dir, run_id: str, task_id: str) -> None:
    (results_dir / f"{run_id}.json").write_text(
        json.dumps({"run_id": run_id, "task_id": task_id, "task_name": "t", "summary": {}}),
        encoding="utf-8",
    )


@pytest.fixture
def scope(client_admin, isolated_storage):
    """建 viewerA/viewerB/editorA/editorB + projectA/projectB，每项目一个
    datasource + task，再加一组全局（project_id="")资源。返回 dict。"""
    admin = client_admin
    va = _create_user(admin, "viewerA", "viewer")
    vb = _create_user(admin, "viewerB", "viewer")
    ea = _create_user(admin, "editorA", "editor")
    eb = _create_user(admin, "editorB", "editor")
    proj_a = _create_project(admin, "ProjectA", [va, ea])
    proj_b = _create_project(admin, "ProjectB", [vb, eb])

    ds_a = _create_datasource(admin, "ds-A", proj_a)
    ds_b = _create_datasource(admin, "ds-B", proj_b)
    ds_g = _create_datasource(admin, "ds-global", "")
    task_a = _create_task(admin, "task-A", ds_a, proj_a)
    task_b = _create_task(admin, "task-B", ds_b, proj_b)
    task_g = _create_task(admin, "task-global", ds_g, "")

    return {
        "admin": admin,
        "viewerA": _login("viewerA", "pw1234"),
        "viewerB": _login("viewerB", "pw1234"),
        "editorA": _login("editorA", "pw1234"),
        "editorB": _login("editorB", "pw1234"),
        "results_dir": isolated_storage["results"],
        "proj_a": proj_a, "proj_b": proj_b,
        "ds_a": ds_a, "ds_b": ds_b, "ds_g": ds_g,
        "task_a": task_a, "task_b": task_b, "task_g": task_g,
    }


# ─── 1. viewer A 看不到 viewer B 项目的 datasource ───────────────────────────


def test_viewer_cannot_see_other_project_datasource(scope):
    ids = {d["id"] for d in scope["viewerA"].get("/api/datasources").json()}
    assert scope["ds_a"] in ids        # 自己项目
    assert scope["ds_g"] in ids        # 全局资源人人可见
    assert scope["ds_b"] not in ids    # 别人项目 —— 不可见


def test_viewer_b_sees_only_own_datasource(scope):
    ids = {d["id"] for d in scope["viewerB"].get("/api/datasources").json()}
    assert scope["ds_b"] in ids
    assert scope["ds_g"] in ids
    assert scope["ds_a"] not in ids


# ─── 2. viewer A 看不到 viewer B 项目的 task ─────────────────────────────────


def test_viewer_cannot_see_other_project_task(scope):
    ids = {t["id"] for t in scope["viewerA"].get("/api/tasks").json()}
    assert scope["task_a"] in ids
    assert scope["task_g"] in ids
    assert scope["task_b"] not in ids


# ─── 3. editor A 不能运行 B 项目的 task → 403 ────────────────────────────────


def test_editor_cannot_run_other_project_task(scope):
    r = scope["editorA"].post(f"/api/tasks/{scope['task_b']}/run")
    assert r.status_code == 403


def test_editor_can_run_own_project_task(scope):
    """editorA 对自己项目的 task 已通过授权（具体跑成功与否取决于数据源连通）。"""
    r = scope["editorA"].post(f"/api/tasks/{scope['task_a']}/run")
    assert r.status_code not in (401, 403), (r.status_code, r.text)


def test_editor_cannot_delete_other_project_task(scope):
    assert scope["editorA"].delete(f"/api/tasks/{scope['task_b']}").status_code == 403


def test_editor_cannot_create_in_other_project(scope):
    r = scope["editorA"].post("/api/datasources", json={
        "name": "intruder", "db_type": "MySQL", "host": "h", "port": 3306,
        "database": "d", "username": "u", "password": "p", "project_id": scope["proj_b"],
    })
    assert r.status_code == 403


# ─── 4. admin 可以看到全部 ───────────────────────────────────────────────────


def test_admin_sees_all_resources(scope):
    ds_ids = {d["id"] for d in scope["admin"].get("/api/datasources").json()}
    assert {scope["ds_a"], scope["ds_b"], scope["ds_g"]} <= ds_ids
    task_ids = {t["id"] for t in scope["admin"].get("/api/tasks").json()}
    assert {scope["task_a"], scope["task_b"], scope["task_g"]} <= task_ids


def test_admin_can_run_any_project_task(scope):
    r = scope["admin"].post(f"/api/tasks/{scope['task_b']}/run")
    assert r.status_code not in (401, 403), (r.status_code, r.text)


# ─── 5. bootstrap 按当前用户过滤 ─────────────────────────────────────────────


def test_bootstrap_filtered_by_user(scope):
    data = scope["viewerA"].get("/api/bootstrap").json()
    ds_ids = {d["id"] for d in data["datasources"]}
    task_ids = {t["id"] for t in data["tasks"]}
    assert scope["ds_a"] in ds_ids and scope["ds_g"] in ds_ids
    assert scope["ds_b"] not in ds_ids
    assert scope["task_a"] in task_ids and scope["task_g"] in task_ids
    assert scope["task_b"] not in task_ids


def test_bootstrap_admin_sees_all(scope):
    data = scope["admin"].get("/api/bootstrap").json()
    ds_ids = {d["id"] for d in data["datasources"]}
    assert {scope["ds_a"], scope["ds_b"], scope["ds_g"]} <= ds_ids


# ─── 6. /results/* 对无权用户拒绝 ────────────────────────────────────────────


def test_results_download_rejects_unauthorized(scope):
    _write_result(scope["results_dir"], "run_projb_001", scope["task_b"])
    # viewerA 无权 projectB → 403
    assert scope["viewerA"].get("/results/run_projb_001.json").status_code == 403
    # viewerB 有权 projectB → 200
    assert scope["viewerB"].get("/results/run_projb_001.json").status_code == 200
    # admin 不受限 → 200
    assert scope["admin"].get("/results/run_projb_001.json").status_code == 200


def test_results_download_global_visible_to_all(scope):
    """全局 task（project_id="")的结果对所有登录用户可下载。"""
    _write_result(scope["results_dir"], "run_global_001", scope["task_g"])
    assert scope["viewerA"].get("/results/run_global_001.json").status_code == 200
    assert scope["viewerB"].get("/results/run_global_001.json").status_code == 200


# ─── 7. /api/history 按用户作用域过滤 ────────────────────────────────────────


def test_history_filtered_by_user(scope):
    _write_result(scope["results_dir"], "run_a_hist", scope["task_a"])
    _write_result(scope["results_dir"], "run_b_hist", scope["task_b"])

    viewer_runs = {r["run_id"] for r in scope["viewerA"].get("/api/history").json()}
    assert "run_a_hist" in viewer_runs
    assert "run_b_hist" not in viewer_runs

    admin_runs = {r["run_id"] for r in scope["admin"].get("/api/history").json()}
    assert {"run_a_hist", "run_b_hist"} <= admin_runs
