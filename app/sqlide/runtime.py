"""SQL Workbench v0.2 异步执行模型 + 查询中断。

设计:
- execute 不再 sync 阻塞返结果,改成「提交 → 立刻拿 execution_id + 短期 wait
  + 视情况 done/running」 + 客户端 poll
- ThreadPoolExecutor(独立池避免跟 jobs 模块的 max_workers=2 抢)跑 execute_sql
- cancel_requested 标志位:大多数 DB-API 驱动不支持中途真 cancel,我们的兜底
  策略是「执行后 / 读取结果前后再 check,如果 cancel 了就丢弃结果不展示」
  —— 这跟用户要求的「即使底层不支持也要标记 + check」语义一致
- TTL cleanup:每次 get/list 时顺便清掉 finished 且超过 1 小时的 entry,防
  in-memory map 无限增长

线程安全:
- _executions 由 _lock 保护
- Execution 字段在 worker thread 内写,handler thread 读 —— 通过 _lock 同步
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from app.sqlide.executor import execute_sql
from app.sqlide.models import ExecuteResponse


logger = logging.getLogger(__name__)


# v0.5 闭集对齐用户需求 #4。pending = 已提交还没拿到 worker 线程;running = worker
# 正在跑 cursor.execute;success/failed/cancelled = 终态。
# 旧 "done" 不再产生,但向后兼容时前端 store 仍把 done 当 success 处理。
ExecutionStatus = Literal["pending", "running", "success", "failed", "cancelled"]

# 默认查询超时(秒)。caller 可在 ExecuteRequest 里覆盖,有界 [1, 3600]。
DEFAULT_TIMEOUT_SECONDS = 300
MAX_TIMEOUT_SECONDS = 3600


@dataclass
class Execution:
    id: str
    user_id: str             # owner_user_id —— 别的用户不能 cancel
    datasource_id: str
    sql: str
    console_id: str = ""
    status: ExecutionStatus = "pending"
    cancel_requested: bool = False
    cancel_reason: str = ""   # "user" / "timeout" / "" —— history 字段区分用户主动取消 vs 超时
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    started_at: str = ""
    finished_at: str = ""
    result: ExecuteResponse | None = None
    error: str | None = None
    # MySQL 路径下记录底层 connection_id(SHOW PROCESSLIST 的 Id),用 KILL QUERY
    # 中断 in-flight 查询。其它方言空。
    connection_id: int | None = None
    # 防止 timer 跟 worker 终态 race:_finalized 一旦标 True,timer 不能再覆盖。
    _finalized: bool = field(default=False, repr=False)

    def to_envelope(self) -> dict[str, Any]:
        """API 响应 shape;result 用 model_dump 拍平。"""
        env: dict[str, Any] = {
            "execution_id": self.id,
            "status": self.status,
            "cancel_requested": self.cancel_requested,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "timeout_seconds": self.timeout_seconds,
        }
        if self.cancel_reason:
            env["cancel_reason"] = self.cancel_reason
        if self.result is not None:
            env["result"] = self.result.model_dump(mode="json")
        if self.error:
            env["error"] = self.error
        return env


_TTL_SECONDS = 3600  # finished 后保留 1 小时让客户端有时间 poll
_DEFAULT_SYNC_WAIT = 0.3  # execute 提交后服务端最多等多久看是否能立刻返完整 result

# 独立线程池,跟 jobs 模块的 max_workers=2 隔开,允许并行多查询。每用户在前端
# 通常不会同时跑多个;4 并发足够单用户 + 几个 admin 并行查
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="sql-workbench-")

_executions: dict[str, Execution] = {}
_lock = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _cleanup_old() -> None:
    """删 finished 且超过 _TTL_SECONDS 的 entry。每次 lookup 时顺便扫一遍。"""
    cutoff_ts = time.time() - _TTL_SECONDS
    with _lock:
        # finished_at 是 iso 字符串,直接字符序比对 cutoff 不行;转 epoch
        stale: list[str] = []
        for exe_id, exe in _executions.items():
            if exe.status in ("pending", "running") or not exe.finished_at:
                continue
            try:
                finished_ts = datetime.fromisoformat(exe.finished_at).timestamp()
            except ValueError:
                continue
            if finished_ts < cutoff_ts:
                stale.append(exe_id)
        for exe_id in stale:
            del _executions[exe_id]


def _try_driver_kill(exe: Execution) -> None:
    """v0.5:对支持的方言(MySQL)发 KILL QUERY 中断 in-flight 查询。

    走旁路新连接(不能用原查询的 connection 自己 kill 自己)。失败静默 —— 即使
    kill 不成功,worker 完成后还有 cancel_requested check + 丢弃结果兜底。
    Oracle/DM 走 callTimeout(已在 dialect 层),不在这里 kill。
    """
    if exe.connection_id is None:
        return
    try:
        from app.dbclients import factory as _factory
        from app.services.repositories import datasource_store
        ds = datasource_store.get(exe.datasource_id)
        if ds is None:
            return
        db_type = str(getattr(ds.db_type, "value", ds.db_type)).lower()
        if db_type != "mysql":
            return
        # 用同一 ds 起一条短连接发 KILL。fetch_rows 内部走 pool,会自己 borrow/release。
        kill_sql = f"KILL QUERY {int(exe.connection_id)}"
        _factory.fetch_rows(ds, kill_sql, max_rows=1, raise_on_overflow=False)
        logger.info("KILL QUERY %s sent for execution=%s", exe.connection_id, exe.id)
    except Exception as exc:
        logger.warning("driver kill failed for execution=%s: %s", exe.id, exc)


def start_execution(
    *,
    user_id: str,
    datasource: Any,
    sql: str,
    max_rows: int = 1000,
    console_id: str = "",
    sync_wait: float = _DEFAULT_SYNC_WAIT,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> Execution:
    """提交一次执行,在 sync_wait 秒内同步等待;到点未完成则返 running 或 pending。

    状态机:
      pending(刚提交,等线程)→ running(worker 拿到线程,cursor.execute 中)
        → success | failed | cancelled

    timeout_seconds:到时自动 request_cancel + 标 cancel_reason='timeout'。
    """
    timeout_s = max(1, min(int(timeout_seconds or DEFAULT_TIMEOUT_SECONDS), MAX_TIMEOUT_SECONDS))
    exe_id = uuid.uuid4().hex
    exe = Execution(
        id=exe_id, user_id=user_id, datasource_id=datasource.id, sql=sql,
        console_id=console_id, status="pending", started_at=_now(),
        timeout_seconds=timeout_s,
    )
    with _lock:
        _executions[exe_id] = exe

    # timeout timer:到点没结束就把 cancel_requested 翻 True + reason=timeout,
    # 并对支持的驱动发 KILL。timer 自己不直接改 status —— 让 worker / cancel
    # 路径走统一的"check cancel → 改 cancelled"逻辑,避免 race。
    def _on_timeout() -> None:
        with _lock:
            if exe._finalized or exe.status not in ("pending", "running"):
                return
            if exe.cancel_requested:
                return
            exe.cancel_requested = True
            exe.cancel_reason = "timeout"
        logger.info("execution %s timeout after %ds", exe.id, timeout_s)
        _try_driver_kill(exe)

    timer = threading.Timer(timeout_s, _on_timeout)
    timer.daemon = True

    def _run() -> None:
        # worker thread 拿到 → pending → running
        with _lock:
            if exe.cancel_requested:
                exe.status = "cancelled"
                exe.finished_at = _now()
                exe._finalized = True
                return
            exe.status = "running"
        try:
            resp = execute_sql(datasource, sql, max_rows=max_rows, _execution=exe)
            with _lock:
                if exe._finalized:
                    return
                if exe.cancel_requested:
                    # 完成后 cancel 已请求 —— 即便底层没真停,也丢弃结果不返用户
                    exe.status = "cancelled"
                    exe.result = None
                else:
                    exe.status = "success"
                    exe.result = resp
                exe.finished_at = _now()
                exe._finalized = True
        except Exception as exc:
            logger.exception("sql workbench execution worker failed")
            with _lock:
                if exe._finalized:
                    return
                # 区分 cancel + driver KILL 抛错(KILL QUERY 时 pymysql 抛
                # "Query execution was interrupted") vs 真正失败
                if exe.cancel_requested:
                    exe.status = "cancelled"
                    exe.result = None
                else:
                    exe.status = "failed"
                    exe.error = str(exc)
                exe.finished_at = _now()
                exe._finalized = True
        finally:
            timer.cancel()

    _executor.submit(_run)
    timer.start()

    # short-poll:等 sync_wait 秒看是否能立刻完成(典型快查 < 100ms 直接返 success)
    deadline = time.time() + max(0.0, sync_wait)
    while time.time() < deadline:
        with _lock:
            if exe.status not in ("pending", "running"):
                break
        time.sleep(0.02)

    _cleanup_old()
    return exe


def get_execution(exe_id: str) -> Execution | None:
    with _lock:
        return _executions.get(exe_id)


def request_cancel(exe_id: str, *, user_id: str) -> tuple[bool, str]:
    """设 cancel_requested,**并对支持的驱动发 KILL QUERY 中断 in-flight**。

    返 (ok, reason)。pending / running 都允许 cancel(用户极速点的边界场景)。
    """
    with _lock:
        exe = _executions.get(exe_id)
        if exe is None:
            return False, "execution 不存在或已过期"
        if exe.user_id != user_id:
            return False, "无权 cancel 他人的 execution"
        if exe.status not in ("pending", "running"):
            return False, f"execution 已是 {exe.status} 状态"
        if exe.cancel_requested:
            return True, ""  # 已经请求过,幂等
        exe.cancel_requested = True
        if not exe.cancel_reason:
            exe.cancel_reason = "user"
    # 锁外发 KILL —— fetch_rows 可能阻塞,不能 hold _lock 跟 worker 死锁
    _try_driver_kill(exe)
    return True, ""


# Tests 友好的内部 helper —— 让测试能注入 fake execute / 看到 in-flight
def _peek_all() -> dict[str, Execution]:
    with _lock:
        return dict(_executions)


def _reset_for_tests() -> None:
    """测试 fixture 清空 in-memory state 避免跨测试串扰。"""
    with _lock:
        _executions.clear()
