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
from typing import Any

from app.sqlide.executor import execute_sql
from app.sqlide.models import ExecuteResponse


logger = logging.getLogger(__name__)


ExecutionStatus = str  # "running" / "done" / "failed" / "cancelled"


@dataclass
class Execution:
    id: str
    user_id: str             # owner_user_id —— 别的用户不能 cancel
    datasource_id: str
    sql: str
    console_id: str = ""
    status: ExecutionStatus = "running"
    cancel_requested: bool = False
    started_at: str = ""
    finished_at: str = ""
    result: ExecuteResponse | None = None
    error: str | None = None

    def to_envelope(self) -> dict[str, Any]:
        """API 响应 shape;result 用 model_dump 拍平。"""
        env: dict[str, Any] = {
            "execution_id": self.id,
            "status": self.status,
            "cancel_requested": self.cancel_requested,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }
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
            if exe.status == "running" or not exe.finished_at:
                continue
            try:
                finished_ts = datetime.fromisoformat(exe.finished_at).timestamp()
            except ValueError:
                continue
            if finished_ts < cutoff_ts:
                stale.append(exe_id)
        for exe_id in stale:
            del _executions[exe_id]


def start_execution(
    *,
    user_id: str,
    datasource: Any,
    sql: str,
    max_rows: int = 1000,
    console_id: str = "",
    sync_wait: float = _DEFAULT_SYNC_WAIT,
) -> Execution:
    """提交一次执行,在 sync_wait 秒内同步等待;到点未完成则返 running。

    返回 Execution 对象给 API 层 to_envelope。
    """
    exe_id = uuid.uuid4().hex
    exe = Execution(
        id=exe_id, user_id=user_id, datasource_id=datasource.id, sql=sql,
        console_id=console_id, status="running", started_at=_now(),
    )
    with _lock:
        _executions[exe_id] = exe

    def _run() -> None:
        # 执行前再 check 一次 cancel(用户极快连点 + cancel 的边界)
        if exe.cancel_requested:
            with _lock:
                exe.status = "cancelled"
                exe.finished_at = _now()
            return
        try:
            resp = execute_sql(datasource, sql, max_rows=max_rows)
            with _lock:
                if exe.cancel_requested:
                    # 完成后再 check;cancel 了丢弃结果不展示给用户
                    exe.status = "cancelled"
                    exe.result = None
                else:
                    exe.status = "done"
                    exe.result = resp
                exe.finished_at = _now()
        except Exception as exc:  # pragma: no cover —— executor 内部不抛
            logger.exception("sql workbench execution worker failed")
            with _lock:
                exe.status = "failed"
                exe.error = str(exc)
                exe.finished_at = _now()

    _executor.submit(_run)

    # short-poll:等 sync_wait 秒看是否能立刻完成(典型快查 < 100ms 直接返 done)
    deadline = time.time() + max(0.0, sync_wait)
    while time.time() < deadline:
        with _lock:
            if exe.status != "running":
                break
        time.sleep(0.02)

    _cleanup_old()
    return exe


def get_execution(exe_id: str) -> Execution | None:
    with _lock:
        return _executions.get(exe_id)


def request_cancel(exe_id: str, *, user_id: str) -> tuple[bool, str]:
    """设 cancel_requested。底层驱动多数不支持真中途取消,只标记并在
    worker 完成时丢弃结果。

    返 (ok, reason)。
    """
    with _lock:
        exe = _executions.get(exe_id)
        if exe is None:
            return False, "execution 不存在或已过期"
        if exe.user_id != user_id:
            return False, "无权 cancel 他人的 execution"
        if exe.status != "running":
            return False, f"execution 已是 {exe.status} 状态"
        exe.cancel_requested = True
        return True, ""


# Tests 友好的内部 helper —— 让测试能注入 fake execute / 看到 in-flight
def _peek_all() -> dict[str, Execution]:
    with _lock:
        return dict(_executions)


def _reset_for_tests() -> None:
    """测试 fixture 清空 in-memory state 避免跨测试串扰。"""
    with _lock:
        _executions.clear()
