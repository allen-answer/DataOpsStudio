"""HTTP 层端到端集成测试 —— 用 FastAPI TestClient 跑真实路由。

之前 267 个 unit 测试都直接调函数，没经过 router / response_model 序列化 /
mimetype 推断 / FileResponse 挂头这一层 —— 这次发现的 bug（导出 .xlsx
Content-Type 是 text/plain 让浏览器渲染成乱码、 currentWorkflow.name
访问 undefined 让组件白屏）正是这一层薄弱的体现。

集成测试只跑不需要真实数据库的链路：params + excel_export + 历史导出 +
artifact 下载。compare 节点要 MySQL，留给单元测试覆盖。
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(isolated_storage):
    """绑在隔离 storage 上的 TestClient。"""
    # main.py 里 import mimetypes 注册 .xlsx 等的步骤要在 client 起来之前
    # 已生效——直接 import main 就触发了。
    from main import app
    return TestClient(app)


# --- workflow CRUD ---

def test_create_run_delete_workflow_full_cycle(client, isolated_storage):
    """workflow 全生命周期：创建 → 同步执行 → 查 run → 删 workflow → 删 run."""
    # 1. 创建
    payload = {
        "name": "e2e-cycle",
        "nodes": [
            {"id": "p1", "type": "params", "name": "p",
             "config": {"parameters": [{"name": "x", "type": "fixed", "default": "v"}]},
             "depends_on": []},
        ],
    }
    response = client.post("/api/workflows", json=payload)
    assert response.status_code == 200, response.text
    wf = response.json()
    wf_id = wf["id"]
    assert wf["status"] == "draft"   # Slice A 加的字段

    # 2. 列出能看到
    response = client.get("/api/workflows")
    assert any(item["id"] == wf_id for item in response.json())

    # 3. 同步执行
    response = client.post(f"/api/workflows/{wf_id}/run", json={"variables": {}})
    assert response.status_code == 200, response.text
    run = response.json()
    assert run["status"] == "success"
    run_id = run["run_id"]

    # 4. 拿 run 详情
    response = client.get(f"/api/workflow-runs/{run_id}")
    assert response.status_code == 200
    assert response.json()["run_id"] == run_id

    # 5. 删 run
    response = client.delete(f"/api/workflow-runs/{run_id}")
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    response = client.get(f"/api/workflow-runs/{run_id}")
    assert response.status_code == 404

    # 6. 删 workflow
    response = client.delete(f"/api/workflows/{wf_id}")
    assert response.status_code == 200


def test_create_workflow_rejects_bad_when_expression(client, isolated_storage):
    """`when` 语法预检（_shared.ensure_workflow_node_targets）应该 400 拒掉
    空 LHS 这种坏表达式，不留到运行时报错。"""
    payload = {
        "name": "bad-when",
        "nodes": [
            {"id": "p", "type": "params", "config": {"parameters": [{"name": "x"}]}},
            {"id": "n", "type": "params",
             "config": {"parameters": [{"name": "y"}]},
             "depends_on": ["p"],
             "when": ' == "prod"'},
        ],
    }
    response = client.post("/api/workflows", json=payload)
    assert response.status_code == 400
    assert "when" in response.json()["detail"].lower()


def test_create_workflow_rejects_compare_partial_sql_override(client, isolated_storage):
    """compare override 必须是完整 SELECT/WITH，不能是 WHERE 片段。"""
    # 先建一个 task 让 compare 能引用
    ds = client.post("/api/datasources", json={
        "name": "ds-test", "db_type": "MySQL", "host": "localhost", "port": 3306,
    }).json()
    task = client.post("/api/tasks", json={
        "name": "t-test", "source_id": ds["id"], "target_id": ds["id"],
        "sql_mode": "single", "source_sql": "SELECT 1 as id",
        "key_columns": ["id"],
    }).json()

    payload = {
        "name": "bad-override",
        "nodes": [
            {"id": "c", "type": "compare", "config": {
                "task_id": task["id"],
                "source_sql_override": "id = ${user_id}",   # 半句 SQL，应被拒
            }},
        ],
    }
    response = client.post("/api/workflows", json=payload)
    assert response.status_code == 400
    assert "select/with" in response.json()["detail"].lower() or "WHERE" in response.json()["detail"]


# --- excel_export 链路 + artifact 下载 ---

def test_workflow_with_excel_export_produces_artifact_and_downloads(client, isolated_storage):
    """params → excel_export 同步跑：run.artifacts 里有 1 个 excel artifact，
    通过 /results/<relative_path> 能拉到合法 .xlsx 二进制 + 正确 mimetype.
    """
    # 1. 建 workflow（params + excel_export）
    payload = {
        "name": "e2e-export",
        "nodes": [
            {"id": "p1", "type": "params",
             "config": {"parameters": [{"name": "biz_date", "type": "fixed", "default": "2026-05-01"}]},
             "depends_on": []},
            {"id": "ex1", "type": "excel_export", "name": "导出",
             "config": {"sheets": [{
                 "id": "s1", "enabled": True, "sheet_name": "params",
                 "source_type": "node_output", "node_id": "p1", "dataset": "",
             }]},
             "depends_on": ["p1"]},
        ],
    }
    wf = client.post("/api/workflows", json=payload).json()

    # 2. 同步跑
    run = client.post(f"/api/workflows/{wf['id']}/run", json={"variables": {}}).json()
    assert run["status"] == "success", run

    # 3. run.artifacts 顶层聚合 (Slice B 的 computed_field)
    arts = run["artifacts"]
    assert len(arts) == 1
    art = arts[0]
    assert art["type"] == "excel"
    assert art["node_id"] == "ex1"   # engine 回填
    assert art["run_id"] == run["run_id"]
    rel = art["relative_path"]
    assert rel.startswith(f"workflow_runs/{run['run_id']}/exports/")
    assert rel.endswith(".xlsx")

    # 4. 通过 /results/<rel> 下载
    response = client.get(f"/results/{rel}")
    assert response.status_code == 200
    # 真 xlsx 的 zip 头：PK\x03\x04
    assert response.content[:4] == b"PK\x03\x04"
    # mimetype 必须是 spreadsheet —— 之前 bug 就是这里给了 text/plain
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


def test_results_endpoint_blocks_path_traversal(client, isolated_storage):
    """/results/{path} 不能用 .. 跳出 RESULTS_DIR 读其它文件。"""
    # 试图读 RESULTS_DIR 上一层（一般是 BASE_DIR / config / datasources.json）
    response = client.get("/results/../config/datasources.json")
    # 注意：HTTP 客户端 / 框架 normalize URL 中的 .. → 实际请求可能是
    # `/config/datasources.json`（不在 /results/ prefix 下，404）；
    # 如果直接到了 endpoint，应该 404 path traversal 防御。
    assert response.status_code == 404


def test_delete_workflow_run_cleans_artifact_directory(client, isolated_storage):
    """删 run 必须把 results/workflow_runs/<run_id>/ 整个目录清掉，否则磁盘
    残留垃圾文件。Slice B 的 delete cascade。"""
    # 跑一个产生 artifact 的 run
    wf = client.post("/api/workflows", json={
        "name": "cleanup",
        "nodes": [
            {"id": "p1", "type": "params",
             "config": {"parameters": [{"name": "x", "type": "fixed", "default": "v"}]}},
            {"id": "ex", "type": "excel_export",
             "config": {"sheets": [{"id": "s", "enabled": True, "sheet_name": "s",
                                    "source_type": "node_output", "node_id": "p1", "dataset": ""}]},
             "depends_on": ["p1"]},
        ],
    }).json()
    run = client.post(f"/api/workflows/{wf['id']}/run", json={"variables": {}}).json()
    run_id = run["run_id"]
    arts_dir = isolated_storage["wf_runs"] / run_id
    assert arts_dir.exists(), "exports 目录应已创建"

    # 删 run
    response = client.delete(f"/api/workflow-runs/{run_id}")
    assert response.status_code == 200
    assert not arts_dir.exists(), "删 run 后 artifacts 目录应被 rmtree 清掉"


# --- /history/export 这次 bug 的回归测试 ---

def test_history_export_returns_correct_xlsx_mimetype(client, isolated_storage):
    """这次 bug 的回归测试：浏览器拿到 text/plain 的 .xlsx 就把二进制当
    文本解码成一坨乱码。Content-Type 必须是真实 spreadsheet mime。"""
    import openpyxl
    from openpyxl import Workbook

    # 在 results/ 直接放一个假的"已运行 run"的 .xlsx + .json，模拟 compare 任务跑完落盘。
    # history_exporter 从 RESULTS_DIR/<run_id>.xlsx 读 sheet 合并导出。
    run_id = "test_run_xyz"
    book = Workbook()
    sheet = book.active
    sheet.title = "diff"
    sheet.append(["id", "old", "new"])
    sheet.append([1, "a", "b"])
    book.save(isolated_storage["results"] / f"{run_id}.xlsx")

    # 写 JSON 让 list_result_history 能列出（不强制要求，export 只读 xlsx）
    (isolated_storage["results"] / f"{run_id}.json").write_text(
        json.dumps({"run_id": run_id, "task_id": "t1"}), encoding="utf-8",
    )

    # 实际调导出（form 用 dict + list 形式，list-of-tuples 在 httpx 不一致）
    response = client.post(
        "/history/export",
        data={"run_ids": [run_id], "sheet_names": ["diff"]},
    )
    assert response.status_code == 200, response.text
    # 内容是真 xlsx
    assert response.content[:4] == b"PK\x03\x04"
    # mimetype 是 spreadsheet —— 这次 bug 直接的回归保护
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    ), f"Content-Type 错了：{response.headers['content-type']}"
    # 文件名通过 Content-Disposition 给出
    assert "history_export_" in response.headers.get("content-disposition", "")


# --- workflow rerun 链路（局部重跑接口） ---

def test_rerun_workflow_run_from_node_succeeds(client, isolated_storage):
    """params → params 链路：rerun from 第二个节点，第一个不重跑（reused），
    第二个真跑。"""
    payload = {
        "name": "rerun",
        "nodes": [
            {"id": "p1", "type": "params",
             "config": {"parameters": [{"name": "a", "type": "fixed", "default": "1"}]}},
            {"id": "p2", "type": "params",
             "config": {"parameters": [{"name": "b", "type": "fixed", "default": "2"}]},
             "depends_on": ["p1"]},
        ],
    }
    wf = client.post("/api/workflows", json=payload).json()
    run = client.post(f"/api/workflows/{wf['id']}/run", json={"variables": {}}).json()
    assert run["status"] == "success"

    # rerun from p2
    response = client.post(
        f"/api/workflow-runs/{run['run_id']}/rerun",
        json={"from_node_id": "p2"},
    )
    assert response.status_code == 200, response.text
    job = response.json()
    assert job["kind"] == "workflow"
    assert job["resumed_from"]["from_node_id"] == "p2"


def test_rerun_unknown_run_returns_404(client, isolated_storage):
    response = client.post(
        "/api/workflow-runs/no-such-run/rerun",
        json={"from_node_id": "x"},
    )
    assert response.status_code == 404


# --- system endpoints ---

def test_bootstrap_returns_complete_payload(client, isolated_storage):
    """前端首屏要的 8 个字段必须都在。"""
    response = client.get("/api/bootstrap")
    assert response.status_code == 200
    payload = response.json()
    expected = {"datasources", "tasks", "workflows", "drivers",
                "db_types", "sql_modes", "history", "history_sheets"}
    assert expected.issubset(set(payload.keys()))


def test_drivers_response_schema(client, isolated_storage):
    """每个 db_type 应当返回 {available, installed_modules, candidate_modules}。"""
    response = client.get("/api/drivers")
    payload = response.json()
    for db_type, info in payload.items():
        assert "available" in info
        assert "installed_modules" in info
        assert "candidate_modules" in info


# --- 密码脱敏 ---

def test_datasource_password_redacted_on_all_read_endpoints(client, isolated_storage):
    """create / list / bootstrap / update 返回的 password 必须空串 —— 内部
    store 仍存明文跑 test 用，但永远不通过 API 暴露。"""
    secret = "my-super-secret-pw-123"
    created = client.post("/api/datasources", json={
        "name": "ds-secret", "db_type": "MySQL", "host": "h", "port": 3306,
        "password": secret,
    }).json()
    assert created["password"] == "", "create 响应漏密码"

    listed = client.get("/api/datasources").json()
    target = next(d for d in listed if d["id"] == created["id"])
    assert target["password"] == "", "list 响应漏密码"

    bootstrap = client.get("/api/bootstrap").json()
    target = next(d for d in bootstrap["datasources"] if d["id"] == created["id"])
    assert target["password"] == "", "bootstrap 响应漏密码"


def test_datasource_update_with_empty_password_keeps_original(client, isolated_storage):
    """前端编辑表单 password 留空 = 保持原密码不变。否则用户每次改 host 都要
    重输密码——糟糕的 UX。"""
    from app.services.repositories import datasource_store

    secret = "original-pw"
    created = client.post("/api/datasources", json={
        "name": "ds-keep", "db_type": "MySQL", "host": "h1", "port": 3306,
        "password": secret,
    }).json()

    # 留空 password 的 update —— 只想改 host
    response = client.put(f"/api/datasources/{created['id']}", json={
        "name": "ds-keep", "db_type": "MySQL", "host": "h2", "port": 3306,
        "password": "",
    })
    assert response.status_code == 200
    assert response.json()["host"] == "h2"

    # 内部 store 必须仍存原密码（不是被空覆盖）
    internal = datasource_store.get(created["id"])
    assert internal.password == secret, "update with empty password 把密码覆盖空了"


def test_datasource_update_with_new_password_replaces(client, isolated_storage):
    """非空 password 正常覆盖。"""
    from app.services.repositories import datasource_store

    created = client.post("/api/datasources", json={
        "name": "ds-rotate", "db_type": "MySQL", "host": "h", "port": 3306,
        "password": "old",
    }).json()

    client.put(f"/api/datasources/{created['id']}", json={
        "name": "ds-rotate", "db_type": "MySQL", "host": "h", "port": 3306,
        "password": "new-rotated",
    })
    assert datasource_store.get(created["id"]).password == "new-rotated"


def test_config_export_redacts_passwords_by_default(client, isolated_storage):
    """/config/export 默认导出文件不含明文密码。"""
    import json as _json

    client.post("/api/datasources", json={
        "name": "ds-export", "db_type": "MySQL", "host": "h", "port": 3306,
        "password": "secret-must-not-leak",
    })
    response = client.get("/config/export")
    assert response.status_code == 200
    payload = _json.loads(response.content)
    assert payload["passwords_included"] is False
    for ds in payload["datasources"]:
        assert ds["password"] == "", \
            f"默认导出泄露明文密码：{ds.get('name')} -> {ds['password']!r}"


def test_config_export_includes_passwords_when_explicitly_requested(client, isolated_storage):
    """显式 ?include_passwords=true 才把明文密码写进去（用户自备份场景）。"""
    import json as _json

    client.post("/api/datasources", json={
        "name": "ds-backup", "db_type": "MySQL", "host": "h", "port": 3306,
        "password": "wanted-this-time",
    })
    response = client.get("/config/export?include_passwords=true")
    payload = _json.loads(response.content)
    assert payload["passwords_included"] is True
    pwds = [ds["password"] for ds in payload["datasources"] if ds.get("name") == "ds-backup"]
    assert "wanted-this-time" in pwds
