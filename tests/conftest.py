"""共享 pytest fixture。

`isolated_storage` —— 把所有持久化路径（数据源 / 任务 / 作业流 JSON、结果
目录、jobs 文件等）重定向到 tmp_path 下，集成测试运行后留下的文件不会污染
真实 dev 环境的 config/ 和 results/。

之所以要 patch 多个模块属性而不是只 patch paths.py：因为很多模块在 import
顶层做了 `from app.utils.paths import RESULTS_DIR`，把当时的值绑到自己的
模块命名空间。只 patch paths 模块来不及改这些已经绑好的引用。

P0.4 起后端 endpoint 强制鉴权，老测试 client 不带 token 会 401。本文件
提供：
  - `client`           ：带 admin token 的 TestClient（老测试默认用，零改动）
  - `client_anon`      ：纯匿名 TestClient（专门测 401 / 公开 endpoint）
  - `client_editor`    ：editor 角色（专门测 403 / 普通业务）
  - `client_viewer`    ：viewer 角色（专门测只读 / 403 写）
  - `client_admin`     ：跟 `client` 一样的 admin 角色（语义明确时用）
所有 fixture 都依赖 `isolated_storage` + auto-bootstrap 三档用户。
"""
from __future__ import annotations

import pytest


# 默认 `pytest` 跑 unit + integration，跳过 e2e（playwright 浏览器测试）
# —— e2e 需要装 chromium 二进制 + 应用在 :8010 真跑起来。
# 显式 `pytest tests/e2e/` 才会跑（直接传路径绕过 ignore）。
# 用 collect_ignore（list，不是 glob 版）：仅在没显式指定时排除。
collect_ignore = ["e2e"]


@pytest.fixture
def isolated_storage(tmp_path, monkeypatch):
    """所有 storage 路径重定向到 tmp_path。yield 一个 dict 给测试拿子目录。"""
    cfg = tmp_path / "config"
    cfg.mkdir()
    results = tmp_path / "results"
    results.mkdir()
    wf_runs = results / "workflow_runs"
    wf_runs.mkdir()
    uploads = results / "uploads"
    uploads.mkdir()
    data = tmp_path / "data"
    data.mkdir()

    # 1. JsonStore 单例：直接改 path + invalidate cache
    from app.services.repositories import datasource_store, task_store, workflow_store, workflow_template_store

    monkeypatch.setattr(datasource_store, "path", cfg / "datasources.json")
    monkeypatch.setattr(task_store, "path", cfg / "tasks.json")
    monkeypatch.setattr(workflow_store, "path", cfg / "workflows.json")
    monkeypatch.setattr(workflow_template_store, "path", cfg / "workflow_templates.json")
    datasource_store.invalidate_cache()
    task_store.invalidate_cache()
    workflow_store.invalidate_cache()
    workflow_template_store.invalidate_cache()

    # 2. paths.py 模块属性（function-level import 的拿到的会是这些 patched 值）
    from app.utils import paths as paths_module
    monkeypatch.setattr(paths_module, "RESULTS_DIR", results)
    monkeypatch.setattr(paths_module, "WORKFLOW_RUNS_DIR", wf_runs)
    monkeypatch.setattr(paths_module, "DATASOURCES_FILE", cfg / "datasources.json")
    monkeypatch.setattr(paths_module, "TASKS_FILE", cfg / "tasks.json")
    monkeypatch.setattr(paths_module, "WORKFLOWS_FILE", cfg / "workflows.json")
    monkeypatch.setattr(paths_module, "WORKFLOW_TEMPLATES_FILE", cfg / "workflow_templates.json")
    monkeypatch.setattr(paths_module, "JOBS_FILE", cfg / "jobs.json")
    monkeypatch.setattr(paths_module, "LINEAGE_AI_CONFIG_FILE", cfg / "lineage_ai.json")
    monkeypatch.setattr(paths_module, "LOCAL_SECRET_KEY_FILE", cfg / ".dataops_secret.key")

    # 3. 已经在 import 顶层绑住 path 的模块 —— 必须各自 patch
    from app.services import workflow_history, history as history_svc, history_exporter, excel_uploads, jobs, config_io as config_io_svc
    monkeypatch.setattr(workflow_history, "WORKFLOW_RUNS_DIR", wf_runs)
    monkeypatch.setattr(history_svc, "RESULTS_DIR", results)
    monkeypatch.setattr(history_exporter, "RESULTS_DIR", results)
    monkeypatch.setattr(excel_uploads, "RESULTS_DIR", results)
    monkeypatch.setattr(excel_uploads, "UPLOADS_DIR", uploads)
    monkeypatch.setattr(jobs, "JOBS_FILE", cfg / "jobs.json")
    monkeypatch.setattr(config_io_svc, "DATASOURCES_FILE", cfg / "datasources.json")
    monkeypatch.setattr(config_io_svc, "TASKS_FILE", cfg / "tasks.json")
    monkeypatch.setattr(config_io_svc, "RESULTS_DIR", results)
    # jobs 模块的 _jobs 全局 dict 跨测试残留，清一下
    jobs._jobs.clear()
    jobs._futures.clear()

    # 4. api 子模块也持有 RESULTS_DIR 引用
    from app.api import system as system_api
    monkeypatch.setattr(system_api, "RESULTS_DIR", results)

    # 5.0 SQLite 重定向到 tmp_path/data/dataops.db（Phase 9 ADR 6 起 audit/jobs 用）
    from app.services import sqlite_store
    monkeypatch.setattr(sqlite_store, "SQLITE_DB_FILE", data / "dataops.db")
    # 清掉模块级初始化标记，让新 db 走一遍 _ensure_initialized
    sqlite_store._initialized.clear()

    # 5. user / project / audit store —— D-MVP 多项目空间相关
    from app.services import auth as auth_svc, audit as audit_svc
    from app.services import lineage_ai_config as lineage_ai_config_svc, secret_crypto as secret_crypto_svc
    from app.api import projects as projects_api
    monkeypatch.setattr(auth_svc.user_store, "path", cfg / "users.json")
    monkeypatch.setattr(projects_api.project_store, "path", cfg / "projects.json")
    monkeypatch.setattr(audit_svc, "AUDIT_LOG_FILE", tmp_path / "audit.jsonl")
    monkeypatch.setattr(lineage_ai_config_svc, "LINEAGE_AI_CONFIG_FILE", cfg / "lineage_ai.json")
    monkeypatch.setattr(secret_crypto_svc, "LOCAL_SECRET_KEY_FILE", cfg / ".dataops_secret.key")
    monkeypatch.setattr(paths_module, "USERS_FILE", cfg / "users.json")
    monkeypatch.setattr(paths_module, "PROJECTS_FILE", cfg / "projects.json")
    auth_svc.user_store.invalidate_cache()
    projects_api.project_store.invalidate_cache()

    # 6. column lineage edge index + workflow_history payload 内存缓存 —— 跨测试
    #    tmp_path 切换时残留旧索引（run_count 可能巧合相同），显式 clear 避免污染
    from app.services import assets as assets_svc
    from app.services import workflow_history as wf_history
    assets_svc.invalidate_column_edge_index_cache()
    wf_history.invalidate_run_payloads_cache()

    yield {
        "cfg": cfg,
        "results": results,
        "wf_runs": wf_runs,
        "uploads": uploads,
        "data": data,
    }


# ─── P0.4 鉴权 fixture：自动 bootstrap admin/editor/viewer + 各色 TestClient ───


_TEST_USERS = (
    ("admin",  "admin",   "admin"),
    ("editor", "editor",  "editor"),
    ("viewer", "viewer",  "viewer"),
)


def _bootstrap_users(isolated_storage):
    """isolated_storage 起好后建 admin (内置) + editor + viewer 三档账号。

    密码 = 用户名（仅本地测试，bcrypt 一致）。
    """
    import json
    import uuid
    from datetime import datetime
    from app.services import auth as auth_svc
    from app.utils.paths import USERS_FILE

    auth_svc.bootstrap_default_admin()
    raw = json.loads(USERS_FILE.read_text(encoding="utf-8"))
    existing_names = {u.get("username") for u in raw}
    for username, password, role in _TEST_USERS:
        if username in existing_names:
            continue
        raw.append({
            "id": uuid.uuid4().hex,
            "username": username,
            "password_hash": auth_svc.hash_password(password),
            "role": role,
            "display_name": username,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        })
    USERS_FILE.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    auth_svc.user_store.invalidate_cache()


def _new_client(role: str | None):
    """新建一个 TestClient，若 role 非 None 则自动登录并挂 Authorization 头。"""
    from fastapi.testclient import TestClient
    from main import app
    tc = TestClient(app)
    if role is None:
        return tc
    r = tc.post("/api/auth/login", json={"username": role, "password": role})
    assert r.status_code == 200, f"login {role} failed: {r.status_code} {r.text}"
    tc.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    return tc


@pytest.fixture
def client(isolated_storage):
    """默认 admin 角色的 TestClient —— 老业务测试不改一行直接复用。"""
    _bootstrap_users(isolated_storage)
    return _new_client("admin")


@pytest.fixture
def client_admin(isolated_storage):
    """语义明确的 admin client（跟 `client` 等价，文件里同时测多角色时清楚标识）。"""
    _bootstrap_users(isolated_storage)
    return _new_client("admin")


@pytest.fixture
def client_editor(isolated_storage):
    _bootstrap_users(isolated_storage)
    return _new_client("editor")


@pytest.fixture
def client_viewer(isolated_storage):
    _bootstrap_users(isolated_storage)
    return _new_client("viewer")


@pytest.fixture
def client_anon(isolated_storage):
    """纯匿名 TestClient —— 不带任何 token，专测 401 / 公开 endpoint。"""
    _bootstrap_users(isolated_storage)
    return _new_client(None)
