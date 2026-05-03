from __future__ import annotations

import mimetypes
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.services.scheduler import DEFAULT_ENABLED, start_scheduler, stop_scheduler
from app.utils.logging_config import setup_logging
from app.utils.paths import BASE_DIR, ensure_dirs


# 注册 Office / 数据相关扩展名的 mimetype。python:3.12-slim 镜像没装
# /etc/mime.types，python `mimetypes` 默认表里也认不出 .xlsx —— 导致
# FileResponse 用 text/plain 回 .xlsx 二进制，浏览器把它当文本渲染就成了
# 一坨乱码。在 import 阶段统一 add_type 一次，所有 FileResponse 自动用对。
mimetypes.add_type(
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"
)
mimetypes.add_type(
    "application/vnd.ms-excel.sheet.macroEnabled.12", ".xlsm"
)
mimetypes.add_type("application/json", ".json")

ensure_dirs()
setup_logging()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if DEFAULT_ENABLED:
        start_scheduler()
    try:
        yield
    finally:
        stop_scheduler()


app = FastAPI(title="Lightweight Data Compare Tool", version="0.1.0", lifespan=lifespan)
# 审计日志中间件 —— 记 mutating endpoint 流水到 logs/audit.jsonl
from app.services.audit import AuditLogMiddleware
app.add_middleware(AuditLogMiddleware)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.include_router(router)
