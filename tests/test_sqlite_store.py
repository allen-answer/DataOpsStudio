"""SQLite 抽象 + audit / jobs 切到 SQLite 的测试。

覆盖：
- sqlite_store.connect 自动建表 + WAL mode
- audit_logs 表的 CRUD（含索引）
- migrate_audit_jsonl 幂等（重复跑不重复导入）
- migrate_jobs_json 幂等
- audit.py 的 _append_log 双写 SQLite + jsonl，read_recent_logs 优先 SQLite
- jobs.py 的 _persist_jobs 写 SQLite + _load_jobs_from_disk 读 SQLite
- 启动时跑过的 jobs 重启变 failed（Phase 8 既定行为，迁移后保持）
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from app.services import sqlite_store


def test_connect_creates_db_and_tables(isolated_storage):
    with sqlite_store.connect() as conn:
        # 期望 audit_logs / jobs 两表存在
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        names = [row["name"] for row in cur.fetchall()]
    assert "audit_logs" in names
    assert "jobs" in names


def test_connect_uses_wal_mode(isolated_storage):
    with sqlite_store.connect() as conn:
        cur = conn.execute("PRAGMA journal_mode")
        mode = cur.fetchone()[0]
    assert str(mode).lower() == "wal"


def test_audit_insert_and_query(isolated_storage):
    with sqlite_store.connect() as conn:
        conn.execute(
            "INSERT INTO audit_logs (ts, user_id, username, method, path, status, request_id, "
            "resource, resource_id, project_id, extra) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("2026-05-04T10:00:00", "u1", "alice", "POST", "/api/tasks", 200,
             "req-abc", "tasks", "t1", "p1", ""),
        )
    with sqlite_store.connect() as conn:
        cur = conn.execute("SELECT * FROM audit_logs WHERE request_id=?", ("req-abc",))
        row = cur.fetchone()
    assert row is not None
    assert row["username"] == "alice"
    assert row["status"] == 200


def test_audit_index_supports_user_lookup(isolated_storage):
    """audit_logs_user_idx 让按 user_id 过滤走索引（非 EXPLAIN，但行为正确即可）。"""
    with sqlite_store.connect() as conn:
        for i in range(5):
            conn.execute(
                "INSERT INTO audit_logs (ts, user_id, username, method, path, status) "
                "VALUES (?,?,?,?,?,?)",
                (f"2026-05-04T10:0{i}:00", "u1", "alice", "POST", f"/api/x/{i}", 200),
            )
        conn.execute(
            "INSERT INTO audit_logs (ts, user_id, username, method, path, status) "
            "VALUES (?,?,?,?,?,?)",
            ("2026-05-04T10:00:00", "u2", "bob", "POST", "/api/x", 200),
        )
    with sqlite_store.connect() as conn:
        cur = conn.execute(
            "SELECT COUNT(*) AS n FROM audit_logs WHERE user_id=?",
            ("u1",),
        )
        n = cur.fetchone()["n"]
    assert n == 5


# ─── 迁移 ────────────────────────────────────────────────────────────────────


def test_migrate_audit_jsonl_imports_lines(isolated_storage, tmp_path):
    jsonl = tmp_path / "audit.jsonl"
    jsonl.write_text(
        json.dumps({"ts": "T1", "user_id": "u1", "username": "alice", "method": "POST",
                    "path": "/api/tasks", "status_code": 200, "resource": "tasks", "resource_id": "t1"}) + "\n"
        + json.dumps({"ts": "T2", "user_id": "u2", "method": "DELETE",
                      "path": "/api/tasks/x", "status_code": 204}) + "\n",
        encoding="utf-8",
    )
    imported = sqlite_store.migrate_audit_jsonl(jsonl)
    assert imported == 2
    with sqlite_store.connect() as conn:
        cur = conn.execute("SELECT COUNT(*) AS n FROM audit_logs")
        assert cur.fetchone()["n"] == 2


def test_migrate_audit_jsonl_idempotent(isolated_storage, tmp_path):
    """跑两次只导入一次（表非空时跳过）。"""
    jsonl = tmp_path / "audit.jsonl"
    jsonl.write_text(
        json.dumps({"ts": "T", "method": "POST", "path": "/api/x", "status_code": 200}) + "\n",
        encoding="utf-8",
    )
    n1 = sqlite_store.migrate_audit_jsonl(jsonl)
    n2 = sqlite_store.migrate_audit_jsonl(jsonl)
    assert n1 == 1
    assert n2 == 0  # 第二次跳过
    with sqlite_store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM audit_logs").fetchone()["n"] == 1


def test_migrate_audit_jsonl_skips_malformed_lines(isolated_storage, tmp_path):
    jsonl = tmp_path / "audit.jsonl"
    jsonl.write_text(
        json.dumps({"ts": "T1", "method": "POST", "path": "/api/x", "status_code": 200}) + "\n"
        + "{ broken half line\n"
        + json.dumps({"ts": "T2", "method": "DELETE", "path": "/api/y", "status_code": 204}) + "\n",
        encoding="utf-8",
    )
    imported = sqlite_store.migrate_audit_jsonl(jsonl)
    assert imported == 2  # 坏行被跳过


def test_migrate_jobs_json_imports(isolated_storage, tmp_path):
    jpath = tmp_path / "jobs.json"
    jpath.write_text(json.dumps({
        "abc123": {"job_id": "abc123", "kind": "compare_task", "status": "success",
                   "task_id": "t1", "started_at": "T", "finished_at": "T"},
        "xyz789": {"job_id": "xyz789", "kind": "workflow_run", "status": "running",
                   "workflow_id": "w1", "run_id": "r1"},
    }), encoding="utf-8")
    imported = sqlite_store.migrate_jobs_json(jpath)
    assert imported == 2
    with sqlite_store.connect() as conn:
        cur = conn.execute("SELECT id, status FROM jobs ORDER BY id")
        rows = cur.fetchall()
    assert len(rows) == 2
    by_id = {r["id"]: r["status"] for r in rows}
    assert by_id == {"abc123": "success", "xyz789": "running"}


def test_migrate_jobs_json_idempotent(isolated_storage, tmp_path):
    jpath = tmp_path / "jobs.json"
    jpath.write_text(json.dumps({
        "abc": {"job_id": "abc", "kind": "x", "status": "success"},
    }), encoding="utf-8")
    assert sqlite_store.migrate_jobs_json(jpath) == 1
    assert sqlite_store.migrate_jobs_json(jpath) == 0


def test_migrate_jobs_json_handles_list_format(isolated_storage, tmp_path):
    """jobs.json 历史格式是 list of dict（_persist_jobs 早期写的），需兼容。"""
    jpath = tmp_path / "jobs.json"
    jpath.write_text(json.dumps([
        {"job_id": "j1", "kind": "compare_task", "status": "success"},
        {"job_id": "j2", "kind": "workflow_run", "status": "running"},
    ]), encoding="utf-8")
    assert sqlite_store.migrate_jobs_json(jpath) == 2
    with sqlite_store.connect() as conn:
        cur = conn.execute("SELECT id, status FROM jobs ORDER BY id")
        rows = cur.fetchall()
    assert {r["id"]: r["status"] for r in rows} == {"j1": "success", "j2": "running"}


# ─── audit.py 集成 ──────────────────────────────────────────────────────────


def test_audit_append_writes_both_sqlite_and_jsonl(isolated_storage):
    """_append_log 同时写 SQLite + jsonl。"""
    from app.services import audit as audit_svc
    from app.models import AuditLogEntry

    entry = AuditLogEntry(
        ts="2026-05-04T10:00:00", user_id="u1", username="alice",
        method="POST", path="/api/tasks", status_code=200,
        resource_type="tasks", resource_id="t1",
    )
    audit_svc._append_log(entry, request_id="rid-test", project_id="proj-a")

    # 1. SQLite 有
    with sqlite_store.connect() as conn:
        cur = conn.execute("SELECT * FROM audit_logs WHERE request_id=?", ("rid-test",))
        row = cur.fetchone()
    assert row is not None
    assert row["project_id"] == "proj-a"
    assert row["resource"] == "tasks"

    # 2. jsonl 也有（用 audit_svc.AUDIT_LOG_FILE 拿到 conftest patch 后的路径，
    # 不要 from app.utils.paths import 那个会被 import 时定值的别名）
    patched_jsonl = audit_svc.AUDIT_LOG_FILE
    assert patched_jsonl.exists()
    text = patched_jsonl.read_text(encoding="utf-8")
    assert "rid-test" in text
    assert "alice" in text


def test_audit_read_recent_prefers_sqlite(isolated_storage):
    """read_recent_logs 走 SQLite 路径，按 ts 倒序返回。"""
    from app.services.audit import _append_log, read_recent_logs
    from app.models import AuditLogEntry

    for i in range(3):
        _append_log(AuditLogEntry(
            ts=f"2026-05-04T10:0{i}:00", user_id="u", method="POST",
            path=f"/api/x/{i}", status_code=200,
            resource_type="x", resource_id=str(i),
        ))
    rows = read_recent_logs(limit=10)
    assert len(rows) == 3
    # SQLite 路径返回 status_code（兼容字段名），按 id DESC 顺序
    assert rows[0]["path"] == "/api/x/2"
    assert rows[2]["path"] == "/api/x/0"


# ─── jobs.py 集成 ───────────────────────────────────────────────────────────


def test_persist_jobs_writes_sqlite(isolated_storage):
    """jobs 持久化到 SQLite —— 老 jobs.json 不再是 SoT。"""
    from app.services import jobs as jobs_mod
    jobs_mod._jobs.clear()
    jobs_mod._jobs["job-1"] = {
        "job_id": "job-1", "kind": "compare_task", "status": "success",
        "task_id": "t1", "started_at": "T0", "finished_at": "T1",
    }
    jobs_mod._persist_jobs()

    with sqlite_store.connect() as conn:
        cur = conn.execute("SELECT id, status, kind FROM jobs WHERE id=?", ("job-1",))
        row = cur.fetchone()
    assert row["status"] == "success"
    assert row["kind"] == "compare_task"


def test_load_jobs_from_disk_after_migrate(isolated_storage):
    """启动场景：老 jobs.json 存在 → 迁移到 SQLite → _jobs 拿到数据。"""
    from app.services import jobs as jobs_mod
    from app.utils.paths import JOBS_FILE

    JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    JOBS_FILE.write_text(json.dumps([
        {"job_id": "j1", "kind": "compare_task", "status": "success",
         "started_at": "T", "finished_at": "T"},
    ]), encoding="utf-8")
    # jobs.json 是 list 格式 —— migrate_jobs_json 只接 dict，所以这里通过
    # _load_jobs_from_disk 的 fallback 路径（直接读 jobs.json）
    jobs_mod._jobs.clear()
    jobs_mod._load_jobs_from_disk()
    assert "j1" in jobs_mod._jobs
    assert jobs_mod._jobs["j1"]["status"] == "success"


def test_load_jobs_from_disk_marks_running_as_failed(isolated_storage):
    """运行中 job 在重启时变 failed（Phase 8 决策，迁移后行为不变）。"""
    from app.services import jobs as jobs_mod
    jobs_mod._jobs.clear()
    jobs_mod._jobs["running-job"] = {
        "job_id": "running-job", "kind": "compare_task", "status": "running",
        "task_id": "t1", "started_at": "T", "stage": "compare",
    }
    jobs_mod._persist_jobs()
    jobs_mod._jobs.clear()  # 模拟重启

    jobs_mod._load_jobs_from_disk()
    assert jobs_mod._jobs["running-job"]["status"] == "failed"
    assert "service restarted" in jobs_mod._jobs["running-job"]["error"]
