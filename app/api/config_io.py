"""配置文件导入 / 导出（数据源 + 任务）。"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse

from app.services.auth import require_role

# 注意：业务侧服务在 app.services.config_io，本模块在 app.api.config_io；
# import 路径不同但模块名相同——别 from .services import config_io，要走完整路径。
from app.services.config_io import export_config, import_config


# config 全局备份 / 恢复，权限更严：导出 admin only（含 datasources / tasks 全量），
# 导入 editor（属业务写）。详见 docs/AUTHORIZATION_MATRIX.md。
router = APIRouter()


@router.get("/config/export")
def config_export(include_passwords: bool = False, _: object = Depends(require_role("admin"))):
    """导出 datasources + tasks。默认密码脱敏；显式 ?include_passwords=true
    才把数据源明文密码写进去（用户备份场景）。admin only —— 含敏感配置。"""
    path = export_config(include_passwords=include_passwords)
    return FileResponse(path, filename=path.name)


@router.post("/config/import")
def config_import(config_file: UploadFile = File(...), _: object = Depends(require_role("editor"))):
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
