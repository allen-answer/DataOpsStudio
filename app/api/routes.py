"""聚合所有 API 子模块的 router 到一个总 router。

main.py 仍然 `from app.api.routes import router` 拿到这一个总 router，URL
路径全部保持不变。新增 endpoint 请加到对应领域子模块（system / datasources
/ tasks / runs / workflows / workflow_runs / history / lineage / uploads /
config_io）；如果不属于任何已有领域，新建一个子模块再 include 进来。
"""
from __future__ import annotations

from fastapi import APIRouter

from app.api import (
    ai_utils,
    assets,
    auth,
    config_io,
    datasources,
    history,
    lineage,
    lineage_graph,
    mfa,
    projects,
    runs,
    scenarios,
    scheduler,
    search,
    slow_sql,
    sql_templates,
    sql_workbench,
    system,
    tasks,
    uploads,
    workflow_runs,
    workflows,
)


router = APIRouter()
# include 顺序仅影响 OpenAPI /docs 的展示分组，不影响实际路由匹配（FastAPI
# 路由匹配按"先注册先匹配"+"具体路径优先"）。
router.include_router(system.router)
router.include_router(auth.router)
router.include_router(mfa.router)
router.include_router(projects.router)
router.include_router(datasources.router)
router.include_router(tasks.router)
router.include_router(runs.router)
router.include_router(scheduler.router)
router.include_router(workflows.router)
router.include_router(workflow_runs.router)
router.include_router(history.router)
router.include_router(lineage.router)
router.include_router(lineage_graph.router)
router.include_router(uploads.router)
router.include_router(config_io.router)
router.include_router(ai_utils.router)
router.include_router(search.router)
router.include_router(assets.router)
router.include_router(scenarios.router)
router.include_router(slow_sql.router)
router.include_router(sql_templates.router)
router.include_router(sql_workbench.router)
