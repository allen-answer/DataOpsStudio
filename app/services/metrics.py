"""Prometheus 风格 /metrics 后端 —— 不依赖 prometheus_client。

设计目标：单进程内存指标（够 dev / 单机部署用）；生产多 worker 场景需要
shared store（redis / statsd），那时再换 prometheus_client + multiprocess
mode 也不晚。

暴露三类指标：
- Counter：累计计数（http_requests_total / ai_usage_calls_total）
- Histogram：分位数（http_request_duration_seconds 用固定 bucket）
- Gauge：瞬时值（lineage_index_table_count / workflow_runs_active）

输出走 Prometheus text format（v0.0.4），让 Prometheus / Grafana / VictoriaMetrics
都能直接抓。
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Any, Iterable


# 分桶：5ms / 10ms / 25ms / 50ms / 100ms / 250ms / 500ms / 1s / 2.5s / 5s / 10s / +inf
_DEFAULT_BUCKETS = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]


def _format_labels(labels: dict[str, str]) -> str:
    """`{path="/x",status="200"}` —— Prometheus 标签格式。"""
    if not labels:
        return ""
    parts = []
    for k in sorted(labels):
        v = str(labels[k]).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        parts.append(f'{k}="{v}"')
    return "{" + ",".join(parts) + "}"


class Counter:
    def __init__(self, name: str, help_text: str, label_names: list[str]) -> None:
        self.name = name
        self.help_text = help_text
        self.label_names = label_names
        self._values: dict[tuple[str, ...], float] = defaultdict(float)
        self._lock = threading.Lock()

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        key = tuple(labels.get(n, "") for n in self.label_names)
        with self._lock:
            self._values[key] += amount

    def render(self) -> Iterable[str]:
        yield f"# HELP {self.name} {self.help_text}"
        yield f"# TYPE {self.name} counter"
        with self._lock:
            items = list(self._values.items())
        for key, value in items:
            labels = dict(zip(self.label_names, key))
            yield f"{self.name}{_format_labels(labels)} {value}"


class Histogram:
    def __init__(self, name: str, help_text: str, label_names: list[str], buckets: list[float] | None = None) -> None:
        self.name = name
        self.help_text = help_text
        self.label_names = label_names
        self.buckets = sorted(buckets or _DEFAULT_BUCKETS)
        self._counts: dict[tuple[str, ...], list[int]] = defaultdict(lambda: [0] * (len(self.buckets) + 1))
        self._sums: dict[tuple[str, ...], float] = defaultdict(float)
        self._lock = threading.Lock()

    def observe(self, value: float, **labels: str) -> None:
        key = tuple(labels.get(n, "") for n in self.label_names)
        with self._lock:
            counts = self._counts[key]
            for i, bound in enumerate(self.buckets):
                if value <= bound:
                    counts[i] += 1
            counts[-1] += 1  # +Inf bucket
            self._sums[key] += value

    def render(self) -> Iterable[str]:
        yield f"# HELP {self.name} {self.help_text}"
        yield f"# TYPE {self.name} histogram"
        with self._lock:
            items = list(self._counts.items())
            sums = dict(self._sums)
        for key, counts in items:
            labels = dict(zip(self.label_names, key))
            # 注意：counts[i] 已经是累计 —— observe 时每个 value 都给所有
            # 它 ≤ 的 bucket +1，所以这里直接 emit，不再二次累加。
            for i, bound in enumerate(self.buckets):
                bucket_labels = {**labels, "le": format(bound, "g")}
                yield f"{self.name}_bucket{_format_labels(bucket_labels)} {counts[i]}"
            inf_labels = {**labels, "le": "+Inf"}
            yield f"{self.name}_bucket{_format_labels(inf_labels)} {counts[-1]}"
            yield f"{self.name}_count{_format_labels(labels)} {counts[-1]}"
            yield f"{self.name}_sum{_format_labels(labels)} {sums.get(key, 0.0)}"


class Gauge:
    """瞬时值；用 callable 而非 set/inc，每次 render 现取最新值
    （避免维护一个 stale 缓存）。"""

    def __init__(self, name: str, help_text: str, value_fn) -> None:
        self.name = name
        self.help_text = help_text
        self.value_fn = value_fn

    def render(self) -> Iterable[str]:
        try:
            value = float(self.value_fn())
        except Exception:
            return
        yield f"# HELP {self.name} {self.help_text}"
        yield f"# TYPE {self.name} gauge"
        yield f"{self.name} {value}"


# ─── 单例注册表 ──────────────────────────────────────────────────────────────


http_requests_total = Counter(
    "http_requests_total",
    "Total HTTP requests by path / method / status",
    ["path", "method", "status"],
)
http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["path", "method"],
)
ai_usage_calls_total = Counter(
    "ai_usage_calls_total",
    "AI provider call count by kind / provider / status",
    ["kind", "provider", "status"],
)
ai_usage_tokens_total = Counter(
    "ai_usage_tokens_total",
    "AI token consumption by kind / direction (input/output)",
    ["kind", "direction"],
)
guard_decisions_total = Counter(
    "dataops_guard_decisions_total",
    "resource_guard decisions by decision (allow/queue/deny) and primary reason code",
    ["decision", "reason"],
)
auth_rate_limit_hits_total = Counter(
    "auth_rate_limit_hits_total",
    "Auth endpoint rate limit hits by endpoint and key type (ip / user)",
    ["endpoint", "key_type"],
)


def _lineage_table_count() -> int:
    try:
        from app.services.lineage_index import get_lineage_index
        return get_lineage_index().stats().get("table_count", 0)
    except Exception:
        return 0


def _lineage_edge_count() -> int:
    try:
        from app.services.lineage_index import get_lineage_index
        return get_lineage_index().stats().get("edge_count", 0)
    except Exception:
        return 0


def _ai_jobs_inflight() -> int:
    try:
        from app.services.lineage_ai import _AI_JOBS, _AI_JOB_LOCK
        with _AI_JOB_LOCK:
            return sum(1 for v in _AI_JOBS.values() if (v.get("status") or "") in {"pending", "running"})
    except Exception:
        return 0


_GAUGES = [
    Gauge("lineage_index_table_count", "Tables aggregated in global lineage index", _lineage_table_count),
    Gauge("lineage_index_edge_count", "Edges aggregated in global lineage index", _lineage_edge_count),
    Gauge("ai_jobs_inflight", "AI enrichment / inference jobs currently running", _ai_jobs_inflight),
]


_COUNTERS_HISTOGRAMS = [
    http_requests_total,
    http_request_duration_seconds,
    ai_usage_calls_total,
    ai_usage_tokens_total,
    guard_decisions_total,
    auth_rate_limit_hits_total,
]


def render_prometheus() -> str:
    """Prometheus text format v0.0.4 输出。"""
    lines: list[str] = []
    for collector in _COUNTERS_HISTOGRAMS:
        lines.extend(collector.render())
    for gauge in _GAUGES:
        lines.extend(gauge.render())
    return "\n".join(lines) + "\n"


__all__ = [
    "http_requests_total",
    "http_request_duration_seconds",
    "ai_usage_calls_total",
    "ai_usage_tokens_total",
    "guard_decisions_total",
    "auth_rate_limit_hits_total",
    "render_prometheus",
]
