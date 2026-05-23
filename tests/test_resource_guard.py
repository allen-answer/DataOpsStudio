"""resource_guard 规则引擎测试 —— 每条 deny/queue/allow 规则正反例 + 优先级 +
dry-run vs enforce。`evaluate()` 是纯函数，host/queue/config 全注入，无需 fixture。
"""
from __future__ import annotations

import pytest

from app.models import CompareTask, RunLimits
from app.services.resource_guard import (
    DiskWatermarkExceeded,
    GuardConfig,
    GuardDecision,
    HostSnapshot,
    QueueState,
    RunQuotaExceeded,
    TaskShape,
    check_disk_critical,
    check_run_quota,
    decision_detail,
    evaluate,
    guard_compare_run,
    task_shape,
)


# ─── builders（健康默认值）────────────────────────────────────────────────────


def _host(**kw) -> HostSnapshot:
    base = dict(disk_free_gb=500.0, disk_usage_pct=40.0, mem_available_mb=8000, mem_available_pct=60.0)
    base.update(kw)
    return HostSnapshot(**base)


def _queue(**kw) -> QueueState:
    base = dict(compare_running=0, export_running=0, queue_depth=0)
    base.update(kw)
    return QueueState(**base)


def _shape(**kw) -> TaskShape:
    base = dict(task_id="t1", project_id="", max_rows=1000, result_format="json", stream_compare=False)
    base.update(kw)
    return TaskShape(**base)


def _eval(shape=None, host=None, queue=None, config=None) -> GuardDecision:
    return evaluate(
        shape or _shape(),
        host=host or _host(),
        queue=queue or _queue(),
        config=config or GuardConfig(),
    )


def _compare_task(*, project_id: str = "", **limits_kw) -> CompareTask:
    """构造一个能过 CompareTask 校验的 SQL 单模式任务（SQL kind 需要
    source_id / source_sql / target_id / key_columns）。"""
    return CompareTask(
        id="t", name="t",
        source_id="ds", target_id="ds",
        source_sql="select 1",
        key_columns=["id"],
        project_id=project_id,
        limits=RunLimits(**limits_kw),
    )


# ─── allow ──────────────────────────────────────────────────────────────────


def test_small_healthy_task_is_allowed():
    decision = _eval()
    assert decision.decision == "allow"
    assert decision.reasons == []
    assert decision.risk_level == "low"


# ─── 静态规则：deny ─────────────────────────────────────────────────────────


def test_json_large_result_denied():
    decision = _eval(_shape(result_format="json", max_rows=400_000))
    assert decision.decision == "deny"
    assert "json_large_result" in {r.code for r in decision.reasons}


def test_json_under_limit_allowed():
    decision = _eval(_shape(result_format="json", max_rows=200_000))
    assert decision.decision == "allow"


def test_huge_requires_parquet_denied():
    decision = _eval(_shape(result_format="json", max_rows=2_000_000))
    assert decision.decision == "deny"
    assert "huge_requires_parquet" in {r.code for r in decision.reasons}


def test_huge_with_parquet_under_stream_threshold_allowed():
    decision = _eval(_shape(result_format="parquet", max_rows=2_000_000, stream_compare=False))
    assert decision.decision == "allow"


def test_huge_requires_stream_denied():
    decision = _eval(_shape(result_format="parquet", max_rows=6_000_000, stream_compare=False))
    assert decision.decision == "deny"
    reason = next(r for r in decision.reasons if r.code == "huge_requires_stream")
    assert reason.severity == "critical"


def test_huge_with_stream_compare_allowed():
    decision = _eval(_shape(result_format="parquet", max_rows=6_000_000, stream_compare=True))
    assert decision.decision == "allow"
    assert decision.risk_level == "low"


# ─── 静态规则：queue ────────────────────────────────────────────────────────


def test_same_persist_large_queues():
    decision = _eval(_shape(
        result_format="parquet", max_rows=2_000_000, stream_compare=True, persist_same_bucket=True,
    ))
    assert decision.decision == "queue"
    assert "same_persist_large" in {r.code for r in decision.reasons}
    assert decision.retry_after_seconds == 30


# ─── 主机健康 ────────────────────────────────────────────────────────────────


def test_disk_low_free_denied():
    decision = _eval(host=_host(disk_free_gb=3.0))
    assert decision.decision == "deny"
    assert "disk_low_watermark" in {r.code for r in decision.reasons}


def test_disk_high_usage_denied():
    decision = _eval(host=_host(disk_usage_pct=90.0))
    assert decision.decision == "deny"
    assert "disk_low_watermark" in {r.code for r in decision.reasons}


def test_mem_pressure_denied():
    decision = _eval(host=_host(mem_available_pct=10.0))
    assert decision.decision == "deny"
    assert "mem_pressure" in {r.code for r in decision.reasons}


def test_mem_rule_skipped_when_unknown():
    # 非 Linux dev 机 /proc/meminfo 不可用 → mem 字段 None → 规则跳过，不误拦
    decision = _eval(host=_host(mem_available_mb=None, mem_available_pct=None))
    assert decision.decision == "allow"


# ─── 队列上限 ────────────────────────────────────────────────────────────────


def test_queue_full_denied():
    decision = _eval(queue=_queue(queue_depth=50))
    assert decision.decision == "deny"
    assert "queue_full" in {r.code for r in decision.reasons}


def test_compare_cap_queues():
    decision = _eval(queue=_queue(compare_running=2))
    assert decision.decision == "queue"
    assert "compare_cap" in {r.code for r in decision.reasons}


def test_export_cap_queues():
    decision = _eval(queue=_queue(export_running=1))
    assert decision.decision == "queue"
    assert "export_cap" in {r.code for r in decision.reasons}


def test_project_cap_queues():
    decision = _eval(_shape(project_id="p1"), queue=_queue(per_project_running=2))
    assert decision.decision == "queue"
    assert "project_cap" in {r.code for r in decision.reasons}


def test_project_cap_skipped_for_global_task():
    # 全局任务（project_id 空）没有项目 scope —— 不触发 project_cap
    decision = _eval(_shape(project_id=""), queue=_queue(per_project_running=9))
    assert "project_cap" not in {r.code for r in decision.reasons}


def test_datasource_cap_queues():
    decision = _eval(queue=_queue(per_datasource_running=2))
    assert decision.decision == "queue"
    assert "datasource_cap" in {r.code for r in decision.reasons}


def test_queue_snapshot_counts_per_project_and_datasource(isolated_storage):
    from app.models import CompareTaskCreate, RunLimits
    from app.services import jobs as jobs_mod
    from app.services.repositories import task_store
    from app.services.resource_guard import queue_snapshot

    t1 = task_store.create(CompareTaskCreate(
        name="t1", source_id="dsX", target_id="dsX", source_sql="select 1",
        key_columns=["id"], project_id="pA", limits=RunLimits(),
    ))
    t2 = task_store.create(CompareTaskCreate(
        name="t2", source_id="dsY", target_id="dsY", source_sql="select 1",
        key_columns=["id"], project_id="pA", limits=RunLimits(),
    ))
    jobs_mod._jobs["j1"] = {"job_id": "j1", "kind": "compare", "task_id": t1.id, "status": "running"}
    jobs_mod._jobs["j2"] = {"job_id": "j2", "kind": "compare", "task_id": t2.id, "status": "queued"}

    snap = queue_snapshot(project_id="pA", datasource_ids=("dsX",))
    assert snap.per_project_running == 2     # t1 + t2 都属 pA
    assert snap.per_datasource_running == 1  # 只有 t1 用 dsX
    assert snap.compare_running == 2


# ─── 决策优先级：deny 压过 queue ────────────────────────────────────────────


def test_deny_beats_queue():
    # json_large_result(deny) + compare_cap(queue) 同时命中 → 决策取 deny
    decision = _eval(
        _shape(result_format="json", max_rows=400_000),
        queue=_queue(compare_running=5),
    )
    assert decision.decision == "deny"
    codes = {r.code for r in decision.reasons}
    assert "json_large_result" in codes and "compare_cap" in codes


# ─── dry-run vs enforce ─────────────────────────────────────────────────────


def test_dry_run_decision_computed_but_not_enforced():
    decision = _eval(_shape(result_format="json", max_rows=400_000), config=GuardConfig(enforce=False))
    assert decision.decision == "deny"   # 决策照算
    assert decision.enforced is False    # 但不强制


def test_enforce_mode_marks_enforced():
    decision = _eval(_shape(result_format="json", max_rows=400_000), config=GuardConfig(enforce=True))
    assert decision.decision == "deny"
    assert decision.enforced is True


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("DATAOPS_GUARD_ENFORCE", "true")
    monkeypatch.setenv("DATAOPS_MAX_COMPARE_JOBS", "7")
    monkeypatch.setenv("DATAOPS_MAX_JOBS_PER_PROJECT", "4")
    monkeypatch.setenv("DATAOPS_MAX_QUERIES_PER_DATASOURCE", "3")
    config = GuardConfig.from_env()
    assert config.enforce is True
    assert config.max_compare_jobs == 7
    assert config.max_jobs_per_project == 4
    assert config.max_queries_per_datasource == 3


def test_config_from_env_defaults(monkeypatch):
    monkeypatch.delenv("DATAOPS_GUARD_ENFORCE", raising=False)
    config = GuardConfig.from_env()
    assert config.enforce is False
    assert config.max_compare_jobs == 2


# ─── task_shape / decision_detail / guard_compare_run ───────────────────────


def test_task_shape_maps_limits():
    task = _compare_task(
        project_id="p1", max_rows=7_000_000, result_format="parquet", stream_compare=True,
    )
    shape = task_shape(task)
    assert shape.task_id == "t" and shape.project_id == "p1"
    assert shape.max_rows == 7_000_000 and shape.result_format == "parquet"
    assert shape.stream_compare is True


def test_decision_detail_mentions_reason_and_suggestion():
    decision = _eval(_shape(result_format="json", max_rows=400_000))
    text = decision_detail(decision)
    assert "已拒绝" in text and "建议" in text


def test_decision_detail_queue_prefix():
    decision = _eval(queue=_queue(compare_running=2))
    assert decision_detail(decision).startswith("已排队")


def test_guard_compare_run_huge_task_denied():
    # 静态规则命中 → 与主机/队列状态无关，结果稳定可断言
    task = _compare_task(max_rows=8_000_000, result_format="json")
    decision = guard_compare_run(task)
    assert isinstance(decision, GuardDecision)
    assert decision.decision == "deny"


def test_guard_compare_run_respects_enforce_env(monkeypatch):
    task = _compare_task(max_rows=1000)
    monkeypatch.setenv("DATAOPS_GUARD_ENFORCE", "false")
    assert guard_compare_run(task).enforced is False
    monkeypatch.setenv("DATAOPS_GUARD_ENFORCE", "true")
    assert guard_compare_run(task).enforced is True


# ─── 端点接入（run / run-async）──────────────────────────────────────────────


def _seed_huge_json_task():
    """直接落一个高危任务（json + 8M 行）到 task_store，绕过 API datasource 校验。"""
    from app.models import CompareTaskCreate, RunLimits
    from app.services.repositories import task_store

    return task_store.create(CompareTaskCreate(
        name="huge-json", source_id="ds", target_id="ds", source_sql="select 1",
        key_columns=["id"],
        limits=RunLimits(max_rows=8_000_000, result_format="json"),
    ))


def test_run_endpoint_denies_huge_task_when_enforced(client, monkeypatch):
    monkeypatch.setenv("DATAOPS_GUARD_ENFORCE", "true")
    task = _seed_huge_json_task()
    resp = client.post(f"/api/tasks/{task.id}/run")
    assert resp.status_code == 429


def test_run_async_endpoint_denies_huge_task_when_enforced(client, monkeypatch):
    monkeypatch.setenv("DATAOPS_GUARD_ENFORCE", "true")
    task = _seed_huge_json_task()
    resp = client.post(f"/api/tasks/{task.id}/run-async")
    assert resp.status_code == 429


# ─── Phase 13:check_disk_critical mid-run 水位 ──────────────────────────────


def test_check_disk_critical_healthy(monkeypatch):
    """剩余 / 使用率都在阈值内 → critical=False"""
    monkeypatch.setattr(
        "app.services.resource_guard._disk_stats",
        lambda: (200.0, 30.0),  # 200GB free, 30% used
    )
    critical, reason = check_disk_critical()
    assert critical is False
    assert reason is None


def test_check_disk_critical_free_below_threshold(monkeypatch):
    monkeypatch.setenv("DATAOPS_RESULTS_MIN_FREE_GB", "10")
    monkeypatch.setattr(
        "app.services.resource_guard._disk_stats",
        lambda: (3.5, 40.0),  # 3.5GB < 10GB threshold
    )
    critical, reason = check_disk_critical()
    assert critical is True
    assert "3.50GB" in reason
    assert "10" in reason


def test_check_disk_critical_usage_above_threshold(monkeypatch):
    monkeypatch.setenv("DATAOPS_RESULTS_MAX_DISK_USAGE_PERCENT", "85")
    monkeypatch.setattr(
        "app.services.resource_guard._disk_stats",
        lambda: (100.0, 92.5),  # 92.5% > 85% threshold
    )
    critical, reason = check_disk_critical()
    assert critical is True
    assert "92.5%" in reason
    assert "85" in reason


def test_check_disk_critical_free_takes_priority(monkeypatch):
    """剩余空间是更紧迫的信号(可能比使用率%先到红线 —— 大盘小数据场景),
    所以优先返"""
    monkeypatch.setenv("DATAOPS_RESULTS_MIN_FREE_GB", "10")
    monkeypatch.setenv("DATAOPS_RESULTS_MAX_DISK_USAGE_PERCENT", "85")
    monkeypatch.setattr(
        "app.services.resource_guard._disk_stats",
        lambda: (5.0, 90.0),  # 两个阈值都过 → free 先报
    )
    critical, reason = check_disk_critical()
    assert critical is True
    assert "剩余" in reason and "5.00GB" in reason


def test_disk_watermark_exceeded_is_runtime_error():
    # caller(runner)用 try/except DiskWatermarkExceeded 包流式循环 ——
    # 必须是 RuntimeError 子类,不能是 BaseException(让 generic except 抓到)
    assert issubclass(DiskWatermarkExceeded, RuntimeError)
    exc = DiskWatermarkExceeded("test message")
    assert str(exc) == "test message"


# ─── per-run 配额 check_run_quota ─────────────────────────────────────────


def test_run_quota_exceeded_is_disk_watermark_subclass():
    """caller 的 try/except DiskWatermarkExceeded 必须能接住 RunQuotaExceeded ——
    走同一条 cleanup 路径"""
    assert issubclass(RunQuotaExceeded, DiskWatermarkExceeded)
    assert issubclass(RunQuotaExceeded, RuntimeError)


def test_check_run_quota_none_means_unlimited(tmp_path):
    """quota=None 永远 OK(老 task / 无显式配额)"""
    (tmp_path / "data.parquet").write_bytes(b"x" * 1024 * 1024)  # 1MB
    over, reason = check_run_quota(tmp_path, None)
    assert over is False
    assert reason is None


def test_check_run_quota_under_quota(tmp_path):
    """落盘 1MB,配额 10MB → 不超"""
    (tmp_path / "data.parquet").write_bytes(b"x" * 1024 * 1024)
    over, reason = check_run_quota(tmp_path, 10)
    assert over is False
    assert reason is None


def test_check_run_quota_over_quota(tmp_path):
    """落盘 3MB,配额 1MB → 超(message 含 MB 数字 + 配额值)"""
    (tmp_path / "data.parquet").write_bytes(b"x" * 3 * 1024 * 1024)
    over, reason = check_run_quota(tmp_path, 1)
    assert over is True
    assert "3.0MB" in reason
    assert "1MB" in reason


def test_check_run_quota_nested_files_summed(tmp_path):
    """run_dir 嵌套子目录的文件也算进总额"""
    (tmp_path / "only_source.parquet").write_bytes(b"x" * 1024 * 1024)
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "diff.parquet").write_bytes(b"y" * 1024 * 1024)
    over, reason = check_run_quota(tmp_path, 1)
    # 1MB + 1MB = 2MB > 1MB 配额
    assert over is True


def test_check_run_quota_missing_dir_is_healthy(tmp_path):
    """run_dir 不存在(write 前 early phase)→ 不超"""
    missing = tmp_path / "missing"
    over, reason = check_run_quota(missing, 10)
    assert over is False


def test_check_run_quota_none_run_dir_is_healthy():
    """run_dir=None(json writer 没 run_dir 属性)→ 不超"""
    over, reason = check_run_quota(None, 10)
    assert over is False
