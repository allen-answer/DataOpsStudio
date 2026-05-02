"""配置文件导入 / 导出（数据源 + 任务）。"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse

# 注意：业务侧服务在 app.services.config_io，本模块在 app.api.config_io；
# import 路径不同但模块名相同——别 from .services import config_io，要走完整路径。
from app.services.config_io import export_config, import_config


router = APIRouter()


@router.get("/config/export")
def config_export():
    path = export_config()
    return FileResponse(path, filename=path.name)


@router.post("/config/import")
def config_import(config_file: UploadFile = File(...)):
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
