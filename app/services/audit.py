"""审计日志：mutating endpoint 的操作流水。

Phase 9 ADR 6 起切 SQLite —— 主存储是 `data/dataops.db` 的 `audit_logs` 表
（多线程安全 + WAL mode + 索引化查询）。`logs/audit.jsonl` 仍双写
（向后兼容备份脚本 + grep / tail 工具链），主查询走 SQLite。

查询接口 GET /api/audit-logs（admin only）按 ts 倒序读最近 N 条。

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
from app.services import sqlite_store
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


def _append_log(entry: AuditLogEntry, *, request_id: str = "", project_id: str = "") -> None:
    """SQLite 主存储 + jsonl 双写（向后兼容备份 / grep）。两路独立 catch，
    一边失败不影响另一边写成功。
    """
    payload = entry.model_dump()
    # 1. SQLite
    try:
        with sqlite_store.connect() as conn:
            conn.execute(
                "INSERT INTO audit_logs (ts, user_id, username, method, path, status, "
                "request_id, resource, resource_id, project_id, extra) VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    payload.get("ts", ""),
                    payload.get("user_id", ""),
                    payload.get("username", ""),
                    payload.get("method", ""),
                    payload.get("path", ""),
                    int(payload.get("status_code", 0)),
                    request_id,
                    payload.get("resource_type", ""),
                    payload.get("resource_id", ""),
                    project_id,
                    "",  # extra 暂时空着，留给以后扩展（如 ip / user_agent）
                ),
            )
    except Exception:
        logger.exception("审计日志写 SQLite 失败 path=%s", entry.path)

    # 2. jsonl 双写（兼容老备份脚本 / oncall 用 tail / grep 排错）
    try:
        AUDIT_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_LOG_FILE.open("a", encoding="utf-8") as f:
            line = dict(payload)
            if request_id:
                line["request_id"] = request_id
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception:
        logger.exception("审计日志写 jsonl 失败 path=%s", entry.path)


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
            # request_id：Phase 9 Day 6 ContextVar 已设；project_id 从 query 拿（如果有）
            try:
                from app.api._error_handler import request_id_ctx
                rid = request_id_ctx.get() or ""
            except Exception:
                rid = ""
            project_id = request.query_params.get("project_id", "") if hasattr(request, "query_params") else ""
            _append_log(entry, request_id=rid, project_id=project_id)
        except Exception:
            logger.exception("审计中间件异常 —— 不影响主流程")
        return response


def record_auth_event(
    event: str,
    *,
    username: str = "",
    user_id: str = "",
    method: str = "POST",
    path: str = "",
    status_code: int = 200,
    extra: dict[str, Any] | None = None,
) -> None:
    """显式写一条 auth-related 审计事件。

    用法:在 /login、/refresh、/mfa/*、/logout 等关键路径里调,记下:
      - login_success / login_failure(username + 原因)
      - mfa_enroll / mfa_verify / mfa_disable / mfa_regenerate_codes
      - mfa_challenge_success / mfa_challenge_failure
      - refresh_rotation / refresh_reuse_detected
      - logout
      - rate_limit_hit

    跟中间件 AuditLogMiddleware 互补:中间件按 HTTP 维度记 path/method/status,
    auth 失败时拿不到 user;本 helper 显式把 username 和事件语义写进 audit_logs
    的 `resource` / `extra` 字段。

    event 字符串建模到 `resource` 列 + `extra` JSON 字段;查询时 admin 按
    `resource='auth_event'` 过滤即可见 auth 全套时间线。
    """
    extra_dict = dict(extra or {})
    extra_dict["event"] = event
    entry = AuditLogEntry(
        ts=datetime.now().isoformat(timespec="seconds"),
        user_id=user_id,
        username=username,
        method=method,
        path=path or f"/api/auth/{event}",
        resource_type="auth_event",
        resource_id=event,
        status_code=status_code,
    )
    try:
        from app.api._error_handler import request_id_ctx
        rid = request_id_ctx.get() or ""
    except Exception:
        rid = ""
    # SQLite + jsonl 双写,extra 走 SQLite 的 extra column
    try:
        with sqlite_store.connect() as conn:
            conn.execute(
                "INSERT INTO audit_logs (ts, user_id, username, method, path, status, "
                "request_id, resource, resource_id, project_id, extra) VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    entry.ts, entry.user_id, entry.username, entry.method,
                    entry.path, entry.status_code,
                    rid, entry.resource_type, entry.resource_id, "",
                    json.dumps(extra_dict, ensure_ascii=False),
                ),
            )
    except Exception:
        logger.exception("审计 auth event 写 SQLite 失败 event=%s", event)
    try:
        AUDIT_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_LOG_FILE.open("a", encoding="utf-8") as f:
            line = entry.model_dump()
            line["request_id"] = rid
            line["extra"] = extra_dict
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception:
        logger.exception("审计 auth event 写 jsonl 失败 event=%s", event)


def read_recent_logs(limit: int = 200) -> list[dict[str, Any]]:
    """倒序读最近 N 条。用于 GET /api/audit-logs。

    Phase 9 ADR 6 起：主走 SQLite（按 ts 索引倒序），失败兜底回 jsonl 兼容路径。
    """
    limit = max(int(limit or 1), 1)
    # SQLite 主路径
    try:
        with sqlite_store.connect() as conn:
            cur = conn.execute(
                "SELECT ts, user_id, username, method, path, status, request_id, "
                "resource, resource_id, project_id "
                "FROM audit_logs ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            rows = cur.fetchall()
        if rows:
            out: list[dict[str, Any]] = []
            for row in rows:
                d = dict(row)
                # 兼容老 AuditLogEntry 字段名
                d["status_code"] = d.pop("status", 0)
                d["resource_type"] = d.pop("resource", "")
                out.append(d)
            return out
    except Exception:
        logger.exception("audit logs read from SQLite failed; falling back to jsonl")

    # jsonl 兜底（SQLite 不可用 / 还没迁移完时）
    if not AUDIT_LOG_FILE.exists():
        return []
    try:
        lines = AUDIT_LOG_FILE.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    out2: list[dict[str, Any]] = []
    for line in reversed(lines[-limit:]):
        line = line.strip()
        if not line:
            continue
        try:
            out2.append(json.loads(line))
        except Exception:
            continue
    return out2
