"""系统级 endpoint：首屏 / SPA / 驱动检测 / results 文件下载。"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, RedirectResponse

from app.dbclients.drivers import detect_drivers
from app.models import BootstrapResponse, DatabaseType, DriverInfo, SqlMode
from app.services.history import list_result_history
from app.services.history_exporter import AVAILABLE_HISTORY_SHEETS
from app.services.repositories import datasource_store, task_store, workflow_store, workflow_template_store
from app.utils.paths import BASE_DIR, RESULTS_DIR


router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/")
def index():
    return RedirectResponse("/spa", status_code=302)


@router.get("/spa")
def spa_page():
    path = BASE_DIR / "static" / "spa" / "index.html"
    if not path.exists():
        raise HTTPException(status_code=404, detail="SPA has not been built yet")
    # 禁 cache：index.html 自身没有 hash，浏览器一旦缓存住就一直引用旧 hash
    # 的 bundle，导致前端改动看起来"没生效"。assets/*.js 自带 hash 文件名，
    # 它们由前置 CDN / FileResponse 默认 immutable cache 没问题——只锁定
    # index.html 不缓存即可。
    return FileResponse(
        path,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
        },
    )


@router.get("/api/drivers", response_model=dict[str, DriverInfo])
def drivers():
    result = detect_drivers()
    logger.info("driver detection result=%s", result)
    return result


@router.get("/api/bootstrap", response_model=BootstrapResponse)
def bootstrap(project_id: str = ""):
    history = list_result_history(project_id=project_id)
    # bootstrap 是首屏拉取，datasources 走前端展示，password 必须脱敏
    redacted_datasources = [
        ds.model_copy(update={"password": ""}) for ds in datasource_store.list()
    ]
    tasks = task_store.list()
    workflows = workflow_store.list()
    if project_id:
        # 项目隔离：当前项目下的资源 + 历史遗留无 project_id 的全局资源
        redacted_datasources = [d for d in redacted_datasources if d.project_id == project_id or not d.project_id]
        tasks = [t for t in tasks if t.project_id == project_id or not t.project_id]
        workflows = [w for w in workflows if w.project_id == project_id or not w.project_id]
    return {
        "datasources": redacted_datasources,
        "tasks": tasks,
        "workflows": workflows,
        "workflow_templates": workflow_template_store.list(),
        "drivers": detect_drivers(),
        "db_types": [item.value for item in DatabaseType],
        "sql_modes": [item.value for item in SqlMode],
        "history": history[:200],
        "history_sheets": list(AVAILABLE_HISTORY_SHEETS),
    }


@router.get("/results/{filename:path}")
def download_result(filename: str):
    """支持子目录的下载：/results/workflow_runs/<run>/exports/<file>.xlsx 也走这里。
    path traversal 防御：解析后必须仍位于 RESULTS_DIR 之下。"""
    path = (RESULTS_DIR / filename).resolve()
    if RESULTS_DIR.resolve() not in path.parents or not path.exists():
        raise HTTPException(status_code=404, detail="Result not found")
    return FileResponse(path, filename=Path(filename).name)
