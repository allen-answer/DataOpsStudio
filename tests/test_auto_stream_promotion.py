"""Wave 4 #17:大任务自动 promote 到 stream_compare + parquet。"""
from __future__ import annotations

import pytest

from app.models import CompareTask, RunLimits, CompareRules, SqlMode


def _task(*, max_rows: int, stream_compare: bool = False, result_format: str = "json") -> CompareTask:
    return CompareTask(
        id="t1",
        name="test",
        source_id="ds1",
        target_id="ds1",
        source_sql="select 1",
        sql_mode=SqlMode.SINGLE,
        key_columns=["id"],
        rules=CompareRules(),
        limits=RunLimits(
            max_rows=max_rows,
            stream_compare=stream_compare,
            result_format=result_format,
        ),
    )


def test_small_task_not_promoted():
    from app.services.runner import _maybe_promote_large_task
    task = _task(max_rows=1000, stream_compare=False, result_format="json")
    out, reason = _maybe_promote_large_task(task)
    assert reason == ""
    assert out.limits.stream_compare is False
    assert out.limits.result_format == "json"


def test_large_task_auto_promoted(monkeypatch):
    """超过 auto_stream_bytes 阈值(默认 1 GiB)且非 stream+parquet → promote。"""
    # 1 GiB / 256 bytes per row ≈ 4.2M rows;用低阈值测试更快
    monkeypatch.setenv("DATAOPS_COMPARE_AUTO_STREAM_BYTES", str(1024 * 1024))  # 1 MB
    from app.services.runner import _maybe_promote_large_task
    task = _task(max_rows=10_000, stream_compare=False, result_format="json")  # ~2.5MB
    out, reason = _maybe_promote_large_task(task)
    assert "auto_streaming_promoted" in reason
    assert out.limits.stream_compare is True
    assert out.limits.result_format == "parquet"


def test_already_stream_parquet_no_promote(monkeypatch):
    """已经是 stream+parquet 即便估算大,也无需再 promote。"""
    monkeypatch.setenv("DATAOPS_COMPARE_AUTO_STREAM_BYTES", str(1024))
    from app.services.runner import _maybe_promote_large_task
    task = _task(max_rows=10_000, stream_compare=True, result_format="parquet")
    out, reason = _maybe_promote_large_task(task)
    assert reason == ""
    assert out.limits.stream_compare is True
    assert out.limits.result_format == "parquet"


def test_huge_task_denied(monkeypatch):
    """超过 deny_bytes(默认 5 GiB)→ raise ValueError。"""
    monkeypatch.setenv("DATAOPS_COMPARE_DENY_BYTES", str(1024 * 1024))  # 1 MB
    monkeypatch.setenv("DATAOPS_COMPARE_AUTO_STREAM_BYTES", str(512 * 1024))
    from app.services.runner import _maybe_promote_large_task
    task = _task(max_rows=10_000)  # ~2.5MB
    with pytest.raises(ValueError, match="exceeds run budget"):
        _maybe_promote_large_task(task)


def test_promote_only_changes_limits():
    """promote 不改 task 其它字段(source_id / rules 等)。"""
    import os
    os.environ["DATAOPS_COMPARE_AUTO_STREAM_BYTES"] = str(1024)
    try:
        from app.services.runner import _maybe_promote_large_task
        task = _task(max_rows=100_000, stream_compare=False, result_format="json")
        out, _ = _maybe_promote_large_task(task)
        assert out.id == task.id
        assert out.source_id == task.source_id
        assert out.target_id == task.target_id
        assert out.key_columns == task.key_columns
    finally:
        os.environ.pop("DATAOPS_COMPARE_AUTO_STREAM_BYTES", None)
