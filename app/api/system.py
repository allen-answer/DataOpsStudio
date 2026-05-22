"""系统级 endpoint：首屏 / SPA / 驱动检测 / results 文件下载。"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, RedirectResponse

from app.api._authz import (
    accessible_project_ids,
    can_access_project,
    filter_by_project,
    result_download_project_id,
)
from app.dbclients.drivers import detect_drivers
from app.models import BootstrapResponse, DatabaseType, DriverInfo, SqlMode, User
from app.services.auth import get_current_user
from app.services.download_token import verify_download_token
from app.services.history import list_result_history
from app.services.history_exporter import AVAILABLE_HISTORY_SHEETS
from app.services.repositories import datasource_store, task_store, workflow_store, workflow_template_store
from app.utils.paths import BASE_DIR, RESULTS_DIR


# system router 混合公开 + 鉴权 endpoint —— 不在 router 级挂 auth，
# 单个 endpoint 决定。/  /spa  /api/drivers 公开；/api/bootstrap  /results/* 走 viewer。
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
def bootstrap(project_id: str = "", current: User = Depends(get_current_user)):
    # 首屏拉取 —— 只要前 200 条历史；全量在历史页按需加载。
    # 历史按当前用户可见项目过滤（admin 传 None = 不限制）。
    history = list_result_history(
        project_id=project_id,
        limit=200,
        allowed_project_ids=accessible_project_ids(current),
    )
    # bootstrap 是首屏拉取，datasources 走前端展示，password 必须脱敏。
    # 三类资源先按用户可访问项目过滤（非 admin 看不到别人项目的资源）。
    redacted_datasources = filter_by_project(
        [ds.model_copy(update={"password": ""}) for ds in datasource_store.list()],
        current,
    )
    tasks = filter_by_project(task_store.list(), current)
    workflows = filter_by_project(workflow_store.list(), current)
    if project_id:
        # 显式项目过滤：当前项目下的资源 + 历史遗留无 project_id 的全局资源
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
def download_result(filename: str, current: User = Depends(get_current_user)):
    """支持子目录的下载：/results/workflow_runs/<run>/exports/<file>.xlsx 也走这里。
    path traversal 防御：解析后必须仍位于 RESULTS_DIR 之下。

    项目级隔离：不能只看登录态 —— 把文件解析回它所属项目（compare result
    走 task / workflow 产物走 workflow），用户对该项目无权 → 403。无法归属的
    文件（上传文件 / 未知）回落到仅登录态。见 docs/PROJECT_AUTHORIZATION.md。"""
    path = (RESULTS_DIR / filename).resolve()
    if RESULTS_DIR.resolve() not in path.parents or not path.exists():
        raise HTTPException(status_code=404, detail="Result not found")
    project_id, resolved = result_download_project_id(filename)
    if resolved and not can_access_project(current, project_id):
        raise HTTPException(status_code=403, detail="无权下载该结果文件")
    return FileResponse(path, filename=Path(filename).name)


_DOWNLOAD_SUFFIX_WHITELIST = {".json", ".xlsx", ".parquet"}


@router.get("/api/downloads/{token}")
def download_via_token(token: str, current: User = Depends(get_current_user)):
    """签名 token 下载 —— 取代路径式 `/results/*`。

    token 短时有效、HMAC 防篡改；项目权限以当前用户实时校验（token 里的
    project_id 仅作记录）。即便 token 泄露也只在 TTL 内有效，且仍受项目权限拦。
    """
    payload = verify_download_token(token)
    if payload is None:
        raise HTTPException(status_code=401, detail="下载链接无效或已过期")
    if not can_access_project(current, str(payload.get("project_id") or "")):
        raise HTTPException(status_code=403, detail="无权下载该结果文件")
    rel = str(payload.get("rel") or "")
    path = (RESULTS_DIR / rel).resolve()
    if RESULTS_DIR.resolve() not in path.parents or not path.exists():
        raise HTTPException(status_code=404, detail="Result not found")
    if path.suffix.lower() not in _DOWNLOAD_SUFFIX_WHITELIST:
        raise HTTPException(status_code=400, detail="不允许的文件类型")
    return FileResponse(path, filename=path.name)
