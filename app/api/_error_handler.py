"""Phase 9 Day 6：统一错误响应 + request_id 注入。

设计要点：
- 所有 HTTPException 和未捕获 Exception 走同一个 envelope：
  `{code, message, detail, request_id, retryable, ai_translation?, suggestions?}`
- `request_id` 用 ContextVar 在 middleware 进入时生成 uuid（如果 header 里
  没有 `X-Request-Id`），让任意层的 logger / response 能拿到同一个 ID
- AI 翻译改"按需"：默认 off；用户在 admin AIConfig 显式开启 `enable_auto_translation`
  才在 envelope 注入 `ai_translation` 字段。前端不再自动调 `/api/ai/translate-error`，
  改成错误卡片底部"AI 解释"按钮显式触发。
"""
from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)

# request_id —— 在 middleware 入口设置，logger / 业务代码任意层都能 .get()
request_id_ctx: ContextVar[str] = ContextVar("request_id", default="")


class ErrorEnvelope(BaseModel):
    """统一错误响应 envelope。所有 4xx / 5xx 都走这个 schema。"""

    code: str = Field(..., description="machine-readable 错误码，例如 http_400 / http_500 / validation_error")
    message: str = Field(..., description="人类可读简短描述（中文）")
    detail: Any = Field(default=None, description="原始 detail，pydantic ValidationError 这里是 list[dict]")
    request_id: str = Field(default="", description="请求 ID，便于跨日志 trace")
    retryable: bool = Field(default=False, description="客户端是否可以重试（5xx 默认 True，4xx 默认 False）")
    ai_translation: str | None = Field(default=None, description="AI 翻译后的中文解释（仅当 enable_auto_translation=True 时填）")
    suggestions: list[str] = Field(default_factory=list, description="AI 给出的排查 / 修复建议")


class RequestIdMiddleware:
    """每个请求生成 / 接收 X-Request-Id，写入 ContextVar 并加到 response header。

    用纯 ASGI middleware 而不是 BaseHTTPMiddleware：后者在 `finally` 里 reset
    token 会让 unhandled exception handler（在 ServerErrorMiddleware 里）拿不
    到 request_id。这里不显式 reset —— 每个请求独立的 asyncio task 有自己的
    contextvar 副本，task 结束时自动 GC。
    """

    HEADER_NAME = "X-Request-Id"

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        # 从 headers 抽 X-Request-Id（小写 key），不存在则生成 uuid
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
        rid = headers.get(self.HEADER_NAME.lower()) or uuid.uuid4().hex
        request_id_ctx.set(rid)

        async def send_with_header(message):
            if message["type"] == "http.response.start":
                # 给 response 加 X-Request-Id header
                existing = list(message.get("headers") or [])
                existing.append((self.HEADER_NAME.encode("latin-1"), rid.encode("latin-1")))
                message = {**message, "headers": existing}
            await send(message)

        await self.app(scope, receive, send_with_header)


def _is_5xx(status_code: int) -> bool:
    return 500 <= status_code < 600


def _build_envelope(
    *,
    status_code: int,
    code: str,
    message: str,
    detail: Any = None,
) -> dict[str, Any]:
    """统一构造 envelope dict。`request_id` 来自 ContextVar；retryable 默认按 5xx 判断。"""
    return {
        "code": code,
        "message": message,
        "detail": detail,
        "request_id": request_id_ctx.get() or "",
        "retryable": _is_5xx(status_code),
        "ai_translation": None,
        "suggestions": [],
    }


async def _http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail
    # message：人类可读简短描述。detail：保留原始值（string / dict / list 都
    # 行），让老 tests 和老前端代码 `response.json()["detail"]` 继续可用。
    if isinstance(detail, str):
        message = detail
    elif isinstance(detail, dict):
        message = str(detail.get("message") or detail.get("detail") or "请求出错")
    else:
        message = str(detail or "请求出错")
    envelope = _build_envelope(
        status_code=exc.status_code,
        code=f"http_{exc.status_code}",
        message=message[:500],
        detail=detail,
    )
    # X-Request-Id 由 RequestIdMiddleware 在 ASGI 层注入到 response header，
    # 这里不再重复加（重复会变成 `id, id` 逗号分隔）。
    return JSONResponse(envelope, status_code=exc.status_code, headers=dict(exc.headers or {}))


async def _validation_exception_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    envelope = _build_envelope(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        code="validation_error",
        message="请求参数校验失败",
        detail=jsonable_encoder(exc.errors()),
    )
    return JSONResponse(envelope, status_code=status.HTTP_422_UNPROCESSABLE_ENTITY)


async def _unhandled_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled exception request_id=%s", request_id_ctx.get())
    envelope = _build_envelope(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code="internal_error",
        message="服务器内部错误，请重试或联系管理员",
        detail=str(exc)[:500],
    )
    return JSONResponse(envelope, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)


def install(app: FastAPI) -> None:
    """挂载 middleware + 注册三类异常处理。

    main.py 里调 `install(app)` 即可。等价于：
    - app.add_middleware(RequestIdMiddleware)
    - app.add_exception_handler(HTTPException, _http_exception_handler)
    - app.add_exception_handler(RequestValidationError, _validation_exception_handler)
    - app.add_exception_handler(Exception, _unhandled_exception_handler)
    """
    app.add_middleware(RequestIdMiddleware)
    app.add_exception_handler(HTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)


__all__ = [
    "ErrorEnvelope",
    "RequestIdMiddleware",
    "install",
    "request_id_ctx",
]
