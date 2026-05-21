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


# ─── 8. 引用资源授权：task 引用的 datasource ─────────────────────────────────
# 外壳（task）的项目权限过了，但 task 引用的 datasource 不能跨到无权项目。


def _task_payload(name, ds_id, project_id):
    return {
        "name": name, "source_id": ds_id, "target_id": ds_id,
        "source_sql": "SELECT 1 AS id", "key_columns": ["id"], "project_id": project_id,
    }


def test_editor_cannot_create_task_referencing_other_project_datasource(scope):
    r = scope["editorA"].post("/api/tasks", json=_task_payload("cross-ds", scope["ds_b"], scope["proj_a"]))
    assert r.status_code == 403


def test_editor_cannot_update_task_to_other_project_datasource(scope):
    # task_a 在 ProjectA，editorA 有权改它；但不能把它的数据源换成 ProjectB 的
    r = scope["editorA"].put(
        f"/api/tasks/{scope['task_a']}",
        json=_task_payload("task-A", scope["ds_b"], scope["proj_a"]),
    )
    assert r.status_code == 403


def test_editor_can_create_task_referencing_own_project_datasource(scope):
    r = scope["editorA"].post("/api/tasks", json=_task_payload("own-ds", scope["ds_a"], scope["proj_a"]))
    assert r.status_code == 200


def test_editor_can_create_task_referencing_global_datasource(scope):
    r = scope["editorA"].post("/api/tasks", json=_task_payload("global-ds", scope["ds_g"], scope["proj_a"]))
    assert r.status_code == 200


# ─── 9. 引用资源授权：workflow compare 节点引用的 task ───────────────────────


def _workflow_payload(name, task_id, project_id):
    return {
        "name": name, "project_id": project_id,
        "nodes": [{"id": "n1", "type": "compare", "config": {"task_id": task_id}}],
    }


def test_editor_cannot_create_workflow_referencing_other_project_task(scope):
    r = scope["editorA"].post(
        "/api/workflows", json=_workflow_payload("wf-cross", scope["task_b"], scope["proj_a"]),
    )
    assert r.status_code == 403


def test_editor_can_create_workflow_referencing_own_project_task(scope):
    r = scope["editorA"].post(
        "/api/workflows", json=_workflow_payload("wf-own", scope["task_a"], scope["proj_a"]),
    )
    assert r.status_code == 200


def test_editor_can_create_workflow_referencing_global_task(scope):
    r = scope["editorA"].post(
        "/api/workflows", json=_workflow_payload("wf-global", scope["task_g"], scope["proj_a"]),
    )
    assert r.status_code == 200


# ─── 10. config import 收紧为 admin only ─────────────────────────────────────


def test_config_import_requires_admin(scope):
    files = {"config_file": ("config.json", b'{"datasources": [], "tasks": []}', "application/json")}
    assert scope["viewerA"].post("/config/import", files=files).status_code == 403
    assert scope["editorA"].post("/config/import", files=files).status_code == 403
    # admin 能用 —— 通过 auth gate（import 成功走 303 重定向）
    r = scope["admin"].post("/config/import", files=files, follow_redirects=False)
    assert r.status_code not in (401, 403), (r.status_code, r.text)


# ─── 11. 直接 datasource_id 接口授权 ─────────────────────────────────────────
# 凡是直接接收 datasource_id 并执行 SQL / EXPLAIN / 字段预览 / introspect 的
# endpoint，都必须调 require_datasource_access。这一节覆盖 5 个入口：
# /api/preview/rows、/api/preview/columns、/api/tasks/{id}/preview override、
# /api/slow-sql/analyze、/api/assets/introspect/{name}。


@pytest.fixture
def stub_db(monkeypatch):
    """所有命中 db 的执行点 monkeypatch 成空结果，让授权校验是测试的唯一变量。"""
    from app.api import uploads as uploads_api
    from app.api import tasks as tasks_api
    from app.api import slow_sql as slow_sql_api
    from app.services import datasource_introspect as di

    monkeypatch.setattr(uploads_api, "fetch_rows", lambda *a, **k: [])

    class _Details:
        columns = []
        warnings = []

    monkeypatch.setattr(uploads_api, "fetch_column_details", lambda *a, **k: _Details())

    class _RowsResult:
        columns = []
        rows = []
        warnings = []

    monkeypatch.setattr(tasks_api, "fetch_rows_with_schema", lambda *a, **k: _RowsResult())

    # api 模块在 import 时把 analyze_sql 绑到自己命名空间，必须改 api 模块的引用
    monkeypatch.setattr(
        slow_sql_api, "analyze_sql",
        lambda *a, **k: {"plan": [], "issues": [], "suggestions": []},
    )
    monkeypatch.setattr(di, "introspect_columns", lambda *a, **k: [])


# /api/preview/rows -----------------------------------------------------------


def test_preview_rows_editor_cannot_use_other_project_datasource(scope, stub_db):
    r = scope["editorA"].post("/api/preview/rows", json={
        "kind": "sql", "datasource_id": scope["ds_b"], "sql": "SELECT 1",
    })
    assert r.status_code == 403, r.text


def test_preview_rows_editor_can_use_own_project_datasource(scope, stub_db):
    r = scope["editorA"].post("/api/preview/rows", json={
        "kind": "sql", "datasource_id": scope["ds_a"], "sql": "SELECT 1",
    })
    assert r.status_code == 200, r.text


def test_preview_rows_editor_can_use_global_datasource(scope, stub_db):
    r = scope["editorA"].post("/api/preview/rows", json={
        "kind": "sql", "datasource_id": scope["ds_g"], "sql": "SELECT 1",
    })
    assert r.status_code == 200, r.text


# /api/preview/columns --------------------------------------------------------


def test_preview_columns_editor_cannot_use_other_project_datasource(scope, stub_db):
    r = scope["editorA"].post("/api/preview/columns", json={
        "kind": "sql", "datasource_id": scope["ds_b"], "sql": "SELECT 1",
    })
    assert r.status_code == 403, r.text


def test_preview_columns_editor_can_use_own_project_datasource(scope, stub_db):
    r = scope["editorA"].post("/api/preview/columns", json={
        "kind": "sql", "datasource_id": scope["ds_a"], "sql": "SELECT 1",
    })
    assert r.status_code == 200, r.text


# /api/tasks/{id}/preview override_datasource_id ------------------------------


def test_task_preview_editor_cannot_override_to_other_project_datasource(scope, stub_db):
    """editorA 对 task_a（自己项目）已通过授权，但不能把 override 指向 ProjectB ds。"""
    r = scope["editorA"].post(
        f"/api/tasks/{scope['task_a']}/preview",
        json={"side": "source", "datasource_id": scope["ds_b"], "sql": "SELECT 1"},
    )
    assert r.status_code == 403, r.text


def test_task_preview_editor_no_override_still_works(scope, stub_db):
    """非 override 路径 —— editorA 对自己项目 task 仍能正常预览。"""
    r = scope["editorA"].post(
        f"/api/tasks/{scope['task_a']}/preview",
        json={"side": "source"},
    )
    assert r.status_code == 200, r.text


# /api/slow-sql/analyze -------------------------------------------------------


def test_slow_sql_editor_cannot_analyze_other_project_datasource(scope, stub_db):
    r = scope["editorA"].post("/api/slow-sql/analyze", json={
        "sql": "SELECT 1", "datasource_id": scope["ds_b"],
    })
    assert r.status_code == 403, r.text


def test_slow_sql_editor_can_analyze_own_project_datasource(scope, stub_db):
    r = scope["editorA"].post("/api/slow-sql/analyze", json={
        "sql": "SELECT 1", "datasource_id": scope["ds_a"],
    })
    assert r.status_code == 200, r.text


def test_slow_sql_viewer_blocked_by_role(scope, stub_db):
    """slow-sql 是 editor 角色门槛 —— viewer 即便对项目有权也走不到授权层。"""
    r = scope["viewerA"].post("/api/slow-sql/analyze", json={
        "sql": "SELECT 1", "datasource_id": scope["ds_a"],
    })
    assert r.status_code == 403


# /api/assets/introspect/{name} ----------------------------------------------


def test_introspect_editor_cannot_introspect_other_project_datasource(scope, stub_db):
    r = scope["editorA"].get(
        f"/api/assets/introspect/ods.t1?datasource_id={scope['ds_b']}",
    )
    assert r.status_code == 403, r.text


def test_introspect_editor_can_introspect_own_project_datasource(scope, stub_db):
    r = scope["editorA"].get(
        f"/api/assets/introspect/ods.t1?datasource_id={scope['ds_a']}",
    )
    assert r.status_code == 200, r.text


def test_introspect_editor_can_introspect_global_datasource(scope, stub_db):
    r = scope["editorA"].get(
        f"/api/assets/introspect/ods.t1?datasource_id={scope['ds_g']}",
    )
    assert r.status_code == 200, r.text


# admin 全权（汇总） ----------------------------------------------------------


def test_admin_can_access_any_datasource_via_direct_endpoints(scope, stub_db):
    for ds in (scope["ds_a"], scope["ds_b"], scope["ds_g"]):
        r = scope["admin"].post(
            "/api/preview/rows",
            json={"kind": "sql", "datasource_id": ds, "sql": "SELECT 1"},
        )
        assert r.status_code == 200, f"preview/rows ds={ds}: {r.text}"

        r = scope["admin"].post(
            "/api/slow-sql/analyze",
            json={"sql": "SELECT 1", "datasource_id": ds},
        )
        assert r.status_code == 200, f"slow-sql/analyze ds={ds}: {r.text}"

        r = scope["admin"].get(
            f"/api/assets/introspect/ods.t1?datasource_id={ds}",
        )
        assert r.status_code == 200, f"introspect ds={ds}: {r.text}"


# 404 / 403 区分 -------------------------------------------------------------


def test_direct_endpoints_404_on_unknown_datasource(scope, stub_db):
    """admin 也好、editor 也好，datasource_id 不存在统一 404，不暴露 403/404 差别。"""
    r = scope["admin"].post(
        "/api/preview/rows",
        json={"kind": "sql", "datasource_id": "ghost-id", "sql": "SELECT 1"},
    )
    assert r.status_code == 404, r.text
    r = scope["editorA"].post(
        "/api/slow-sql/analyze",
        json={"sql": "SELECT 1", "datasource_id": "ghost-id"},
    )
    assert r.status_code == 404, r.text


# ─── 12. P1 阻塞：parquet run 项目级授权（compare_result_project_id + result_download_project_id） ───
# 切片 B/C/E 落地后，parquet runs 目录形态（results/<run_id>/{meta.json, *.parquet}）
# 的归属反查路径不能只支持 legacy <run_id>.json。下列测试同时验证：
# - /api/runs/<id>/meta + /buckets + /export-excel 三个 API 端点（_check_run_project_access 路径）
# - /results/<run_id>/meta.json + /results/<run_id>/<bucket>.parquet 直链下载（result_download_project_id 路径）


def _build_parquet_run_under_task(results_dir, task_id: str, run_id: str) -> None:
    """在指定 task 下建一个真 parquet run，落 meta.json + parquet 文件 +
    legacy json 同名 stub 不写（专测 parquet 路径，验证 detect_format 走目录）。"""
    from app.compare.result_writer import ParquetResultWriter, feed_buckets

    bs = {
        "only_source": [{"key": [1], "source": {"id": 1, "name": "a"}}],
        "only_target": [],
        "diff": [
            {"key": [2], "source": {"id": 2, "v": 1}, "target": {"id": 2, "v": 2},
             "changes": {"v": {"source": 1, "target": 2, "target_column": "v"}}},
        ],
        "same": [{"key": [3], "source": {"id": 3}, "target": {"id": 3}}],
    }
    envelope = {
        "run_id": run_id, "task_id": task_id, "task_name": "p1-fixture",
        "started_at": "2026-05-21T10:00:00", "elapsed_seconds": 1.0,
        "source_rows": 2, "target_rows": 2,
        "summary": {"only_source": 1, "only_target": 0, "diff": 1, "same": 1},
        "rules": {}, "limits": {"export_max_rows": 50000}, "schema_report": {},
    }
    writer = ParquetResultWriter(
        run_dir=results_dir / run_id,
        excel_path=results_dir / f"{run_id}.xlsx",
        payload=envelope,
    )
    feed_buckets(writer, bs)
    writer.finalize()


@pytest.fixture
def parquet_run_in_proj_b(scope):
    """在 ProjectB 的 task_b 下落一个 parquet run。返回 run_id。
    editorA 应该全部 403，editorB 应该全部 200。"""
    run_id = "PARQ_RUN_B_001"
    _build_parquet_run_under_task(scope["results_dir"], scope["task_b"], run_id)
    return run_id


@pytest.fixture
def parquet_run_in_proj_a(scope):
    """对照组：ProjectA 的 task_a 下的 parquet run。editorA 应该 200。"""
    run_id = "PARQ_RUN_A_001"
    _build_parquet_run_under_task(scope["results_dir"], scope["task_a"], run_id)
    return run_id


# /api/runs/<id>/meta -------------------------------------------------------


def test_p1_parquet_meta_editorA_blocked_for_proj_b(scope, parquet_run_in_proj_b):
    r = scope["editorA"].get(f"/api/runs/{parquet_run_in_proj_b}/meta")
    assert r.status_code == 403, r.text


def test_p1_parquet_meta_editorB_can_read_own(scope, parquet_run_in_proj_b):
    r = scope["editorB"].get(f"/api/runs/{parquet_run_in_proj_b}/meta")
    assert r.status_code == 200, r.text
    assert r.json()["format"] == "parquet"


# /api/runs/<id>/buckets/<bucket> -------------------------------------------


def test_p1_parquet_bucket_editorA_blocked(scope, parquet_run_in_proj_b):
    r = scope["editorA"].get(f"/api/runs/{parquet_run_in_proj_b}/buckets/diff")
    assert r.status_code == 403, r.text


def test_p1_parquet_bucket_editorB_can_read(scope, parquet_run_in_proj_b):
    r = scope["editorB"].get(f"/api/runs/{parquet_run_in_proj_b}/buckets/diff")
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 1


# POST /api/runs/<id>/export-excel ------------------------------------------


def test_p1_parquet_export_excel_editorA_blocked(scope, parquet_run_in_proj_b):
    r = scope["editorA"].post(f"/api/runs/{parquet_run_in_proj_b}/export-excel")
    assert r.status_code == 403, r.text


def test_p1_parquet_export_excel_editorB_can_trigger(scope, parquet_run_in_proj_b):
    r = scope["editorB"].post(f"/api/runs/{parquet_run_in_proj_b}/export-excel")
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "excel_export"


# GET /results/<run_id>/* 直链下载 -------------------------------------------
# 这是 P1 阻塞最容易漏的部分：parquet 模式下用户/前端可能直链 /results/<id>/meta.json
# 拿 envelope，或者直链 /results/<id>/diff.parquet 给 pandas/duckdb 离线分析。
# result_download_project_id 必须能识别 <run_id>/<file> 模式反查归属项目。


def test_p1_results_meta_json_editorA_blocked(scope, parquet_run_in_proj_b):
    r = scope["editorA"].get(f"/results/{parquet_run_in_proj_b}/meta.json")
    assert r.status_code == 403, r.text


def test_p1_results_meta_json_editorB_can_download(scope, parquet_run_in_proj_b):
    r = scope["editorB"].get(f"/results/{parquet_run_in_proj_b}/meta.json")
    assert r.status_code == 200, r.text


def test_p1_results_parquet_file_editorA_blocked(scope, parquet_run_in_proj_b):
    r = scope["editorA"].get(f"/results/{parquet_run_in_proj_b}/diff.parquet")
    assert r.status_code == 403, r.text


def test_p1_results_parquet_file_editorB_can_download(scope, parquet_run_in_proj_b):
    r = scope["editorB"].get(f"/results/{parquet_run_in_proj_b}/diff.parquet")
    assert r.status_code == 200, r.text


# ProjectA run 同 user 正路径（防回归：editorA 对自己项目仍 200） ------------


def test_p1_parquet_own_project_full_access(scope, parquet_run_in_proj_a):
    """editorA 对自己 ProjectA 的 parquet run 应能读 meta / bucket / 直链 meta.json。"""
    assert scope["editorA"].get(f"/api/runs/{parquet_run_in_proj_a}/meta").status_code == 200
    assert scope["editorA"].get(f"/api/runs/{parquet_run_in_proj_a}/buckets/only_source").status_code == 200
    assert scope["editorA"].get(f"/results/{parquet_run_in_proj_a}/meta.json").status_code == 200


# legacy json run 回归（保 PR2 的兼容） -------------------------------------


def test_p1_legacy_json_run_still_authorized(scope):
    """legacy <run_id>.json 也走同一条 compare_result_project_id 路径，
    确保 P1 修改没影响老格式。"""
    from app.compare.result_writer import JsonResultWriter, feed_buckets

    run_id = "LEGACY_RUN_B_001"
    writer = JsonResultWriter(
        result_path=scope["results_dir"] / f"{run_id}.json",
        excel_path=scope["results_dir"] / f"{run_id}.xlsx",
        payload={
            "run_id": run_id, "task_id": scope["task_b"], "task_name": "legacy",
            "summary": {"only_source": 0, "only_target": 0, "diff": 0, "same": 0},
        },
    )
    feed_buckets(writer, {"only_source": [], "only_target": [], "diff": [], "same": []})
    writer.finalize()

    # editorA 跨项目 403；editorB 200
    assert scope["editorA"].get(f"/api/runs/{run_id}/meta").status_code == 403
    assert scope["editorB"].get(f"/api/runs/{run_id}/meta").status_code == 200
    # 直链 .json 下载同样
    assert scope["editorA"].get(f"/results/{run_id}.json").status_code == 403
    assert scope["editorB"].get(f"/results/{run_id}.json").status_code == 200


# ─── 13. P1-F hardening：/api/runs/{job_id} 异步 job 项目级授权 ──────────────
# 老路径：任何登录用户拿到 job_id 就能读 JobInfo —— 暴露别项目的 run 元数据
# / download_url。本节验证按 kind 分流的归属判定：
#   - excel_export job → job.task_id 字段存的是 run_id → compare_result_project_id
#   - compare / task job → job.task_id → task.project_id
#   - workflow job → job.workflow_id → workflow.project_id
# 孤儿 job（task / workflow / run 已删）保留登录态访问，避免历史 job poll 失效。


def test_p1f_excel_export_job_editorA_blocked(scope, parquet_run_in_proj_b):
    """editorB 起 excel_export job 后，editorA 不能 poll 该 job。"""
    # editorB 触发 job
    r = scope["editorB"].post(f"/api/runs/{parquet_run_in_proj_b}/export-excel")
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]

    # editorA 跨项目 poll → 403
    rA = scope["editorA"].get(f"/api/runs/{job_id}")
    assert rA.status_code == 403, rA.text

    # editorB 自己 200
    rB = scope["editorB"].get(f"/api/runs/{job_id}")
    assert rB.status_code == 200, rB.text

    # admin 不受限
    rAd = scope["admin"].get(f"/api/runs/{job_id}")
    assert rAd.status_code == 200, rAd.text


def test_p1f_compare_job_editorA_blocked(scope):
    """直接造一个 compare job 记录指向 task_b（ProjectB） —— editorA 不能 poll。"""
    from app.services.jobs import _set_job, _iso
    from datetime import datetime
    job_id = "fake-compare-job-001"
    _set_job(job_id, {
        "job_id": job_id, "kind": "compare", "task_id": scope["task_b"],
        "status": "success", "stage": "done", "message": "done",
        "created_at": _iso(datetime.now()), "updated_at": _iso(datetime.now()),
        "expires_at": "", "retry_count": 0, "max_retries": 0,
        "result": None, "error": "", "cancel_requested": False,
    })
    assert scope["editorA"].get(f"/api/runs/{job_id}").status_code == 403
    assert scope["editorB"].get(f"/api/runs/{job_id}").status_code == 200
    assert scope["admin"].get(f"/api/runs/{job_id}").status_code == 200


def test_p1f_orphan_job_falls_back_to_login_only(scope):
    """job 指向已删 task —— 归属无法解析，回落到仅登录态（不 403 拒绝），
    避免历史 job poll 接口变 4xx。"""
    from app.services.jobs import _set_job, _iso
    from datetime import datetime
    job_id = "fake-orphan-job"
    _set_job(job_id, {
        "job_id": job_id, "kind": "compare", "task_id": "ghost-task-deleted",
        "status": "failed", "stage": "failed", "message": "task gone",
        "created_at": _iso(datetime.now()), "updated_at": _iso(datetime.now()),
        "expires_at": "", "retry_count": 0, "max_retries": 0,
        "result": None, "error": "", "cancel_requested": False,
    })
    # 任意登录用户都能读（resolved=False 时不门禁）
    assert scope["editorA"].get(f"/api/runs/{job_id}").status_code == 200
    assert scope["editorB"].get(f"/api/runs/{job_id}").status_code == 200


def test_p1f_cancel_endpoint_also_gated(scope):
    """POST /api/runs/{job_id}/cancel 同样走 _gate_job_access。"""
    from app.services.jobs import _set_job, _iso
    from datetime import datetime
    job_id = "fake-running-job"
    _set_job(job_id, {
        "job_id": job_id, "kind": "compare", "task_id": scope["task_b"],
        "status": "running", "stage": "exporting", "message": "running",
        "created_at": _iso(datetime.now()), "updated_at": _iso(datetime.now()),
        "expires_at": "", "retry_count": 0, "max_retries": 0,
        "result": None, "error": "", "cancel_requested": False,
    })
    # editorA 不能 cancel editorB 项目的 job
    assert scope["editorA"].post(f"/api/runs/{job_id}/cancel").status_code == 403
    # editorB 可以（cancel 走完后状态变 cancelling / cancelled）
    rB = scope["editorB"].post(f"/api/runs/{job_id}/cancel")
    assert rB.status_code == 200, rB.text
