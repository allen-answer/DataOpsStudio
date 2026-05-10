"""Tests for app.services.workflow_history — WorkflowRun persistence."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.models import (
    NodeRunStatus, Workflow, WorkflowNode, WorkflowNodeType,
    WorkflowRun, WorkflowRunStatus, WorkflowNodeRun,
)


def _make_run(run_id: str, workflow_id: str = "wf1", status: str = "success",
              nodes: list[dict] | None = None) -> WorkflowRun:
    node_runs = []
    for spec in (nodes or [{"id": "n1", "status": "success"}]):
        node_runs.append(WorkflowNodeRun(
            node_id=spec["id"], type=WorkflowNodeType.COMPARE,
            status=NodeRunStatus(spec["status"]),
        ))
    return WorkflowRun(
        run_id=run_id, workflow_id=workflow_id, workflow_name="t",
        status=WorkflowRunStatus(status),
        nodes=node_runs, started_at="2026-05-01T10:00:00",
        finished_at="2026-05-01T10:00:05", elapsed_seconds=5.0,
    )


def test_persist_writes_json_and_reads_back(tmp_path, monkeypatch):
    from app.services import workflow_history

    monkeypatch.setattr(workflow_history, "WORKFLOW_RUNS_DIR", tmp_path)
    run = _make_run("run-001")

    path = workflow_history.persist_workflow_run(run)
    assert path.exists()
    loaded = workflow_history.get_workflow_run("run-001")
    assert loaded["run_id"] == "run-001"
    assert loaded["status"] == "success"


def test_list_filters_by_workflow_id_and_sorts_newest_first(tmp_path, monkeypatch):
    from app.services import workflow_history

    monkeypatch.setattr(workflow_history, "WORKFLOW_RUNS_DIR", tmp_path)

    older = _make_run("run-old", workflow_id="wf-a")
    older.started_at = "2026-04-30T10:00:00"
    newer = _make_run("run-new", workflow_id="wf-a")
    newer.started_at = "2026-05-02T10:00:00"
    other = _make_run("run-other", workflow_id="wf-b")

    workflow_history.persist_workflow_run(older)
    workflow_history.persist_workflow_run(newer)
    workflow_history.persist_workflow_run(other)

    wf_a_runs = workflow_history.list_workflow_runs("wf-a")
    assert [r["run_id"] for r in wf_a_runs] == ["run-new", "run-old"]

    all_runs = workflow_history.list_workflow_runs("")
    assert {r["run_id"] for r in all_runs} == {"run-new", "run-old", "run-other"}


def test_list_summarizes_node_status_counts(tmp_path, monkeypatch):
    from app.services import workflow_history

    monkeypatch.setattr(workflow_history, "WORKFLOW_RUNS_DIR", tmp_path)
    run = _make_run("run-mixed", nodes=[
        {"id": "n1", "status": "success"},
        {"id": "n2", "status": "success"},
        {"id": "n3", "status": "failed"},
        {"id": "n4", "status": "skipped"},
    ])
    workflow_history.persist_workflow_run(run)

    summary = workflow_history.list_workflow_runs("")[0]
    assert summary["node_count"] == 4
    assert summary["node_status_counts"] == {"success": 2, "failed": 1, "skipped": 1}


def test_get_returns_none_for_unknown_run(tmp_path, monkeypatch):
    from app.services import workflow_history

    monkeypatch.setattr(workflow_history, "WORKFLOW_RUNS_DIR", tmp_path)
    assert workflow_history.get_workflow_run("nope") is None


def test_get_rejects_path_traversal(tmp_path, monkeypatch):
    """run_id values like '../something' must not let callers read arbitrary
    files outside the runs directory."""
    from app.services import workflow_history

    monkeypatch.setattr(workflow_history, "WORKFLOW_RUNS_DIR", tmp_path)
    # Plant a sibling file the caller shouldn't reach.
    (tmp_path.parent / "secret.json").write_text('{"x": 1}', encoding="utf-8")

    assert workflow_history.get_workflow_run("../secret") is None


def test_delete_removes_file(tmp_path, monkeypatch):
    from app.services import workflow_history

    monkeypatch.setattr(workflow_history, "WORKFLOW_RUNS_DIR", tmp_path)
    run = _make_run("run-del")
    workflow_history.persist_workflow_run(run)
    assert workflow_history.get_workflow_run("run-del") is not None

    workflow_history.delete_workflow_run("run-del")
    assert workflow_history.get_workflow_run("run-del") is None


def test_delete_also_removes_artifacts_directory(tmp_path, monkeypatch):
    """每个 run 的 artifacts（excel_export 等节点的产物）落到
    WORKFLOW_RUNS_DIR/<run_id>/exports/，删 run 时必须连带 rmtree
    整个 <run_id>/ 目录，否则磁盘上残留垃圾文件。"""
    from app.services import workflow_history

    monkeypatch.setattr(workflow_history, "WORKFLOW_RUNS_DIR", tmp_path)
    run = _make_run("run-with-arts")
    workflow_history.persist_workflow_run(run)

    # 模拟 excel_export runner 生成一个产物
    arts_dir = tmp_path / "run-with-arts" / "exports"
    arts_dir.mkdir(parents=True)
    artifact_file = arts_dir / "report.xlsx"
    artifact_file.write_bytes(b"fake xlsx")
    assert artifact_file.exists()

    workflow_history.delete_workflow_run("run-with-arts")

    assert workflow_history.get_workflow_run("run-with-arts") is None
    assert not (tmp_path / "run-with-arts").exists()


def test_delete_run_without_artifacts_directory_succeeds(tmp_path, monkeypatch):
    """run 没产生过 artifacts（比如纯 compare 节点的 workflow）→
    artifacts 目录不存在，delete 不能因此失败。"""
    from app.services import workflow_history

    monkeypatch.setattr(workflow_history, "WORKFLOW_RUNS_DIR", tmp_path)
    run = _make_run("run-clean")
    workflow_history.persist_workflow_run(run)

    workflow_history.delete_workflow_run("run-clean")
    assert workflow_history.get_workflow_run("run-clean") is None


def test_delete_rejects_path_traversal(tmp_path, monkeypatch):
    """run_id='../foo' 不应该把 artifacts dir 解析到 WORKFLOW_RUNS_DIR
    外的位置——校验失败时直接 KeyError，不可 rmtree 任何东西。"""
    from app.services import workflow_history

    monkeypatch.setattr(workflow_history, "WORKFLOW_RUNS_DIR", tmp_path)
    with pytest.raises(KeyError):
        workflow_history.delete_workflow_run("../escape")


def test_delete_unknown_raises_key_error(tmp_path, monkeypatch):
    from app.services import workflow_history

    monkeypatch.setattr(workflow_history, "WORKFLOW_RUNS_DIR", tmp_path)
    with pytest.raises(KeyError):
        workflow_history.delete_workflow_run("ghost")


def test_persist_failure_logs_but_does_not_raise(tmp_path, monkeypatch, caplog):
    """Persistence is best-effort — must never break the workflow run."""
    from app.services import workflow_history

    monkeypatch.setattr(workflow_history, "WORKFLOW_RUNS_DIR", tmp_path)
    run = _make_run("run-x")

    with patch.object(workflow_history.Path, "write_text", side_effect=PermissionError("denied")):
        # Should not raise.
        path = workflow_history.persist_workflow_run(run)
    assert "run-x" in str(path)


def test_list_does_not_read_all_jsons_when_limit_set(tmp_path, monkeypatch):
    """有 200 个 run 但 caller 只要 50 时不应该全量解析每个 JSON。

    Phase 11 优化：先按文件 mtime DESC 排序（fs metadata 免读），只读
    `limit*2+10` 个候选 JSON，再按 started_at 二次排序。
    """
    from app.services import workflow_history
    monkeypatch.setattr(workflow_history, "WORKFLOW_RUNS_DIR", tmp_path)

    # 造 200 个 run，每个 started_at 递增（run-001 最早，run-200 最新）
    for i in range(1, 201):
        run = _make_run(f"run-{i:03d}", workflow_id=f"wf-{i % 3}")
        run.started_at = f"2026-01-{(i % 28) + 1:02d}T10:00:{i % 60:02d}"
        workflow_history.persist_workflow_run(run)

    read_count = {"n": 0}
    real_read = workflow_history.Path.read_text

    def counting_read(self, *args, **kwargs):
        read_count["n"] += 1
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(workflow_history.Path, "read_text", counting_read)

    runs = workflow_history.list_workflow_runs(limit=50)
    assert len(runs) == 50
    # 200 文件中只读了 read_budget = 2*50+10 = 110 个，远少于 200
    assert read_count["n"] <= 110, f"read 太多 JSON：{read_count['n']}（应 ≤ 110）"


def test_run_payloads_cache_caps_slots_via_fifo(tmp_path, monkeypatch):
    """Caller hits get_cached_run_payloads with many distinct run_limit values
    (e.g. ?run_limit=N from the API). Without a slot cap each value would
    park its own list of full payloads in memory.
    """
    from app.services import workflow_history

    monkeypatch.setattr(workflow_history, "WORKFLOW_RUNS_DIR", tmp_path)
    workflow_history.invalidate_run_payloads_cache()
    monkeypatch.setattr(workflow_history, "_PAYLOAD_CACHE_MAX_SLOTS", 4)

    # Seed at least one run so list_workflow_runs returns non-empty.
    workflow_history.persist_workflow_run(_make_run("run-seed"))

    for run_limit in range(1, 9):  # 8 distinct keys, cap at 4
        workflow_history.get_cached_run_payloads(run_limit)

    keys = list(workflow_history._run_payloads_cache.keys())
    assert len(keys) == 4
    # FIFO eviction: earliest 1..4 dropped, 5..8 retained.
    assert keys == [5, 6, 7, 8]
    workflow_history.invalidate_run_payloads_cache()


def test_run_payloads_cache_recent_key_lands_at_tail(tmp_path, monkeypatch):
    """Re-fetching an existing run_limit must move it to the tail so it isn't
    evicted ahead of strictly-older entries when the cache is full.
    """
    from app.services import workflow_history

    monkeypatch.setattr(workflow_history, "WORKFLOW_RUNS_DIR", tmp_path)
    workflow_history.invalidate_run_payloads_cache()
    monkeypatch.setattr(workflow_history, "_PAYLOAD_CACHE_MAX_SLOTS", 3)
    monkeypatch.setattr(workflow_history, "_PAYLOAD_TTL", 0.0)  # force re-fetch path

    workflow_history.persist_workflow_run(_make_run("run-seed"))

    workflow_history.get_cached_run_payloads(10)
    workflow_history.get_cached_run_payloads(20)
    workflow_history.get_cached_run_payloads(30)
    # Touch 10 → moves to tail.
    workflow_history.get_cached_run_payloads(10)
    # Add 4th distinct → evict head, which is now 20.
    workflow_history.get_cached_run_payloads(40)

    keys = list(workflow_history._run_payloads_cache.keys())
    assert 10 in keys, "recently-touched key should survive"
    assert 20 not in keys, "stale-since-touch key should evict"
    workflow_history.invalidate_run_payloads_cache()


def test_list_respects_started_at_order_when_mtime_diverges(tmp_path, monkeypatch):
    """mtime 跟 started_at 不一致时，最终顺序按 started_at（语义正确）。

    模拟「跑得久的任务后写盘」：run-old started_at 早，但 mtime 晚（更晚落盘）。
    """
    import os
    from app.services import workflow_history
    monkeypatch.setattr(workflow_history, "WORKFLOW_RUNS_DIR", tmp_path)

    # run-A：started_at 较新（2026-05-02），mtime 较旧
    a = _make_run("run-A")
    a.started_at = "2026-05-02T10:00:00"
    workflow_history.persist_workflow_run(a)
    a_path = tmp_path / "run-A.json"
    # 把 mtime 调到很早
    early = 1_700_000_000  # 2023 年附近
    os.utime(a_path, (early, early))

    # run-B：started_at 较旧（2026-04-30），但刚落盘，mtime 是现在（最新）
    b = _make_run("run-B")
    b.started_at = "2026-04-30T10:00:00"
    workflow_history.persist_workflow_run(b)

    # mtime 顺序：B 新 → A 旧；started_at 顺序：A 新 → B 旧
    runs = workflow_history.list_workflow_runs(limit=50)
    # 最终结果按 started_at —— A 应在前
    assert [r["run_id"] for r in runs] == ["run-A", "run-B"]
