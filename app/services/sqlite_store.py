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

        -- S1.A：aspect 变更轨迹。每次 upsert / delete 落一条 immutable 历史。
        -- 给 admin 看"谁把 PII 等级从 high 改成 low 了"。append-only，不删。
        -- old_value / new_value 存完整 JSON value（不只 diff），方便审计 diff
        -- 算法在前端 / SQL 看心情；语义清晰：insert 时 old_value=''；delete 时
        -- new_value=''；update 两边都填。
        CREATE TABLE IF NOT EXISTS asset_aspect_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset_kind TEXT NOT NULL,
            asset_name TEXT NOT NULL,
            aspect_type TEXT NOT NULL,
            project_id TEXT NOT NULL DEFAULT '',
            action TEXT NOT NULL,              -- insert / update / delete
            old_value TEXT NOT NULL DEFAULT '',
            new_value TEXT NOT NULL DEFAULT '',
            changed_at TEXT NOT NULL DEFAULT '',
            changed_by TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS asset_aspect_history_asset_idx
            ON asset_aspect_history(asset_kind, asset_name, changed_at DESC);
        CREATE INDEX IF NOT EXISTS asset_aspect_history_user_idx
            ON asset_aspect_history(changed_by, changed_at DESC);
        CREATE INDEX IF NOT EXISTS asset_aspect_history_time_idx
            ON asset_aspect_history(changed_at DESC);

        -- 安全加固：JWT token 吊销表。logout 把当前 token 的 jti 写进来，
        -- get_current_user 校验时命中即视为无效 —— 让登出 / 泄露的 token
        -- 立刻失效，不必等它自然 exp。exp 留着给 _prune 清过期记录。
        CREATE TABLE IF NOT EXISTS revoked_tokens (
            jti TEXT PRIMARY KEY,
            exp INTEGER NOT NULL,                -- token 自身过期时间戳（prune 用）
            revoked_at TEXT NOT NULL DEFAULT '',
            user_id TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS revoked_tokens_exp_idx ON revoked_tokens(exp);

        -- 安全加固：refresh token 表（refresh rotation）。
        -- login 同时签短 access (30min~8h) + 长 refresh (7d)；POST /api/auth/refresh
        -- 拿老 refresh 换新 access+refresh 对，老 refresh 标 replaced_by=新jti。
        -- 重放检测：若 replaced_by 非空的 token 再被用 → 视为盗用，整条链 revoke。
        CREATE TABLE IF NOT EXISTS refresh_tokens (
            jti TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            exp INTEGER NOT NULL,                -- refresh JWT 自身过期时间戳
            issued_at TEXT NOT NULL DEFAULT '',
            replaced_by TEXT,                    -- 已被 rotation 替换的新 jti；NULL = 当前 active
            revoked_at TEXT                      -- 显式 revoke 时间；非 NULL = 已失效（logout / 重放检出）
        );
        CREATE INDEX IF NOT EXISTS refresh_tokens_user_idx ON refresh_tokens(user_id);
        CREATE INDEX IF NOT EXISTS refresh_tokens_exp_idx ON refresh_tokens(exp);

        -- Phase 14:下载 token 一次性消费 nonce 表。issue_download_token 给每个
        -- token 带 uuid jti,GET /api/downloads/<token> 先调 consume_download_nonce
        -- (jti) —— 第一次返 True 即标已消费,第二次起返 False 让 endpoint 410 Gone。
        -- 防止 token 在 TTL 内被截获重复下载(尤其是大 parquet 桶 / Excel)。
        CREATE TABLE IF NOT EXISTS download_nonces (
            jti TEXT PRIMARY KEY,
            consumed_at TEXT NOT NULL,
            exp INTEGER NOT NULL,               -- token 自身 exp(prune 用)
            user_id TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS download_nonces_exp_idx ON download_nonces(exp);

        -- Phase 14 P1-2: slow-sql plan history。每次 /api/slow-sql/analyze
        -- 跑完自动落一条,前端 plan-diff 拿同 sql_hash 的最近 2 条对比改写
        -- 前后的 plan(type / rows / Extra / cost 变化)。sql_hash = sha256(归一化 SQL)
        -- 让"格式调一调"的语义相同改写归为同条历史线。
        CREATE TABLE IF NOT EXISTS slow_sql_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            datasource_id TEXT NOT NULL,
            dialect TEXT NOT NULL,
            sql_text TEXT NOT NULL,
            sql_hash TEXT NOT NULL,
            scenario_id TEXT NOT NULL DEFAULT '',
            workload_name TEXT NOT NULL DEFAULT '',
            plan_json TEXT NOT NULL,
            issues_json TEXT NOT NULL,
            suggestions_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS slow_sql_plans_hash_idx
          ON slow_sql_plans(datasource_id, sql_hash, ts DESC);
        CREATE INDEX IF NOT EXISTS slow_sql_plans_scenario_idx
          ON slow_sql_plans(scenario_id, workload_name, ts DESC);
        CREATE INDEX IF NOT EXISTS slow_sql_plans_ts_idx
          ON slow_sql_plans(ts DESC);

        -- Wave 3 #13: run_index — 所有 compare run 的统一持久化记录
        -- 替代「扫文件系统 + 从 run_id 猜 task_id」的脆弱逻辑(Phase 12 起 run_id
        -- 时间戳格式让反查失效)。给 resource_guard 算 per-project disk quota
        -- 一个可靠的事实来源,也给 Phase 5 metrics 提供 peak_rss / disk_bytes 数据
        --
        -- 生命周期:reserved(admission ok 但 worker 还没开始) → running(worker
        -- 拿到线程) → success | failed | cancelled | aborted_guard(mid-run guard
        -- 中止) → deleted(run 文件被删,保留行做审计)
        CREATE TABLE IF NOT EXISTS run_index (
            run_id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL DEFAULT '',
            task_id TEXT NOT NULL DEFAULT '',
            workflow_run_id TEXT NOT NULL DEFAULT '',
            project_id TEXT NOT NULL DEFAULT '',
            owner_user_id TEXT NOT NULL DEFAULT '',
            source_ds_id TEXT NOT NULL DEFAULT '',
            target_ds_id TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'reserved',
            requested_at TEXT NOT NULL DEFAULT '',
            started_at TEXT NOT NULL DEFAULT '',
            finished_at TEXT NOT NULL DEFAULT '',
            result_format TEXT NOT NULL DEFAULT 'json',
            stream_compare INTEGER NOT NULL DEFAULT 0,
            max_rows INTEGER NOT NULL DEFAULT 0,
            estimated_bytes INTEGER NOT NULL DEFAULT 0,
            disk_bytes INTEGER NOT NULL DEFAULT 0,
            peak_rss_mb REAL NOT NULL DEFAULT 0,
            guard_reason TEXT NOT NULL DEFAULT '',
            result_path TEXT NOT NULL DEFAULT '',
            error TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS run_index_project_idx ON run_index(project_id, status);
        CREATE INDEX IF NOT EXISTS run_index_owner_idx ON run_index(owner_user_id, status);
        CREATE INDEX IF NOT EXISTS run_index_task_idx ON run_index(task_id, requested_at DESC);
        CREATE INDEX IF NOT EXISTS run_index_status_idx ON run_index(status);
        CREATE INDEX IF NOT EXISTS run_index_requested_idx ON run_index(requested_at DESC);
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
