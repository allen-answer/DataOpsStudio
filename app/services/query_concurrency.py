"""同用户 × 同数据源 in-flight 查询并发限制 (P1-3)。

**为什么需要**:同一用户在 Workbench 开 10 个 tab 跑同一数据源的查询 → 把
dbclients/pool.py 的 LIFO 池(max_size=4)瞬间打爆,后续查询全阻塞。Workbench
用户没法靠"自觉"控制,需要服务端兜底。

**策略**:
- 维护 `(user_id, datasource_id) → in_flight_count` 计数表
- 提交查询前 acquire(),超阈值返 False
- 查询结束 release()
- 用 context manager 包,避免漏 release

**阈值默认 3**:在 pool 单 ds max_size=4 之下,留 1 个空位给其他用户 / 系统任务
(metadata refresh / scheduler 等)。

**enforce 模式**:跟 resource_guard 一样,prod 模式 enforce(超就拒);dev/test 模式
warn(只 log 不拒,方便本地多 tab 并行调试)。
"""
from __future__ import annotations

import logging
import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class _ConcurrencyState:
    counters: dict[tuple[str, str], int] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)


_state = _ConcurrencyState()


class QueryConcurrencyExceeded(RuntimeError):
    """**预期错** —— 同用户 × 数据源 in-flight 超阈值。caller 应返 429 + 日志 warning,
    不打 traceback(跟 RowOverflowError 同 P1-2 口径)。"""


def _limit_per_user_ds() -> int:
    raw = os.getenv("DATAOPS_QUERY_CONCURRENCY_PER_USER_DS", "3").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 3


def _enforce_mode() -> bool:
    """prod 模式 enforce(超即抛);其他模式 warn(只 log)。

    可单独 override:DATAOPS_QUERY_CONCURRENCY_ENFORCE=true/false
    """
    raw = os.getenv("DATAOPS_QUERY_CONCURRENCY_ENFORCE", "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return os.getenv("DATAOPS_ENV", "").strip().lower() in {"prod", "production"}


def get_in_flight(user_id: str, datasource_id: str) -> int:
    """供 /metrics / admin 查询当前 in-flight 计数。"""
    with _state.lock:
        return _state.counters.get((user_id, datasource_id), 0)


def get_in_flight_snapshot() -> dict[str, int]:
    """全部 (user, ds) → count 快照,给 admin endpoint / debug 用。"""
    with _state.lock:
        # key tuple → "user_id|datasource_id" 便于 JSON 序列化
        return {f"{u}|{ds}": c for (u, ds), c in _state.counters.items() if c > 0}


def try_acquire(user_id: str, datasource_id: str) -> tuple[bool, str]:
    """同步增 counter。返 (ok, reason)。ok=False 表示超阈值且 enforce 模式开。

    跨 sync/async 边界用此 API:caller 立即调 try_acquire,通过后启动异步 worker,
    worker 在 finally 调 release。
    """
    if not user_id or not datasource_id:
        return True, ""

    limit = _limit_per_user_ds()
    key = (user_id, datasource_id)
    enforce = _enforce_mode()

    with _state.lock:
        current = _state.counters.get(key, 0)
        if current >= limit:
            if enforce:
                logger.warning(
                    "query concurrency limit reached user=%s ds=%s in_flight=%d limit=%d",
                    user_id, datasource_id, current, limit,
                )
                return False, f"该用户在该数据源已有 {current} 个查询在跑(上限 {limit}),请稍后重试"
            else:
                logger.info(
                    "query concurrency soft limit user=%s ds=%s in_flight=%d limit=%d (warn-only)",
                    user_id, datasource_id, current, limit,
                )
        _state.counters[key] = current + 1
    return True, ""


def release(user_id: str, datasource_id: str) -> None:
    """对应 try_acquire 的 release;幂等(多调一次不会变负数)。"""
    if not user_id or not datasource_id:
        return
    key = (user_id, datasource_id)
    with _state.lock:
        n = _state.counters.get(key, 0)
        if n <= 1:
            _state.counters.pop(key, None)
        else:
            _state.counters[key] = n - 1


@contextmanager
def acquire_slot(user_id: str, datasource_id: str):
    """获取一个 in-flight slot。

    Raises:
        QueryConcurrencyExceeded: 该 (user_id, datasource_id) 已有 in_flight >= 阈值
            且 enforce 模式开启

    Usage:
        with acquire_slot(user.id, ds.id):
            result = fetch_rows_with_schema(ds, sql)
    """
    if not user_id or not datasource_id:
        # 缺关键 key 不做限制(系统任务 / scheduler 路径),直接放行
        yield
        return

    limit = _limit_per_user_ds()
    key = (user_id, datasource_id)
    enforce = _enforce_mode()

    with _state.lock:
        current = _state.counters.get(key, 0)
        if current >= limit:
            if enforce:
                logger.warning(
                    "query concurrency limit reached user=%s ds=%s in_flight=%d limit=%d",
                    user_id, datasource_id, current, limit,
                )
                raise QueryConcurrencyExceeded(
                    f"该用户在该数据源已有 {current} 个查询在跑(上限 {limit}),请稍后重试"
                )
            else:
                logger.info(
                    "query concurrency soft limit user=%s ds=%s in_flight=%d limit=%d (warn-only)",
                    user_id, datasource_id, current, limit,
                )
        _state.counters[key] = current + 1

    try:
        yield
    finally:
        with _state.lock:
            n = _state.counters.get(key, 0)
            if n <= 1:
                _state.counters.pop(key, None)
            else:
                _state.counters[key] = n - 1


def reset_for_tests() -> None:
    """测试用 —— 清空计数。"""
    with _state.lock:
        _state.counters.clear()
