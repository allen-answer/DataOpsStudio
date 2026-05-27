from __future__ import annotations

# 必须最早 load config.yml —— 后面 import 的 service module 在 module-level
# 就 os.getenv 读 JWT_SECRET / RATELIMIT_ENFORCE 等,晚于此处加载会拿不到 yml 值。
# env var 仍优先于 yml(docker compose env / CI / 启动脚本 set 的不会被覆盖)。
from app.config_loader import load_config  # noqa: E402

load_config()

import mimetypes  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from starlette.responses import Response  # noqa: E402
from starlette.types import Scope  # noqa: E402

from app.api.routes import router  # noqa: E402
from app.services.scheduler import DEFAULT_ENABLED, start_scheduler, stop_scheduler  # noqa: E402
from app.utils.logging_config import setup_logging  # noqa: E402
from app.utils.paths import BASE_DIR, ensure_dirs  # noqa: E402


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
# Phase 14:custom StaticFiles 子类 —— index.html 强制 no-cache,hash 化的
# assets/*.js / *.css 用 immutable 长 cache。不这样的话 deploy 后老 index.html
# 被浏览器缓存住,引用的 hash bundle 文件名变了 → 404 → 用户看到白屏 / 进不去。
class _SpaStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        resp = await super().get_response(path, scope)
        # Windows 下 path 用 `\` 分隔,Unix 用 `/`,统一归一化再 check
        norm = path.replace("\\", "/")
        # index.html 不缓存:让浏览器每次 revalidate,deploy 后立刻拉新版引用
        if norm.endswith("index.html") or norm.endswith("/"):
            resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            resp.headers["Pragma"] = "no-cache"
        elif "/assets/" in norm or norm.startswith("assets/") or "/assets/" in f"/{norm}":
            # hash 化 immutable 资源:1 年长缓存 + immutable(浏览器永不 revalidate)
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return resp


app.mount("/static", _SpaStaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.include_router(router)
# Phase 10 后期：API 版本化前缀。所有 /api/X 路由克隆出 /api/v1/X 同义版本，
# 给前端 / 第三方一个稳定 v1 契约；旧 /api/... 路径继续可用作 deprecation window。
# 必须在 include_router 之后调（要遍历已注册路由）。
from app.api._versioning import install_v1_aliases
install_v1_aliases(app)
