"""Wave 3 #13:run_index DAO + 统一入口测试。

覆盖 reservation → mark_running → finalize 生命周期 + per-project disk quota
基于 SQL SUM(替代扫文件系统)+ runner / workflow / async 三入口都走同一函数
的事实(通过单跑一个 task 后查 run_index 验证)。
"""
from __future__ import annotations

import pytest

from app.services import run_index


def _reserve(rid: str, project_id: str = "", **kwargs) -> None:
    run_index.reserve(
        run_id=rid,
        task_id=kwargs.get("task_id", "t1"),
        project_id=project_id,
        owner_user_id=kwargs.get("owner_user_id", ""),
        result_format=kwargs.get("result_format", "json"),
        stream_compare=kwargs.get("stream_compare", False),
        max_rows=kwargs.get("max_rows", 0),
        estimated_bytes=kwargs.get("estimated_bytes", 0),
    )


def test_reserve_then_get_roundtrip(isolated_storage):
    _reserve("r-1", project_id="p1", task_id="t1")
    rec = run_index.get("r-1")
    assert rec is not None
    assert rec.run_id == "r-1"
    assert rec.task_id == "t1"
    assert rec.project_id == "p1"
    assert rec.status == "reserved"
    assert rec.requested_at  # iso timestamp


def test_mark_running_only_from_reserved(isolated_storage):
    _reserve("r-2")
    run_index.mark_running("r-2")
    rec = run_index.get("r-2")
    assert rec.status == "running"
    assert rec.started_at


def test_finalize_success_writes_metrics(isolated_storage):
    _reserve("r-3", project_id="p1")
    run_index.mark_running("r-3")
    run_index.finalize(
        "r-3", status="success",
        disk_bytes=2 * 1024 * 1024,
        peak_rss_mb=42.5,
        result_path="/results/r-3.json",
    )
    rec = run_index.get("r-3")
    assert rec.status == "success"
    assert rec.disk_bytes == 2 * 1024 * 1024
    assert rec.peak_rss_mb == 42.5
    assert rec.finished_at
    assert rec.result_path == "/results/r-3.json"


def test_finalize_idempotent_on_terminal(isolated_storage):
    """已终态的 run 再 finalize 不覆盖(防 worker 完后 cancel 再追写)。"""
    _reserve("r-4")
    run_index.finalize("r-4", status="success", disk_bytes=100)
    # 再 finalize 不该覆盖
    run_index.finalize("r-4", status="failed", disk_bytes=999, error="should-not-overwrite")
    rec = run_index.get("r-4")
    assert rec.status == "success"
    assert rec.disk_bytes == 100
    assert rec.error == ""


def test_finalize_rejects_non_terminal_status(isolated_storage):
    _reserve("r-5")
    with pytest.raises(ValueError, match="terminal"):
        run_index.finalize("r-5", status="running")


def test_aborted_guard_status(isolated_storage):
    _reserve("r-6", project_id="p1")
    run_index.mark_running("r-6")
    run_index.finalize(
        "r-6", status="aborted_guard",
        guard_reason="DiskWatermarkExceeded",
        error="disk free 1GB < 5GB threshold",
        disk_bytes=512 * 1024,
    )
    rec = run_index.get("r-6")
    assert rec.status == "aborted_guard"
    assert rec.guard_reason == "DiskWatermarkExceeded"


# ─── per-project disk quota via SUM(disk_bytes) ──────────────────────────────

def test_project_disk_used_mb_sums_by_project(isolated_storage):
    """两个 project 各两条 run,SUM 准确。"""
    _reserve("ra-1", project_id="pA")
    _reserve("ra-2", project_id="pA")
    _reserve("rb-1", project_id="pB")
    run_index.finalize("ra-1", status="success", disk_bytes=10 * 1024 * 1024)  # 10 MB
    run_index.finalize("ra-2", status="success", disk_bytes=20 * 1024 * 1024)  # 20 MB
    run_index.finalize("rb-1", status="success", disk_bytes=5 * 1024 * 1024)   # 5 MB

    assert run_index.project_disk_used_mb("pA") == pytest.approx(30.0, rel=0.001)
    assert run_index.project_disk_used_mb("pB") == pytest.approx(5.0, rel=0.001)
    assert run_index.project_disk_used_mb("nonexistent") == 0.0


def test_project_disk_excludes_deleted(isolated_storage):
    """deleted 的 run 不算入配额(让用户删 run 后能恢复配额)。"""
    _reserve("rd-1", project_id="pC")
    run_index.finalize("rd-1", status="success", disk_bytes=100 * 1024 * 1024)
    assert run_index.project_disk_used_mb("pC") == pytest.approx(100.0, rel=0.001)
    run_index.mark_deleted("rd-1")
    assert run_index.project_disk_used_mb("pC") == 0.0


def test_project_disk_includes_active_runs(isolated_storage):
    """reserved / running 的 run 也算 —— estimated_bytes 给 disk_bytes 0 时不算入,
    但 finalize 写了字节就立即可见。本测试用 update_disk_bytes 模拟 mid-run 增量。"""
    _reserve("re-1", project_id="pD")
    run_index.mark_running("re-1")
    run_index.update_disk_bytes("re-1", 50 * 1024 * 1024)
    assert run_index.project_disk_used_mb("pD") == pytest.approx(50.0, rel=0.001)


# ─── list_by_project basic shape ─────────────────────────────────────────────

def test_list_by_project_orders_by_requested_at_desc(isolated_storage):
    import time
    _reserve("rx-1", project_id="pE")
    time.sleep(1.0)
    _reserve("rx-2", project_id="pE")
    runs = run_index.list_by_project("pE")
    assert len(runs) == 2
    # rx-2 后申请 → 在前
    assert runs[0].run_id == "rx-2"
    assert runs[1].run_id == "rx-1"


def test_list_by_project_status_filter(isolated_storage):
    _reserve("rs-1", project_id="pF")
    _reserve("rs-2", project_id="pF")
    run_index.finalize("rs-1", status="success")
    run_index.finalize("rs-2", status="failed")
    successes = run_index.list_by_project("pF", status="success")
    assert [r.run_id for r in successes] == ["rs-1"]
