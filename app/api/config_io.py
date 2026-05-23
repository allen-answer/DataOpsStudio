"""配置文件导入 / 导出（数据源 + 任务）。"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse

from app.services.auth import ensure_recent_auth, require_role

# 注意：业务侧服务在 app.services.config_io，本模块在 app.api.config_io；
# import 路径不同但模块名相同——别 from .services import config_io，要走完整路径。
from app.services.config_io import export_config, import_config


# config 全局备份 / 恢复，权限更严：导出 / 导入都 admin only。导入是批量
# 创建 / 覆盖 datasources + tasks，且可携带任意 project_id（能绕过单对象
# 创建时的项目校验），风险高于普通 editor 写单个对象。详见
# docs/AUTHORIZATION_MATRIX.md 与 docs/PROJECT_AUTHORIZATION.md。
router = APIRouter()


@router.get("/config/export")
def config_export(
    request: Request,
    include_passwords: bool = False,
    _: object = Depends(require_role("admin")),
):
    """导出 datasources + tasks。默认密码脱敏；显式 ?include_passwords=true
    才把数据源明文密码写进去（用户备份场景）。admin only —— 含敏感配置。

    `include_passwords=true` 额外触发 step-up：当前 token `iat` 超 300s →
    403 step_up_required，前端 prompt 密码 + verify-password 换新 token 重试。
    """
    if include_passwords:
        ensure_recent_auth(request, max_age=300)
    path = export_config(include_passwords=include_passwords)
    return FileResponse(path, filename=path.name)


@router.post("/config/import")
def config_import(
    request: Request,
    config_file: UploadFile = File(...),
    _: object = Depends(require_role("admin")),
):
    """批量导入 datasources + tasks（覆盖式）—— admin only + step-up（300s）。

    导入会覆盖已有配置，影响面比单对象 create 大，跟含密码导出同级敏感。
    """
    ensure_recent_auth(request, max_age=300)
    if not config_file.filename or Path(config_file.filename).suffix.lower() != ".json":
        raise HTTPException(status_code=400, detail="Only .json config files are supported")
    try:
        summary = import_config(config_file.file.read())
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(
        f"/spa?config_imported=1&datasources={summary['datasources']}&tasks={summary['tasks']}",
        status_code=303,
    )
