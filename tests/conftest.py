"""共享 pytest fixture。

`isolated_storage` —— 把所有持久化路径（数据源 / 任务 / 作业流 JSON、结果
目录、jobs 文件等）重定向到 tmp_path 下，集成测试运行后留下的文件不会污染
真实 dev 环境的 config/ 和 results/。

之所以要 patch 多个模块属性而不是只 patch paths.py：因为很多模块在 import
顶层做了 `from app.utils.paths import RESULTS_DIR`，把当时的值绑到自己的
模块命名空间。只 patch paths 模块来不及改这些已经绑好的引用。
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

    # 5. user / project / audit store —— D-MVP 多项目空间相关
    from app.services import auth as auth_svc, audit as audit_svc
    from app.api import projects as projects_api
    monkeypatch.setattr(auth_svc.user_store, "path", cfg / "users.json")
    monkeypatch.setattr(projects_api.project_store, "path", cfg / "projects.json")
    monkeypatch.setattr(audit_svc, "AUDIT_LOG_FILE", tmp_path / "audit.jsonl")
    monkeypatch.setattr(paths_module, "USERS_FILE", cfg / "users.json")
    monkeypatch.setattr(paths_module, "PROJECTS_FILE", cfg / "projects.json")
    auth_svc.user_store.invalidate_cache()
    projects_api.project_store.invalidate_cache()

    yield {
        "cfg": cfg,
        "results": results,
        "wf_runs": wf_runs,
        "uploads": uploads,
    }
