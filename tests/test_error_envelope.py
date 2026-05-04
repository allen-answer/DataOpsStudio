"""Phase 9 Day 6：统一错误响应 + request_id middleware 测试。

覆盖：
- HTTPException 的 envelope 形状（code/message/detail/request_id/retryable）
- 422 ValidationError 的 envelope（code=validation_error）
- 500 unhandled exception 的 envelope（code=internal_error，retryable=True）
- request_id：客户端传 X-Request-Id 时透传；不传时后端 uuid 生成；response 必带 X-Request-Id
- request_id ContextVar 在 handler 里能访问到
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.api._error_handler import install, request_id_ctx


def _make_app() -> FastAPI:
    app = FastAPI()
    install(app)

    @app.get("/probe-ok")
    def ok():
        return {"ok": True}

    @app.get("/probe-http")
    def http_err():
        raise HTTPException(status_code=403, detail="禁止访问")

    @app.get("/probe-500")
    def crash():
        raise RuntimeError("boom")

    @app.get("/probe-rid")
    def show_rid():
        # 在 handler 里访问 ContextVar，证明 middleware 已经设上
        return {"rid": request_id_ctx.get()}

    class Body(BaseModel):
        n: int

    @app.post("/probe-validate")
    def validate(b: Body):
        return {"n": b.n}

    return app


def test_http_exception_envelope():
    client = TestClient(_make_app(), raise_server_exceptions=False)
    r = client.get("/probe-http")
    assert r.status_code == 403
    body = r.json()
    assert body["code"] == "http_403"
    assert body["message"] == "禁止访问"
    assert body["retryable"] is False
    assert body["request_id"]  # uuid 自动生成
    assert body["ai_translation"] is None
    assert body["suggestions"] == []
    # response header 也带 X-Request-Id
    assert r.headers["X-Request-Id"] == body["request_id"]


def test_unhandled_exception_envelope():
    client = TestClient(_make_app(), raise_server_exceptions=False)
    r = client.get("/probe-500")
    assert r.status_code == 500
    body = r.json()
    assert body["code"] == "internal_error"
    assert body["retryable"] is True  # 5xx 默认可重试
    assert "boom" in (body["detail"] or "")
    assert body["request_id"]


def test_validation_error_envelope():
    client = TestClient(_make_app(), raise_server_exceptions=False)
    r = client.post("/probe-validate", json={"n": "not-int"})
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "validation_error"
    assert body["message"] == "请求参数校验失败"
    assert isinstance(body["detail"], list)
    assert body["request_id"]


def test_request_id_passthrough_from_client_header():
    client = TestClient(_make_app(), raise_server_exceptions=False)
    r = client.get("/probe-ok", headers={"X-Request-Id": "client-trace-abc"})
    assert r.headers["X-Request-Id"] == "client-trace-abc"


def test_request_id_auto_generated_when_missing():
    client = TestClient(_make_app(), raise_server_exceptions=False)
    r1 = client.get("/probe-ok")
    r2 = client.get("/probe-ok")
    assert r1.headers["X-Request-Id"]
    assert r2.headers["X-Request-Id"]
    # 每个请求一个 uuid，互不相同
    assert r1.headers["X-Request-Id"] != r2.headers["X-Request-Id"]


def test_request_id_visible_in_handler_via_contextvar():
    client = TestClient(_make_app(), raise_server_exceptions=False)
    r = client.get("/probe-rid", headers={"X-Request-Id": "trace-from-test"})
    assert r.json()["rid"] == "trace-from-test"
    assert r.headers["X-Request-Id"] == "trace-from-test"


def test_envelope_4xx_not_retryable():
    """4xx 默认 retryable=False，5xx 默认 True。"""
    app = FastAPI()
    install(app)

    @app.get("/conflict")
    def c():
        raise HTTPException(status_code=409, detail="冲突")

    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/conflict")
    assert r.status_code == 409
    assert r.json()["retryable"] is False
