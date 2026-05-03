"""审计日志：mutating endpoint 的操作流水。

落 logs/audit.jsonl 一行一条 JSON，便于 grep / tail / 不入主 DB。
查询接口 GET /api/audit-logs（admin only）按行倒序读最近 N 条。

不包含响应体，避免日志膨胀；只记元数据 + status_code。
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.models import AuditLogEntry
from app.services.auth import decode_access_token, user_store
from app.utils.paths import AUDIT_LOG_FILE

logger = logging.getLogger(__name__)

# 资源 type → URL 前缀。从 /api/{resource}/{id} 解出来；fallback 取一段 path。
_RESOURCE_RE = re.compile(r"^/api/([\w_-]+)(?:/([^/]+))?")
_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
# 不审计的高频 polling endpoint，避免噪音
_SKIP_PATHS_PREFIX = ("/api/runs/",)  # /api/runs/{job_id} GET/POST 太频繁


def _extract_resource(path: str) -> tuple[str, str]:
    m = _RESOURCE_RE.match(path)
    if not m:
        return "", ""
    return m.group(1) or "", m.group(2) or ""


def _extract_user(request: Request) -> tuple[str, str]:
    """从 Authorization 头或 cookie 解出 user_id / username（不抛错）。"""
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if not auth or not auth.lower().startswith("bearer "):
        return "", ""
    payload = decode_access_token(auth[7:].strip())
    if not payload:
        return "", ""
    user_id = payload.get("sub", "")
    username = payload.get("username", "")
    return user_id, username


def _append_log(entry: AuditLogEntry) -> None:
    try:
        AUDIT_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry.model_dump(), ensure_ascii=False) + "\n")
    except Exception:
        logger.exception("审计日志落盘失败 path=%s", entry.path)


class AuditLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        try:
            method = request.method.upper()
            path = request.url.path
            if method not in _MUTATING_METHODS:
                return response
            if any(path.startswith(p) for p in _SKIP_PATHS_PREFIX):
                return response
            if not path.startswith("/api/"):
                return response

            user_id, username = _extract_user(request)
            resource_type, resource_id = _extract_resource(path)
            entry = AuditLogEntry(
                ts=datetime.now().isoformat(timespec="seconds"),
                user_id=user_id,
                username=username,
                method=method,
                path=path,
                resource_type=resource_type,
                resource_id=resource_id,
                status_code=response.status_code,
            )
            _append_log(entry)
        except Exception:
            logger.exception("审计中间件异常 —— 不影响主流程")
        return response


def read_recent_logs(limit: int = 200) -> list[dict[str, Any]]:
    """倒序读最近 N 条。用于 GET /api/audit-logs。"""
    if not AUDIT_LOG_FILE.exists():
        return []
    try:
        lines = AUDIT_LOG_FILE.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for line in reversed(lines[-max(limit, 1):]):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out
