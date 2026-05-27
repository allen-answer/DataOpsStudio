"""ThreadPoolExecutor 跨线程传递 contextvars 的辅助函数。

**为什么需要**:Python ThreadPoolExecutor.submit 默认**不**继承 caller 线程的
ContextVar。我们用 ContextVar 存 request_id(Phase 9 Day 6,见 app/api/_error_handler.py),
所以 background worker 里 logger 拿到的 request_id 是空 —— P0-5 用户报告的
"rid 为空"就是这个根因。

**用法**:
    from app.utils.threading_ctx import submit_with_context
    submit_with_context(executor, my_worker_fn, arg1, arg2)
    # vs 原本的 executor.submit(my_worker_fn, arg1, arg2)

**原理**:`contextvars.copy_context()` 拷贝 caller 当前的全部 ContextVar 快照,
然后 `ctx.run(fn, *args, **kwargs)` 在 worker 线程里以这份快照为基础执行 —— 这样
worker 线程内 `request_id_ctx.get()` 仍然返回提交时的 request_id,而不是默认值。

**注意**:contextvar 是**快照** —— worker 内对 ContextVar 的修改不会回传到 caller
(这是设计意图,各线程独立)。我们只用来传 request_id 这种"只读上下文",这正合适。
"""
from __future__ import annotations

import contextvars
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, TypeVar

T = TypeVar("T")


def submit_with_context(
    executor: ThreadPoolExecutor,
    fn: Callable[..., T],
    *args: Any,
    **kwargs: Any,
) -> Future[T]:
    """同 ThreadPoolExecutor.submit,但 worker 继承 caller 当前的 ContextVar 快照。"""
    ctx = contextvars.copy_context()
    return executor.submit(ctx.run, fn, *args, **kwargs)
