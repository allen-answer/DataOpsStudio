"""/metrics + structured logging 测试。"""
from __future__ import annotations

import json
import logging
import os

import pytest
from fastapi.testclient import TestClient

from main import app
from app.services.metrics import (
    Counter,
    Gauge,
    Histogram,
    ai_usage_calls_total,
    http_request_duration_seconds,
    http_requests_total,
    render_prometheus,
)
from app.api._metrics_middleware import _normalize_path
from app.utils.logging_config import JsonLogFormatter, RequestIdInjectFilter


# ─── Counter / Histogram / Gauge 单元 ────────────────────────────────────────


def test_counter_inc_and_render():
    c = Counter("test_counter_total", "test help", ["a", "b"])
    c.inc(a="x", b="y")
    c.inc(a="x", b="y")
    c.inc(a="z", b="y")
    out = "\n".join(c.render())
    assert "# HELP test_counter_total test help" in out
    assert "# TYPE test_counter_total counter" in out
    assert 'test_counter_total{a="x",b="y"} 2' in out
    assert 'test_counter_total{a="z",b="y"} 1' in out


def test_histogram_buckets_cumulative():
    h = Histogram("test_hist_seconds", "h", ["op"], buckets=[0.1, 1.0])
    h.observe(0.05, op="x")  # falls in 0.1 bucket
    h.observe(0.5, op="x")   # falls in 1.0 bucket but not 0.1
    h.observe(2.0, op="x")   # +Inf only
    out = "\n".join(h.render())
    # bucket le=0.1 累计 1（只有 0.05）
    assert 'test_hist_seconds_bucket{le="0.1",op="x"} 1' in out
    # bucket le=1 累计 2（0.05 + 0.5）
    assert 'test_hist_seconds_bucket{le="1",op="x"} 2' in out
    # +Inf 累计 3
    assert 'test_hist_seconds_bucket{le="+Inf",op="x"} 3' in out
    assert 'test_hist_seconds_count{op="x"} 3' in out
    assert 'test_hist_seconds_sum{op="x"} 2.55' in out


def test_gauge_pulls_value_lazily():
    state = [0]
    g = Gauge("test_gauge", "g", lambda: state[0])
    state[0] = 42
    out = "\n".join(g.render())
    assert "test_gauge 42" in out


def test_gauge_value_fn_exception_renders_nothing():
    g = Gauge("crash", "x", lambda: 1 / 0)
    assert "\n".join(g.render()) == ""


# ─── label 转义 ──────────────────────────────────────────────────────────────


def test_counter_escapes_quotes_and_newlines():
    c = Counter("safe_total", "h", ["msg"])
    c.inc(msg='has "quote"\nand newline')
    out = "\n".join(c.render())
    # 双引号转义 + 换行转 \n
    assert r'msg="has \"quote\"\nand newline"' in out


# ─── path 归一化（防 label 基数爆炸） ────────────────────────────────────────


def test_normalize_path_collapses_id_segments():
    assert _normalize_path("/api/tasks/abc123") == "/api/tasks/*"
    assert _normalize_path("/api/workflows/xyz/run") == "/api/workflows/*/run"
    assert _normalize_path("/api/lineage/ai/jobs/abcdef") == "/api/lineage/ai/jobs/*"
    assert _normalize_path("/api/assets/table/ods.t_users") == "/api/assets/table/*"


def test_normalize_path_keeps_unknown_paths():
    assert _normalize_path("/api/search") == "/api/search"
    assert _normalize_path("/static/spa/index.html") == "/static/spa/index.html"


# ─── render_prometheus 集成 ──────────────────────────────────────────────────


def test_render_prometheus_includes_all_known_metrics():
    http_requests_total.inc(path="/x", method="GET", status="200")
    ai_usage_calls_total.inc(kind="enrichment", provider="mock", status="ok")
    out = render_prometheus()
    assert "http_requests_total" in out
    assert "http_request_duration_seconds" in out
    assert "ai_usage_calls_total" in out
    assert "lineage_index_table_count" in out
    assert "ai_jobs_inflight" in out


# ─── /metrics endpoint ──────────────────────────────────────────────────────


@pytest.fixture
def client(isolated_storage):
    return TestClient(app)


def test_metrics_endpoint_text_format(client):
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    assert "# HELP http_requests_total" in body
    assert "# TYPE http_requests_total counter" in body


def test_metrics_endpoint_records_real_traffic(client):
    """打几个请求，看 /metrics 里的 counter 有没有跟着涨。"""
    base = client.get("/metrics").text
    base_count = base.count("http_requests_total{")
    # 打几个不同的请求
    client.get("/api/lineage/graph/stats")
    client.get("/api/lineage/graph/stats")
    after = client.get("/metrics").text
    after_count = after.count("http_requests_total{")
    assert after_count >= base_count  # 至少不减少
    assert "/api/lineage/graph/stats" in after


def test_metrics_endpoint_self_excluded(client):
    """连续打 /metrics 自己，counter 里不应该有 /metrics 行（避免 scrape 算自己）。"""
    for _ in range(3):
        client.get("/metrics")
    body = client.get("/metrics").text
    assert 'http_requests_total{method="GET",path="/metrics"' not in body


# ─── Structured logging ─────────────────────────────────────────────────────


def test_json_formatter_basic():
    formatter = JsonLogFormatter()
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="x", lineno=1,
        msg="hello %s", args=("world",), exc_info=None,
    )
    record.request_id = "req-abc"
    out = formatter.format(record)
    payload = json.loads(out)
    assert payload["level"] == "INFO"
    assert payload["logger"] == "test"
    assert payload["msg"] == "hello world"
    assert payload["request_id"] == "req-abc"


def test_json_formatter_includes_extra():
    formatter = JsonLogFormatter()
    record = logging.LogRecord(
        name="t", level=logging.INFO, pathname="x", lineno=1,
        msg="x", args=(), exc_info=None,
    )
    # extra={"task_id": ..., "user_id": ...} 模拟 logger.info(..., extra={...})
    record.task_id = "abc"
    record.user_id = 42
    record.request_id = ""
    out = json.loads(formatter.format(record))
    assert out["task_id"] == "abc"
    assert out["user_id"] == 42
    # 空 request_id 不输出
    assert "request_id" not in out


def test_json_formatter_handles_non_json_extra():
    """extra 里塞了不可 JSON 序列化的对象（如 set / class instance），不抛错。"""
    formatter = JsonLogFormatter()
    record = logging.LogRecord(
        name="t", level=logging.INFO, pathname="x", lineno=1,
        msg="x", args=(), exc_info=None,
    )
    record.weird = {1, 2, 3}  # set 不可 JSON
    record.request_id = ""
    out = json.loads(formatter.format(record))
    # set 被 stringify
    assert isinstance(out["weird"], str)
    assert "1" in out["weird"]


def test_request_id_inject_filter_with_no_context():
    """ContextVar 未设置时 → request_id 字段为空字符串（不抛错）。"""
    flt = RequestIdInjectFilter()
    record = logging.LogRecord(
        name="t", level=logging.INFO, pathname="x", lineno=1,
        msg="x", args=(), exc_info=None,
    )
    flt.filter(record)
    assert getattr(record, "request_id") == ""


def test_request_id_inject_filter_pulls_from_context():
    """请求路径里 ContextVar 已经设置 → filter 把它注入 record。"""
    from app.api._error_handler import request_id_ctx
    flt = RequestIdInjectFilter()
    request_id_ctx.set("rid-from-test")
    try:
        record = logging.LogRecord(
            name="t", level=logging.INFO, pathname="x", lineno=1,
            msg="x", args=(), exc_info=None,
        )
        flt.filter(record)
        assert record.request_id == "rid-from-test"
    finally:
        # 还原（避免污染其它测试）
        request_id_ctx.set("")


# ─── DATAOPS_LOG_FORMAT env 切换 ─────────────────────────────────────────────


def test_setup_logging_uses_json_when_env_set(monkeypatch, tmp_path):
    monkeypatch.setenv("DATAOPS_LOG_FORMAT", "json")
    from app.utils import logging_config, paths as paths_module
    monkeypatch.setattr(paths_module, "LOGS_DIR", tmp_path)
    monkeypatch.setattr(logging_config, "LOGS_DIR", tmp_path)
    logging_config.setup_logging()
    root = logging.getLogger()
    formatters = [h.formatter for h in root.handlers if h.formatter]
    assert any(isinstance(f, JsonLogFormatter) for f in formatters)
