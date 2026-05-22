"""resource_guard 规则引擎测试 —— 每条 deny/queue/allow 规则正反例 + 优先级 +
dry-run vs enforce。`evaluate()` 是纯函数，host/queue/config 全注入，无需 fixture。
"""
from __future__ import annotations

from app.models import CompareTask, RunLimits
from app.services.resource_guard import (
    GuardConfig,
    GuardDecision,
    HostSnapshot,
    QueueState,
    TaskShape,
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
    config = GuardConfig.from_env()
    assert config.enforce is True
    assert config.max_compare_jobs == 7


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
