"""Wave 3 #13: run_index DAO —— compare run 的统一持久化记录。

替代「扫文件系统 + 从 run_id 猜 task_id」的脆弱逻辑(Phase 12 起 run_id
时间戳格式让反查失效)。

生命周期状态机:
- `reserved` —— admission 通过,run_id 已分配,worker 还没开始
- `running` —— worker 真正在执行 compare
- `success` / `failed` / `cancelled` —— 终态
- `aborted_guard` —— mid-run guard 中止(disk / memory / run quota)
- `deleted` —— run 文件已删,保留行做审计

调用约定:
- 所有 compare 入口(sync API / async job / workflow node)统一走
  `reserve()` → `mark_running()` → `finalize_*()` 三步
- finalize 写 disk_bytes / peak_rss_mb / error 等度量
- caller 用 `try/finally` 确保即使异常也调 finalize(failed 路径)

跟 jobs.py 的关系:`run_index.job_id` 对应异步 jobs.py 里的 job_id,可空
(同步 API 没 job)。replace 关系:run_index 是 compare 维度,jobs 是异步任务
维度。两者并存,run_index 给 guard / metric / quota 用,jobs 给前端
poll status / cancel 用。
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from app.services.sqlite_store import connect


logger = logging.getLogger(__name__)


RunStatus = Literal[
    "reserved", "running", "success", "failed", "cancelled",
    "aborted_guard", "deleted",
]
_TERMINAL = {"success", "failed", "cancelled", "aborted_guard", "deleted"}


@dataclass
class RunRecord:
    run_id: str
    job_id: str = ""
    task_id: str = ""
    workflow_run_id: str = ""
    project_id: str = ""
    owner_user_id: str = ""
    source_ds_id: str = ""
    target_ds_id: str = ""
    status: RunStatus = "reserved"
    requested_at: str = ""
    started_at: str = ""
    finished_at: str = ""
    result_format: str = "json"
    stream_compare: bool = False
    max_rows: int = 0
    estimated_bytes: int = 0
    disk_bytes: int = 0
    peak_rss_mb: float = 0.0
    guard_reason: str = ""
    result_path: str = ""
    error: str = ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def reserve(
    *,
    run_id: str,
    task_id: str = "",
    job_id: str = "",
    workflow_run_id: str = "",
    project_id: str = "",
    owner_user_id: str = "",
    source_ds_id: str = "",
    target_ds_id: str = "",
    result_format: str = "json",
    stream_compare: bool = False,
    max_rows: int = 0,
    estimated_bytes: int = 0,
) -> RunRecord:
    """admission 通过,登记 reservation。run_id 必填且全局唯一。

    若 run_id 已存在(同进程极少 race / 测试重用),走 REPLACE 覆盖。
    """
    rec = RunRecord(
        run_id=run_id, task_id=task_id, job_id=job_id, workflow_run_id=workflow_run_id,
        project_id=project_id, owner_user_id=owner_user_id,
        source_ds_id=source_ds_id, target_ds_id=target_ds_id,
        status="reserved", requested_at=_now(),
        result_format=result_format, stream_compare=stream_compare,
        max_rows=max_rows, estimated_bytes=estimated_bytes,
    )
    with connect() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO run_index (
                run_id, job_id, task_id, workflow_run_id, project_id, owner_user_id,
                source_ds_id, target_ds_id, status, requested_at,
                result_format, stream_compare, max_rows, estimated_bytes
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                rec.run_id, rec.job_id, rec.task_id, rec.workflow_run_id,
                rec.project_id, rec.owner_user_id,
                rec.source_ds_id, rec.target_ds_id,
                rec.status, rec.requested_at,
                rec.result_format, int(rec.stream_compare),
                rec.max_rows, rec.estimated_bytes,
            ),
        )
    return rec


def mark_running(run_id: str) -> None:
    """worker 开始执行 → 状态转 running + 记 started_at。"""
    with connect() as conn:
        conn.execute(
            "UPDATE run_index SET status='running', started_at=? "
            "WHERE run_id=? AND status='reserved'",
            (_now(), run_id),
        )


def finalize(
    run_id: str,
    *,
    status: RunStatus,
    disk_bytes: int = 0,
    peak_rss_mb: float = 0.0,
    guard_reason: str = "",
    result_path: str = "",
    error: str = "",
) -> None:
    """终态收口 —— success / failed / cancelled / aborted_guard 都走这。

    幂等:已是终态(_TERMINAL)的 run 不再覆盖(防 worker 完了 cancel 再追写)。
    """
    if status not in _TERMINAL:
        raise ValueError(f"finalize status must be terminal, got {status!r}")
    with connect() as conn:
        conn.execute(
            """UPDATE run_index SET
                status=?, finished_at=?, disk_bytes=?, peak_rss_mb=?,
                guard_reason=?, result_path=?, error=?
              WHERE run_id=? AND status NOT IN ('success','failed','cancelled','aborted_guard','deleted')""",
            (
                status, _now(), disk_bytes, peak_rss_mb,
                guard_reason, result_path, error, run_id,
            ),
        )


def mark_deleted(run_id: str) -> None:
    """run 文件被删 → 标 deleted,保留行做审计 + disk_bytes 仍可被 quota 折算时清 0。"""
    with connect() as conn:
        conn.execute(
            "UPDATE run_index SET status='deleted', disk_bytes=0 WHERE run_id=?",
            (run_id,),
        )


def get(run_id: str) -> RunRecord | None:
    with connect() as conn:
        cur = conn.execute("SELECT * FROM run_index WHERE run_id=?", (run_id,))
        row = cur.fetchone()
    return _row_to_record(row) if row else None


def list_by_project(project_id: str, *, status: str | None = None, limit: int = 100) -> list[RunRecord]:
    sql = "SELECT * FROM run_index WHERE project_id=?"
    params: list[Any] = [project_id]
    if status:
        sql += " AND status=?"
        params.append(status)
    sql += " ORDER BY requested_at DESC LIMIT ?"
    params.append(limit)
    with connect() as conn:
        cur = conn.execute(sql, tuple(params))
        return [_row_to_record(r) for r in cur.fetchall()]


def project_disk_used_mb(project_id: str) -> float:
    """per-project 已落盘 MB —— guard 跨 run 配额用。仅算非终态 + 终态非 deleted。

    替代老 `_project_disk_usage_mb()` 扫文件系统的实现。返回值含 reserved /
    running 状态(预留预估)+ 终态成功/失败/取消的真实 disk_bytes。
    """
    with connect() as conn:
        cur = conn.execute(
            "SELECT SUM(disk_bytes) AS total FROM run_index "
            "WHERE project_id=? AND status NOT IN ('deleted')",
            (project_id,),
        )
        row = cur.fetchone()
    total_bytes = (row["total"] if row and row["total"] else 0) or 0
    return total_bytes / (1024 ** 2)


def update_disk_bytes(run_id: str, disk_bytes: int) -> None:
    """mid-run 增量更新 disk 占用 —— 让 quota 检查能拿到当前真实字节,
    不必等 finalize。caller 可定期(每几千行)调一次。"""
    with connect() as conn:
        conn.execute(
            "UPDATE run_index SET disk_bytes=? WHERE run_id=?",
            (disk_bytes, run_id),
        )


def _row_to_record(row: sqlite3.Row) -> RunRecord:
    return RunRecord(
        run_id=row["run_id"],
        job_id=row["job_id"] or "",
        task_id=row["task_id"] or "",
        workflow_run_id=row["workflow_run_id"] or "",
        project_id=row["project_id"] or "",
        owner_user_id=row["owner_user_id"] or "",
        source_ds_id=row["source_ds_id"] or "",
        target_ds_id=row["target_ds_id"] or "",
        status=row["status"],
        requested_at=row["requested_at"] or "",
        started_at=row["started_at"] or "",
        finished_at=row["finished_at"] or "",
        result_format=row["result_format"] or "json",
        stream_compare=bool(row["stream_compare"]),
        max_rows=row["max_rows"] or 0,
        estimated_bytes=row["estimated_bytes"] or 0,
        disk_bytes=row["disk_bytes"] or 0,
        peak_rss_mb=row["peak_rss_mb"] or 0.0,
        guard_reason=row["guard_reason"] or "",
        result_path=row["result_path"] or "",
        error=row["error"] or "",
    )
