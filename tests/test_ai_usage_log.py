"""Phase 9 Day 4：app.ai.usage_log 单元测试。

记录追加 + 倒序读取 + 损坏行容忍 + 写失败静默降级。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ai import usage_log


@pytest.fixture(autouse=True)
def _isolated_log_path(tmp_path, monkeypatch):
    """每个测试用独立 tmp 文件，避免污染真实 logs/ai_usage.jsonl。"""
    target = tmp_path / "ai_usage.jsonl"
    monkeypatch.setattr(usage_log, "_LOG_PATH", target)
    yield target


def test_log_call_appends_and_read_recent_returns_descending(_isolated_log_path: Path) -> None:
    usage_log.log_call(
        kind="enrichment",
        provider="openai",
        model="gpt-4",
        elapsed_ms=120,
        status="ok",
        input_tokens=100,
        output_tokens=50,
    )
    usage_log.log_call(
        kind="inference",
        provider="anthropic",
        model="claude",
        elapsed_ms=300,
        status="error",
        error="Connection timeout",
    )
    items = usage_log.read_recent(limit=10)
    # 倒序 —— 最新在前
    assert len(items) == 2
    assert items[0]["kind"] == "inference"
    assert items[0]["status"] == "error"
    assert items[0]["error"] == "Connection timeout"
    assert items[1]["kind"] == "enrichment"
    assert items[1]["input_tokens"] == 100
    assert items[1]["output_tokens"] == 50


def test_log_call_truncates_long_error(_isolated_log_path: Path) -> None:
    """error 字段过长时截断到 500 字符，避免日志文件爆炸。"""
    long_err = "x" * 1000
    usage_log.log_call(
        kind="enrichment",
        provider="mock",
        model="m",
        elapsed_ms=1,
        status="error",
        error=long_err,
    )
    items = usage_log.read_recent()
    assert len(items[0]["error"]) == 500


def test_read_recent_skips_corrupted_lines(_isolated_log_path: Path) -> None:
    """日志文件里混入坏行（半截 JSON）也不会拖崩 reader。"""
    _isolated_log_path.write_text(
        json.dumps({"ts": "2026-01-01", "kind": "enrichment", "provider": "x",
                    "model": "y", "elapsed_ms": 1, "status": "ok"}) + "\n"
        + "{ broken half line\n"
        + json.dumps({"ts": "2026-01-02", "kind": "inference", "provider": "x",
                      "model": "y", "elapsed_ms": 1, "status": "ok"}) + "\n",
        encoding="utf-8",
    )
    items = usage_log.read_recent()
    # 坏行被跳过，剩 2 条
    assert len(items) == 2
    assert {it["kind"] for it in items} == {"enrichment", "inference"}


def test_read_recent_returns_empty_when_no_log(_isolated_log_path: Path) -> None:
    """文件不存在 → 空 list（不抛错）。"""
    assert usage_log.read_recent() == []


def test_log_call_extra_fields_passed_through(_isolated_log_path: Path) -> None:
    """extra dict 让 caller 加自定义字段（如 fragment_count）。"""
    usage_log.log_call(
        kind="inference",
        provider="mock",
        model="m",
        elapsed_ms=10,
        status="ok",
        extra={"fragment_count": 3, "scope": "batch"},
    )
    items = usage_log.read_recent()
    assert items[0]["fragment_count"] == 3
    assert items[0]["scope"] == "batch"
