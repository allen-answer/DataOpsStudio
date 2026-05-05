"""Phase 9 ADR 6 落地：高并发写 / 重启关键状态切 SQLite。

设计要点：
- 用 stdlib `sqlite3`，零额外依赖。WAL mode 让读写并发不卡（reader 不阻 writer）
- 单进程多线程安全：每次操作开一个 connection，避免线程间共享 connection 导致
  `Recursive use of cursors not allowed` / `SQLite objects created in a thread can
  only be used in that same thread` 这类错。SQLite 自身的文件锁保证 ACID。
- Schema 在 `_init_schema()` 集中声明（idempotent，多次 init 不冲突）。要加新表
  来这里加 CREATE TABLE IF NOT EXISTS
- 一次性迁移：`migrate_jsonl_to_audit_logs(...)` / `migrate_jobs_json_to_jobs(...)`
  在首次启动时把老 JSON 数据吸进来，不破坏老备份；导入后老文件保留（让回滚仍能
  用 jsonl tail / cat audit）

哪些数据落 SQLite：
- `audit_logs`：原 logs/audit.jsonl，append-only 高并发写
- `jobs`：原 config/jobs.json，重启时丢运行中状态的源头

不落 SQLite（继续 JsonStore）：
- datasource / task / workflow / workflow_template / project / user：低写并发，
  人工编辑友好（可以 vi 改 JSON 救急），暂留
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from app.utils.paths import SQLITE_DB_FILE


logger = logging.getLogger(__name__)


# 单进程内单例 connection 工厂。每个线程自己开 connection，由 sqlite3 库的
# 默认 `check_same_thread=True` 保护；`_init_lock` 只保护"第一次 init schema"
# 的 race。
_init_lock = threading.Lock()
_initialized: dict[Path, bool] = {}


def _ensure_initialized(db_path: Path) -> None:
    """第一次访问时建库 + 表 + WAL mode。线程安全（双检锁）。"""
    if _initialized.get(db_path):
        return
    with _init_lock:
        if _initialized.get(db_path):
            return
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        try:
            # WAL：reader 不阻 writer。`synchronous=NORMAL` 在 WAL 下是安全的，
            # 写性能高很多（默认 FULL 每次 fsync 太重）
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            _init_schema(conn)
            conn.commit()
        finally:
            conn.close()
        _initialized[db_path] = True


def _init_schema(conn: sqlite3.Connection) -> None:
    """所有表的 CREATE IF NOT EXISTS 都集中在这。"""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            user_id TEXT NOT NULL DEFAULT '',
            username TEXT NOT NULL DEFAULT '',
            method TEXT NOT NULL,
            path TEXT NOT NULL,
            status INTEGER NOT NULL DEFAULT 0,
            request_id TEXT NOT NULL DEFAULT '',
            resource TEXT NOT NULL DEFAULT '',
            resource_id TEXT NOT NULL DEFAULT '',
            project_id TEXT NOT NULL DEFAULT '',
            extra TEXT NOT NULL DEFAULT ''  -- JSON blob 给少用字段（如 ip / user_agent / payload diff）
        );
        CREATE INDEX IF NOT EXISTS audit_logs_ts_idx ON audit_logs(ts DESC);
        CREATE INDEX IF NOT EXISTS audit_logs_user_idx ON audit_logs(user_id, ts DESC);
        CREATE INDEX IF NOT EXISTS audit_logs_request_idx ON audit_logs(request_id);

        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,                  -- compare_task / workflow_run
            status TEXT NOT NULL,                -- pending / running / succeeded / failed / cancelled
            task_id TEXT NOT NULL DEFAULT '',
            workflow_id TEXT NOT NULL DEFAULT '',
            run_id TEXT NOT NULL DEFAULT '',
            started_at TEXT NOT NULL DEFAULT '',
            finished_at TEXT NOT NULL DEFAULT '',
            cancel_requested INTEGER NOT NULL DEFAULT 0,
            payload TEXT NOT NULL DEFAULT ''     -- 完整 dict 序列化的 JSON（向后兼容）
        );
        CREATE INDEX IF NOT EXISTS jobs_status_idx ON jobs(status);
        CREATE INDEX IF NOT EXISTS jobs_started_idx ON jobs(started_at DESC);

        -- Phase 10 enhancement：资产 classification / metadata aspects。
        -- 跟 DataHub / Atlan custom aspect 思路对齐 —— 一个 (asset_kind, asset_name)
        -- 资产可挂多个不同 aspect_type（owner / pii / sla / sensitive / tag /
        -- business_term），每个 aspect_type 的具体 value 形态由
        -- config/asset_aspects.yml 定义（schema 外置，加新 type 不动表结构）。
        CREATE TABLE IF NOT EXISTS asset_aspects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset_kind TEXT NOT NULL,            -- table / task / field（前期仅 table）
            asset_name TEXT NOT NULL,            -- 表名 ods.t_users，含 schema
            aspect_type TEXT NOT NULL,           -- owner / pii / sla / sensitive / tag / business_term
            value TEXT NOT NULL DEFAULT '',      -- JSON value（structure 由 yml schema 决定）
            project_id TEXT NOT NULL DEFAULT '', -- 资产可见范围（空 = 全局）
            updated_at TEXT NOT NULL DEFAULT '',
            updated_by TEXT NOT NULL DEFAULT '', -- username 谁改的
            UNIQUE(asset_kind, asset_name, aspect_type, project_id)
        );
        CREATE INDEX IF NOT EXISTS asset_aspects_asset_idx ON asset_aspects(asset_kind, asset_name);
        CREATE INDEX IF NOT EXISTS asset_aspects_type_idx ON asset_aspects(aspect_type);
    """)


@contextmanager
def connect(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """打开 connection 的 context manager —— 自动 commit / rollback / close。

    用法：
        with connect() as conn:
            conn.execute("INSERT ...", (...))
            # 离开 with 自动 commit；抛异常则 rollback
    """
    path = db_path or SQLITE_DB_FILE
    _ensure_initialized(path)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ─── 一次性迁移（启动时跑一次）────────────────────────────────────────────────


def migrate_audit_jsonl(jsonl_path: Path) -> int:
    """把老的 audit.jsonl 导入 audit_logs 表。返回导入条数。

    幂等：老文件保持原样（不删），导入只在表为空时跑（避免重复导入产生重影）。
    """
    if not jsonl_path.exists():
        return 0
    with connect() as conn:
        cur = conn.execute("SELECT COUNT(*) AS n FROM audit_logs")
        existing = cur.fetchone()["n"]
    if existing > 0:
        return 0
    imported = 0
    rows: list[tuple] = []
    try:
        for line in jsonl_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            rows.append((
                str(rec.get("ts") or ""),
                str(rec.get("user_id") or ""),
                str(rec.get("username") or ""),
                str(rec.get("method") or ""),
                str(rec.get("path") or ""),
                int(rec.get("status") or 0),
                str(rec.get("request_id") or ""),
                str(rec.get("resource") or ""),
                str(rec.get("resource_id") or ""),
                str(rec.get("project_id") or ""),
                json.dumps({k: v for k, v in rec.items()
                            if k not in {"ts", "user_id", "username", "method",
                                         "path", "status", "request_id", "resource",
                                         "resource_id", "project_id"}}, ensure_ascii=False)
                if any(k for k in rec if k not in {"ts", "user_id", "username", "method",
                                                    "path", "status", "request_id", "resource",
                                                    "resource_id", "project_id"})
                else "",
            ))
    except Exception as exc:
        logger.warning("audit migration: failed reading %s: %s", jsonl_path, exc)
        return 0
    if not rows:
        return 0
    with connect() as conn:
        conn.executemany(
            "INSERT INTO audit_logs (ts, user_id, username, method, path, status, request_id, "
            "resource, resource_id, project_id, extra) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        imported = len(rows)
    logger.info("audit migration: imported %d rows from %s", imported, jsonl_path)
    return imported


def migrate_jobs_json(jobs_path: Path) -> int:
    """把老的 jobs.json 导入 jobs 表。返回导入条数。

    兼容两种格式：
    - **list of dict**（老 `_persist_jobs` 历史格式）：每个 dict 自带 `job_id`
    - **dict of dict**：键是 job_id
    """
    if not jobs_path.exists():
        return 0
    try:
        data = json.loads(jobs_path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    # 归一化：两种格式都拿到 (job_id, payload) iterator
    items: list[tuple[str, dict]] = []
    if isinstance(data, list):
        for entry in data:
            if not isinstance(entry, dict):
                continue
            jid = str(entry.get("job_id") or "")
            if jid:
                items.append((jid, entry))
    elif isinstance(data, dict):
        for jid, payload in data.items():
            if isinstance(payload, dict):
                items.append((str(jid), payload))
    else:
        return 0
    if not items:
        return 0

    with connect() as conn:
        cur = conn.execute("SELECT COUNT(*) AS n FROM jobs")
        existing = cur.fetchone()["n"]
    if existing > 0:
        return 0

    rows: list[tuple] = []
    for jid, payload in items:
        rows.append((
            jid,
            str(payload.get("kind") or ""),
            str(payload.get("status") or "pending"),
            str(payload.get("task_id") or ""),
            str(payload.get("workflow_id") or ""),
            str(payload.get("run_id") or ""),
            str(payload.get("started_at") or ""),
            str(payload.get("finished_at") or ""),
            1 if payload.get("cancel_requested") else 0,
            json.dumps(payload, ensure_ascii=False),
        ))
    with connect() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO jobs (id, kind, status, task_id, workflow_id, run_id, "
            "started_at, finished_at, cancel_requested, payload) VALUES (?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
    logger.info("jobs migration: imported %d jobs from %s", len(rows), jobs_path)
    return len(rows)


__all__ = [
    "connect",
    "migrate_audit_jsonl",
    "migrate_jobs_json",
]
