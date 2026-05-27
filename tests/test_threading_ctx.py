"""submit_with_context 单测 —— 验证 ContextVar 跨 ThreadPoolExecutor 传递。"""
from __future__ import annotations

import contextvars
from concurrent.futures import ThreadPoolExecutor

from app.utils.threading_ctx import submit_with_context


def test_submit_propagates_contextvar():
    """caller set 了 ContextVar,worker 线程内 .get() 应能拿到。"""
    var: contextvars.ContextVar[str] = contextvars.ContextVar("test_var", default="default")
    var.set("caller-value")

    def worker():
        return var.get()

    with ThreadPoolExecutor(max_workers=1) as ex:
        result = submit_with_context(ex, worker).result()

    assert result == "caller-value"


def test_default_submit_loses_contextvar():
    """对照组:不用 helper 时,worker 拿到的是 default —— 这就是 P0-5 修的 bug。"""
    var: contextvars.ContextVar[str] = contextvars.ContextVar("test_var2", default="default")
    var.set("caller-value")

    def worker():
        return var.get()

    with ThreadPoolExecutor(max_workers=1) as ex:
        result = ex.submit(worker).result()

    # 默认 ThreadPoolExecutor.submit 不传 ContextVar → worker 拿 default
    assert result == "default"


def test_request_id_ctx_propagates():
    """实际场景:request_id_ctx 跨 worker 仍能 get 到原值。"""
    from app.api._error_handler import request_id_ctx
    request_id_ctx.set("test-rid-abc-123")

    def worker():
        return request_id_ctx.get()

    with ThreadPoolExecutor(max_workers=1) as ex:
        result = submit_with_context(ex, worker).result()

    assert result == "test-rid-abc-123"


def test_passes_args_kwargs_correctly():
    """submit_with_context 必须保留原 submit 的 args/kwargs 语义。"""
    def worker(a, b, c=None):
        return (a, b, c)

    with ThreadPoolExecutor(max_workers=1) as ex:
        result = submit_with_context(ex, worker, 1, 2, c=3).result()

    assert result == (1, 2, 3)


def test_worker_modifications_not_leaked_to_caller():
    """contextvar 在 worker 内的修改不应回流到 caller(各线程独立快照)。"""
    var: contextvars.ContextVar[str] = contextvars.ContextVar("test_var3", default="caller-orig")
    var.set("caller-orig")

    def worker():
        var.set("worker-modified")
        return var.get()

    with ThreadPoolExecutor(max_workers=1) as ex:
        worker_view = submit_with_context(ex, worker).result()

    assert worker_view == "worker-modified"
    # caller 看到的值没变
    assert var.get() == "caller-orig"
