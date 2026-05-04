"""HTTP request metrics middleware —— 自动埋点 http_requests_total +
http_request_duration_seconds。"""
from __future__ import annotations

import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.services.metrics import http_request_duration_seconds, http_requests_total


# 路径归一化：把 ID 类路径段（uuid hex / 数字 / dotted name）归并成 `*`，避免
# label 基数爆炸（每个 task_id 单独一条 metric，上千个 task 就上千条 series）。
_KNOWN_PREFIXES = (
    "/api/datasources/", "/api/tasks/", "/api/workflows/", "/api/workflow-runs/",
    "/api/runs/", "/api/history/", "/api/projects/", "/api/lineage/ai/jobs/",
    "/api/assets/table/",
)


def _normalize_path(path: str) -> str:
    for prefix in _KNOWN_PREFIXES:
        if path.startswith(prefix):
            tail = path[len(prefix):]
            if tail and "/" not in tail:
                return prefix + "*"
            if "/" in tail:
                head, rest = tail.split("/", 1)
                return prefix + "*/" + rest
    return path


class MetricsMiddleware:
    """ASGI middleware：拦每个 HTTP 请求记 count + duration。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        method = scope.get("method", "")
        path = _normalize_path(scope.get("path", ""))
        # /metrics 自身不埋点（避免 scrape 也被计入）
        if path == "/metrics":
            return await self.app(scope, receive, send)
        started = time.perf_counter()
        status_holder: dict[str, int] = {"status": 0}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = int(message.get("status") or 0)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed = time.perf_counter() - started
            status = str(status_holder["status"] or 0)
            http_requests_total.inc(path=path, method=method, status=status)
            http_request_duration_seconds.observe(elapsed, path=path, method=method)
