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

# Phase 9 ADR 6：audit / jobs 切 SQLite。启动时跑一次迁移（幂等 —— 表已有数据
# 时跳过，老 jsonl/.json 文件保持原样，让回滚仍能 tail / cat 老文件）。
from app.services import sqlite_store as _sqlite_store
from app.utils.paths import AUDIT_LOG_FILE as _AUDIT_FILE, JOBS_FILE as _JOBS_FILE
_sqlite_store.migrate_audit_jsonl(_AUDIT_FILE)
_sqlite_store.migrate_jobs_json(_JOBS_FILE)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if DEFAULT_ENABLED:
        start_scheduler()
    try:
        yield
    finally:
        stop_scheduler()


app = FastAPI(title="Lightweight Data Compare Tool", version="0.1.0", lifespan=lifespan)
# Phase 9 Day 6：统一错误响应 envelope + request_id middleware。
# 必须在 AuditLogMiddleware 之前 install —— middleware 按 LIFO 触发，先 install
# 的最后跑，让 request_id 在 audit log 阶段已经设进 ContextVar。
from app.api._error_handler import install as install_error_handler
install_error_handler(app)
# 审计日志中间件 —— 记 mutating endpoint 流水到 logs/audit.jsonl
from app.services.audit import AuditLogMiddleware
app.add_middleware(AuditLogMiddleware)
# Phase 10 后期：HTTP metrics 中间件 —— 自动埋点 http_requests_total +
# http_request_duration_seconds，让 /metrics 端点有真实数据
from app.api._metrics_middleware import MetricsMiddleware
app.add_middleware(MetricsMiddleware)
# /metrics 端点（独立路由，不挂在 /api 前缀下，遵循 Prometheus 抓取约定）
from app.api.metrics import router as metrics_router
app.include_router(metrics_router)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.include_router(router)
