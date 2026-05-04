"""Phase 9 Day 5：AI inference 异步化测试。

覆盖：
- enqueue_lineage_ai_inference 立即返回 pending placeholder（不阻塞 caller）
- 后台线程跑完后，_AI_JOBS 里的 job 变 ok 状态，edges/column_hints 进来
- 失败路径：worker 抛异常 → job 变 error，不污染主流程
- ai_async=True 路径下 _attach_ai_inference 的行为：
  result["ai_inferred"] = {"status": "pending", "job_id": ..., "kind": "inference"}
"""
from __future__ import annotations

import time

import pytest

from app.services import lineage_service
from app.services.lineage_ai import LineageAIConfig, get_lineage_ai_job
from app.services.lineage_ai_inference import enqueue_lineage_ai_inference


def _setup_fake_openai(monkeypatch, response_dict):
    """复用 test_lineage_ai_inference 里的 mock 模式。"""
    from app.services import lineage_ai_inference as mod
    captured: dict = {}

    def fake_post_json(url, payload, headers, timeout):  # noqa
        captured["url"] = url
        captured["payload"] = payload
        return response_dict

    monkeypatch.setattr(mod, "_post_json", fake_post_json)
    return captured


def _wait_for_job(job_id: str, *, target_status: str, timeout: float = 3.0) -> dict:
    """轮询 job 直到状态命中 target_status；超时 raise。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = get_lineage_ai_job(job_id)
        if job and job.get("status") == target_status:
            return job
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not reach status={target_status} within {timeout}s; last={get_lineage_ai_job(job_id)}")


def test_enqueue_inference_returns_pending_placeholder_immediately(monkeypatch) -> None:
    """enqueue 返回 pending；不阻塞调用方。"""
    _setup_fake_openai(monkeypatch, {
        "choices": [{"message": {"content": '{"edges": []}'}}]
    })
    config = LineageAIConfig(provider="openai", model="m", api_key="k", timeout_seconds=5)
    pending = enqueue_lineage_ai_inference(
        parse_errors=[{"sql": "INSERT INTO t1 SELECT * FROM t2", "error": "oops"}],
        dynamic_sql_segments=[],
        ambiguous_columns=[],
        procedure_segments=[],
        table_whitelist={"t1", "t2"},
        column_whitelist=set(),
        dialect="mysql",
        config=config,
    )
    assert pending["status"] == "pending"
    assert pending["kind"] == "inference"
    assert "job_id" in pending
    assert pending["edges"] == []  # 占位


def test_async_inference_job_completes_with_edges(monkeypatch) -> None:
    """worker 跑完，job 状态从 pending → running → ok，edges 落进来。"""
    _setup_fake_openai(monkeypatch, {
        "choices": [{"message": {"content": (
            '{"edges": [{"source_table": "t2", "target_table": "t1", '
            '"dml_type": "INSERT", "confidence": "low", '
            '"reason": "test", "evidence": "INSERT..."}]}')}}]
    })
    config = LineageAIConfig(provider="openai", model="m", api_key="k", timeout_seconds=5)
    pending = enqueue_lineage_ai_inference(
        parse_errors=[{"sql": "INSERT INTO t1 SELECT * FROM t2", "error": "oops"}],
        dynamic_sql_segments=[],
        ambiguous_columns=[],
        procedure_segments=[],
        table_whitelist={"t1", "t2"},
        column_whitelist=set(),
        dialect="mysql",
        config=config,
    )
    job = _wait_for_job(pending["job_id"], target_status="ok")
    assert job["kind"] == "inference"
    assert len(job["edges"]) == 1
    assert job["edges"][0]["target_table"] == "t1"
    assert job["edges"][0]["source_table"] == "t2"
    assert job["edges"][0]["confidence"] == "low"
    assert "elapsed_seconds" in job


def test_async_inference_job_handles_provider_exception(monkeypatch) -> None:
    """provider 抛异常时 job 变 error，但不让 caller 拿到异常。"""
    from app.services import lineage_ai_inference as mod

    def fake_post_json(*args, **kwargs):  # noqa
        raise RuntimeError("network down")

    monkeypatch.setattr(mod, "_post_json", fake_post_json)
    config = LineageAIConfig(provider="openai", model="m", api_key="k", timeout_seconds=5)
    pending = enqueue_lineage_ai_inference(
        parse_errors=[{"sql": "INSERT INTO t1 SELECT * FROM t2", "error": "oops"}],
        dynamic_sql_segments=[],
        ambiguous_columns=[],
        procedure_segments=[],
        table_whitelist={"t1", "t2"},
        column_whitelist=set(),
        dialect="mysql",
        config=config,
    )
    # 单个片段失败被 infer_from_parse_errors 内部 catch 成 warning，整体仍然 ok
    job = _wait_for_job(pending["job_id"], target_status="ok")
    assert job["edges"] == []  # 网络挂了 → 没 edge
    assert any(w.get("type") == "ai_inference_error" for w in job["warnings"])


def test_attach_ai_inference_async_returns_placeholder(monkeypatch) -> None:
    """`_attach_ai_inference(... ai_async=True)` 立即返回 pending，不阻塞。"""
    monkeypatch.setattr(
        lineage_service,
        "_extract_ambiguous_columns",
        lambda result: [],
    )
    fake_config = LineageAIConfig(
        provider="openai", model="m", api_key="k", timeout_seconds=5,
        enable_inference=True,
    )
    monkeypatch.setattr(
        "app.services.lineage_ai._config",
        lambda: fake_config,
    )
    _setup_fake_openai(monkeypatch, {
        "choices": [{"message": {"content": '{"edges": []}'}}]
    })

    result: dict = {
        "parse_errors": [{"sql": "INSERT INTO t1 SELECT * FROM t2", "error": "x"}],
        "dynamic_sql_segments": [],
        "tables": [{"table": "t1"}, {"table": "t2"}],
        "graph_edges": [],
        "insert_mappings": [],
        "columns": [],
        "warnings": [],
    }
    lineage_service._attach_ai_inference(
        result, dialect="mysql", enabled=True, ai_async=True,
    )
    inferred = result["ai_inferred"]
    assert inferred["status"] == "pending"
    assert inferred["kind"] == "inference"
    assert "job_id" in inferred
    # 等后台线程跑完
    job = _wait_for_job(inferred["job_id"], target_status="ok")
    assert job["status"] == "ok"
