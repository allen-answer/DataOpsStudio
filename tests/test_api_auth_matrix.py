"""P0.4 鉴权矩阵契约测试 —— docs/AUTHORIZATION_MATRIX.md 的可执行版本。

只覆盖关键路径上的"401 / 403 / 200"三态切换，**不重复**业务测试的功能性
覆盖（业务用例继续走各自 client fixture 拿 admin token）。

每条用例的目的：
1. 后端真的强制 auth（不靠前端 router guard）
2. role 校验真的卡 viewer 不能写 / editor 不能 admin
3. 公开 endpoint 不被 router 默认 dependency 误拦
"""
from __future__ import annotations

import pytest

from app.models import CompareTaskCreate, DataSourceCreate
from app.services.repositories import datasource_store, task_store


# ─── 辅助：建一个最小可执行的 task，供 run / preview 类用例 ───────────────────


@pytest.fixture
def seed_task(isolated_storage):
    """建一个数据源 + 任务（target_id=source_id 的同表自比对，单 SQL 模式）。

    用 SOURCE_KIND=SQL 即可（即使数据源连不上，run 在 readers 阶段才会真连）。
    """
    ds = datasource_store.create(DataSourceCreate(
        name="seed-mysql", db_type="MySQL",
        host="localhost", port=3306, database="t",
        username="u", password="p",
    ))
    task = task_store.create(CompareTaskCreate(
        name="seed-task",
        source_id=ds.id, target_id=ds.id,
        source_sql="SELECT 1 AS id",
        key_columns=["id"],
    ))
    return {"ds_id": ds.id, "task_id": task.id}


# ─── 1. 未登录访问 GET /api/datasources → 401 ──────────────────────────────


def test_anon_get_datasources_returns_401(client_anon):
    r = client_anon.get("/api/datasources")
    assert r.status_code == 401


# ─── 2. viewer 可以 GET /api/datasources ────────────────────────────────────


def test_viewer_get_datasources_returns_200(client_viewer):
    r = client_viewer.get("/api/datasources")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


# ─── 3. viewer 不能 POST /api/datasources → 403 ─────────────────────────────


def test_viewer_post_datasources_returns_403(client_viewer):
    r = client_viewer.post("/api/datasources", json={
        "name": "x", "db_type": "MySQL",
        "host": "h", "port": 3306, "database": "d",
        "username": "u", "password": "p",
    })
    assert r.status_code == 403


# ─── 4. editor 可以 POST /api/datasources → 200 ─────────────────────────────


def test_editor_post_datasources_returns_200(client_editor):
    r = client_editor.post("/api/datasources", json={
        "name": "x-editor", "db_type": "MySQL",
        "host": "h", "port": 3306, "database": "d",
        "username": "u", "password": "p",
    })
    assert r.status_code == 200
    assert r.json()["name"] == "x-editor"


# ─── 5. 未登录访问 POST /api/tasks/{id}/run → 401 ──────────────────────────


def test_anon_run_task_returns_401(client_anon, seed_task):
    r = client_anon.post(f"/api/tasks/{seed_task['task_id']}/run")
    assert r.status_code == 401


# ─── 6. viewer 不能 run task → 403 ──────────────────────────────────────────


def test_viewer_run_task_returns_403(client_viewer, seed_task):
    r = client_viewer.post(f"/api/tasks/{seed_task['task_id']}/run")
    assert r.status_code == 403


# ─── 7. editor 可以触发 run task → 不是 401/403 ─────────────────────────────


def test_editor_run_task_authorized(client_editor, seed_task):
    """editor 有权限触发 run；具体 run 是否成功取决于 datasource 能否连通。
    本测试只断言 auth 已通过（不是 401/403），不强求 200。"""
    r = client_editor.post(f"/api/tasks/{seed_task['task_id']}/run")
    assert r.status_code not in (401, 403), (r.status_code, r.text)


# ─── 8. 未登录访问 /config/export → 401 ─────────────────────────────────────


def test_anon_config_export_returns_401(client_anon):
    r = client_anon.get("/config/export")
    assert r.status_code == 401


# ─── 9. viewer 不能 /config/export → 403（admin only）─────────────────────


def test_viewer_config_export_returns_403(client_viewer):
    r = client_viewer.get("/config/export")
    assert r.status_code == 403


# ─── 10. editor 也不能 /config/export → 403（admin only）─────────────────


def test_editor_config_export_returns_403(client_editor):
    r = client_editor.get("/config/export")
    assert r.status_code == 403


# ─── 11. admin 可以 /config/export → 200 ─────────────────────────────────


def test_admin_config_export_returns_200(client_admin):
    r = client_admin.get("/config/export")
    assert r.status_code == 200
    assert r.headers.get("content-type", "").startswith("application/json")


# ─── 12. 未登录访问 POST /api/scheduler/start → 401 ────────────────────────


def test_anon_scheduler_start_returns_401(client_anon):
    r = client_anon.post("/api/scheduler/start", json={})
    assert r.status_code == 401


# ─── 13. editor 不能 /api/scheduler/start → 403 ───────────────────────────


def test_editor_scheduler_start_returns_403(client_editor):
    r = client_editor.post("/api/scheduler/start", json={})
    assert r.status_code == 403


# ─── 14. admin 可以 /api/scheduler/start → 200（操作合法即可，不限定 body）─


def test_admin_scheduler_start_authorized(client_admin):
    r = client_admin.post("/api/scheduler/start", json={})
    # admin 已通过 auth，业务层可能返 200 或 4xx（不在本测试范围）
    assert r.status_code not in (401, 403), (r.status_code, r.text)


# ─── 15. 未登录访问 /api/drivers → 200（公开 endpoint）───────────────────


def test_anon_drivers_returns_200(client_anon):
    r = client_anon.get("/api/drivers")
    assert r.status_code == 200
    assert isinstance(r.json(), dict)


# ─── 16. 未登录访问 /metrics → 200（公开 endpoint，flag in matrix doc）──


def test_anon_metrics_returns_200(client_anon):
    r = client_anon.get("/metrics")
    assert r.status_code == 200
    body = r.text
    # Prometheus text 格式 marker
    assert "http_requests_total" in body or "# HELP" in body


# ─── 额外覆盖：viewer 能读 GET /api/tasks, /api/workflows, /api/history ────


def test_viewer_get_tasks_returns_200(client_viewer):
    assert client_viewer.get("/api/tasks").status_code == 200


def test_viewer_get_workflows_returns_200(client_viewer):
    assert client_viewer.get("/api/workflows").status_code == 200


def test_viewer_get_history_returns_200(client_viewer):
    assert client_viewer.get("/api/history").status_code == 200


# ─── 额外覆盖：anon 公开 endpoint 不被 router 拦 ────────────────────────────


def test_anon_login_endpoint_accessible(client_anon):
    """登录 endpoint 必须匿名可访（不然没法登）。"""
    r = client_anon.post("/api/auth/login", json={"username": "x", "password": "y"})
    # 401 表示走到了业务校验（用户不存在 / 密码错），不是 auth gate 拦截
    assert r.status_code == 401


def test_anon_docs_accessible(client_anon):
    r = client_anon.get("/docs")
    assert r.status_code == 200


def test_anon_openapi_accessible(client_anon):
    r = client_anon.get("/openapi.json")
    assert r.status_code == 200


# ─── 额外覆盖：anon 调 /results/* / /api/bootstrap / /api/search → 401 ─


def test_anon_bootstrap_returns_401(client_anon):
    assert client_anon.get("/api/bootstrap").status_code == 401


def test_anon_results_download_returns_401(client_anon):
    assert client_anon.get("/results/x.json").status_code == 401


def test_anon_search_returns_401(client_anon):
    assert client_anon.get("/api/search?q=x").status_code == 401


# ─── viewer / editor 对 system & admin-only AI config 的边界 ──────────────


def test_viewer_ai_config_returns_403(client_viewer):
    """AI 配置是 admin only —— viewer 看不了，editor 也看不了。"""
    assert client_viewer.get("/api/lineage/ai/config").status_code == 403


def test_editor_ai_config_returns_403(client_editor):
    assert client_editor.get("/api/lineage/ai/config").status_code == 403


def test_admin_ai_config_returns_200(client_admin):
    assert client_admin.get("/api/lineage/ai/config").status_code == 200


# ─── scenarios router 全 admin only ───────────────────────────────────────


def test_anon_scenarios_list_returns_401(client_anon):
    assert client_anon.get("/api/scenarios").status_code == 401


def test_editor_scenarios_list_returns_403(client_editor):
    assert client_editor.get("/api/scenarios").status_code == 403


def test_admin_scenarios_list_returns_200(client_admin):
    assert client_admin.get("/api/scenarios").status_code == 200
