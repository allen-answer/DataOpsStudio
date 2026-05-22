"""resource_guard —— 高消耗对比作业的准入决策器（安全加固方案 P0 #1）。

把「重查询 + 重写盘」变成有硬上限的系统能力：在任务进入 runner 之前，按
静态规则 + 主机健康 + 队列状态合并出 allow / queue / deny 决策，避免 OOM、
磁盘打满、队列雪崩。

设计要点：
- `evaluate()` 是纯函数 —— host / queue / config 全部注入，便于单测每条规则。
- `guard_compare_run()` 是带 IO 的包装：现采 host/queue 快照、读 env、打指标
  和日志，端点调它。
- `DATAOPS_GUARD_ENFORCE` 默认 false（dry-run）：决策照算、照记、照打指标，
  但 `GuardDecision.enforced=False`，端点据此放行。观测几天确认不误伤后再设
  true 强制。回滚只需切回 false。

规则与 env 变量详见 docs/RESOURCE_GUARD.md。
"""
from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from app.models import CompareTask
from app.services.metrics import guard_decisions_total
from app.utils.paths import RESULTS_DIR


logger = logging.getLogger(__name__)

Decision = Literal["allow", "queue", "deny"]
RiskLevel = Literal["low", "medium", "high", "critical"]

_SEVERITY_ORDER: dict[RiskLevel, int] = {"low": 0, "medium": 1, "high": 2, "critical": 3}


# ─── 模型 ────────────────────────────────────────────────────────────────────


class GuardReason(BaseModel):
    code: str
    severity: RiskLevel
    message: str
    field: str | None = None
    actual: str | None = None
    expected: str | None = None


class HostSnapshot(BaseModel):
    disk_free_gb: float
    disk_usage_pct: float
    # /proc/meminfo 拿不到时为 None（非 Linux dev 机）——mem 规则随之跳过。
    mem_available_mb: int | None = None
    mem_available_pct: float | None = None


class QueueState(BaseModel):
    compare_running: int = 0
    export_running: int = 0
    queue_depth: int = 0


class TaskShape(BaseModel):
    task_id: str
    project_id: str = ""
    kind: str = "compare"
    max_rows: int
    result_format: Literal["json", "parquet"] = "json"
    stream_compare: bool = False
    persist_same_bucket: bool = False
    export_max_rows: int | None = None


class GuardDecision(BaseModel):
    decision: Decision
    risk_level: RiskLevel
    reasons: list[GuardReason] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    host: HostSnapshot
    queue: QueueState
    retry_after_seconds: int | None = None
    # enforce 模式下才为 True；dry-run 时 decision 照算但 enforced=False。
    enforced: bool = False

    @property
    def primary_reason(self) -> str:
        return self.reasons[0].code if self.reasons else "ok"


# ─── 配置（每次从 env 读，便于 dry-run 切换与单测 monkeypatch）────────────────


@dataclass(frozen=True)
class GuardConfig:
    enforce: bool = False
    max_compare_jobs: int = 2
    max_export_jobs: int = 1
    guard_queue_max: int = 50
    results_min_free_gb: float = 5.0
    results_max_disk_usage_pct: float = 85.0
    json_max_rows: int = 300_000
    huge_rows: int = 1_000_000
    stream_required_rows: int = 5_000_000
    mem_min_available_pct: float = 15.0

    @classmethod
    def from_env(cls) -> "GuardConfig":
        return cls(
            enforce=_env_bool("DATAOPS_GUARD_ENFORCE", False),
            max_compare_jobs=_env_int("DATAOPS_MAX_COMPARE_JOBS", 2),
            max_export_jobs=_env_int("DATAOPS_MAX_EXPORT_JOBS", 1),
            guard_queue_max=_env_int("DATAOPS_GUARD_QUEUE_MAX", 50),
            results_min_free_gb=_env_float("DATAOPS_RESULTS_MIN_FREE_GB", 5.0),
            results_max_disk_usage_pct=_env_float("DATAOPS_RESULTS_MAX_DISK_USAGE_PERCENT", 85.0),
            json_max_rows=_env_int("DATAOPS_GUARD_JSON_MAX_ROWS", 300_000),
            huge_rows=_env_int("DATAOPS_GUARD_HUGE_ROWS", 1_000_000),
            stream_required_rows=_env_int("DATAOPS_GUARD_STREAM_ROWS", 5_000_000),
            mem_min_available_pct=_env_float("DATAOPS_GUARD_MEM_MIN_PERCENT", 15.0),
        )


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


# ─── 主机 / 队列快照 ─────────────────────────────────────────────────────────


def host_snapshot() -> HostSnapshot:
    disk_free_gb, disk_usage_pct = _disk_stats()
    mem_mb, mem_pct = _mem_stats()
    return HostSnapshot(
        disk_free_gb=disk_free_gb,
        disk_usage_pct=disk_usage_pct,
        mem_available_mb=mem_mb,
        mem_available_pct=mem_pct,
    )


def _disk_stats() -> tuple[float, float]:
    """results 盘剩余 GB + 使用率%。测不出来时报健康值，绝不因测量失败误拦。"""
    for path in (RESULTS_DIR, RESULTS_DIR.parent, Path.cwd()):
        try:
            usage = shutil.disk_usage(path)
        except (OSError, ValueError):
            continue
        free_gb = usage.free / (1024 ** 3)
        used_pct = (usage.used / usage.total * 100.0) if usage.total else 0.0
        return (round(free_gb, 3), round(used_pct, 2))
    return (1_000_000.0, 0.0)


def _mem_stats() -> tuple[int | None, float | None]:
    """走 /proc/meminfo（Linux/Docker）。非 Linux dev 机返回 (None, None)。"""
    try:
        text = Path("/proc/meminfo").read_text(encoding="utf-8")
    except (OSError, ValueError):
        return (None, None)
    info: dict[str, int] = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        parts = rest.strip().split()
        if parts and parts[0].isdigit():
            info[key.strip()] = int(parts[0])  # kB
    total = info.get("MemTotal")
    avail = info.get("MemAvailable")
    if not total or avail is None:
        return (None, None)
    return (avail // 1024, round(avail / total * 100.0, 2))


def queue_snapshot() -> QueueState:
    from app.services.jobs import active_job_counts

    counts = active_job_counts()
    return QueueState(
        compare_running=counts["compare_running"],
        export_running=counts["export_running"],
        queue_depth=counts["active_total"],
    )


# ─── 决策（纯函数）──────────────────────────────────────────────────────────


def evaluate(
    shape: TaskShape,
    *,
    host: HostSnapshot,
    queue: QueueState,
    config: GuardConfig,
) -> GuardDecision:
    """合并静态规则 + 主机健康 + 队列上限，产出 allow / queue / deny。

    纯函数：host / queue / config 全部注入。`decision` 按「先硬拒绝、再排队」
    取：有 deny 类原因 → deny；否则有 queue 类原因 → queue；都没有 → allow。
    """
    deny: list[GuardReason] = []
    enqueue: list[GuardReason] = []

    # ── 静态规则（只看 TaskShape，无外部状态）──
    if shape.result_format == "json" and shape.max_rows > config.json_max_rows:
        deny.append(GuardReason(
            code="json_large_result", severity="high",
            message=f"result_format=json 且 max_rows={shape.max_rows} 超过 json 上限 {config.json_max_rows}",
            field="max_rows", actual=str(shape.max_rows),
            expected=f"max_rows<= {config.json_max_rows} 或改用 parquet",
        ))
    if shape.max_rows > config.huge_rows and shape.result_format != "parquet":
        deny.append(GuardReason(
            code="huge_requires_parquet", severity="high",
            message=f"max_rows={shape.max_rows} 超过 {config.huge_rows}，必须用 result_format=parquet",
            field="result_format", actual=shape.result_format, expected="parquet",
        ))
    if shape.max_rows > config.stream_required_rows and not shape.stream_compare:
        deny.append(GuardReason(
            code="huge_requires_stream", severity="critical",
            message=f"max_rows={shape.max_rows} 超过 {config.stream_required_rows}，必须开启 stream_compare",
            field="stream_compare", actual="false", expected="true",
        ))
    if shape.persist_same_bucket and shape.max_rows > config.huge_rows:
        enqueue.append(GuardReason(
            code="same_persist_large", severity="medium",
            message=f"persist_same_bucket=true 且 max_rows={shape.max_rows} 超过 {config.huge_rows}，落盘量大，排队执行",
            field="persist_same_bucket", actual="true",
        ))

    # ── 主机健康 ──
    if host.disk_free_gb < config.results_min_free_gb or host.disk_usage_pct > config.results_max_disk_usage_pct:
        deny.append(GuardReason(
            code="disk_low_watermark", severity="critical",
            message=(
                f"结果盘剩余 {host.disk_free_gb}GB / 使用率 {host.disk_usage_pct}%，"
                f"低于安全阈值（剩余>={config.results_min_free_gb}GB 且 使用率<={config.results_max_disk_usage_pct}%）"
            ),
            field="disk", actual=f"free={host.disk_free_gb}GB,used={host.disk_usage_pct}%",
        ))
    if host.mem_available_pct is not None and host.mem_available_pct < config.mem_min_available_pct:
        deny.append(GuardReason(
            code="mem_pressure", severity="high",
            message=f"可用内存 {host.mem_available_pct}% 低于阈值 {config.mem_min_available_pct}%",
            field="memory", actual=f"{host.mem_available_pct}%",
        ))

    # ── 队列上限 ──
    if queue.queue_depth >= config.guard_queue_max:
        deny.append(GuardReason(
            code="queue_full", severity="high",
            message=f"作业队列深度 {queue.queue_depth} 已达上限 {config.guard_queue_max}",
            field="queue_depth", actual=str(queue.queue_depth),
        ))
    if queue.compare_running >= config.max_compare_jobs:
        enqueue.append(GuardReason(
            code="compare_cap", severity="medium",
            message=f"已有 {queue.compare_running} 个对比作业在运行（上限 {config.max_compare_jobs}），任务排队",
            field="compare_running", actual=str(queue.compare_running),
        ))
    if queue.export_running >= config.max_export_jobs:
        enqueue.append(GuardReason(
            code="export_cap", severity="low",
            message=f"已有 {queue.export_running} 个导出作业在运行（上限 {config.max_export_jobs}），任务排队",
            field="export_running", actual=str(queue.export_running),
        ))

    if deny:
        decision: Decision = "deny"
    elif enqueue:
        decision = "queue"
    else:
        decision = "allow"

    reasons = deny + enqueue
    risk_level: RiskLevel = "low"
    for reason in reasons:
        if _SEVERITY_ORDER[reason.severity] > _SEVERITY_ORDER[risk_level]:
            risk_level = reason.severity

    return GuardDecision(
        decision=decision,
        risk_level=risk_level,
        reasons=reasons,
        suggestions=_suggestions(reasons),
        host=host,
        queue=queue,
        retry_after_seconds=30 if decision == "queue" else None,
        enforced=config.enforce,
    )


_SUGGESTION_BY_CODE: dict[str, str] = {
    "json_large_result": "把 result_format 改为 parquet，或调小 max_rows",
    "huge_requires_parquet": "把 result_format 改为 parquet",
    "huge_requires_stream": "开启 stream_compare（要求两端 SQL 已按主键排序）",
    "same_persist_large": "关闭 persist_same_bucket，或在低峰期重试",
    "disk_low_watermark": "清理历史结果或扩容结果盘后重试",
    "mem_pressure": "等待内存压力缓解后重试，或拆小任务",
    "queue_full": "等待队列消化后重试",
    "compare_cap": "前序对比作业完成后会自动开始",
    "export_cap": "前序导出作业完成后会自动开始",
}


def _suggestions(reasons: list[GuardReason]) -> list[str]:
    out: list[str] = []
    for reason in reasons:
        tip = _SUGGESTION_BY_CODE.get(reason.code)
        if tip and tip not in out:
            out.append(tip)
    return out


# ─── 端点入口 ────────────────────────────────────────────────────────────────


def task_shape(task: CompareTask, *, kind: str = "compare") -> TaskShape:
    limits = task.limits
    return TaskShape(
        task_id=task.id,
        project_id=task.project_id or "",
        kind=kind,
        max_rows=limits.max_rows,
        result_format=limits.result_format,
        stream_compare=limits.stream_compare,
        persist_same_bucket=limits.persist_same_bucket,
        export_max_rows=limits.export_max_rows,
    )


def guard_compare_run(task: CompareTask, *, kind: str = "compare") -> GuardDecision:
    """端点入口：现采主机/队列快照 + 读 env，评估并打指标、记日志。

    返回 `GuardDecision`；调用方据 `.enforced` + `.decision` 决定是否拦。
    """
    config = GuardConfig.from_env()
    decision = evaluate(
        task_shape(task, kind=kind),
        host=host_snapshot(),
        queue=queue_snapshot(),
        config=config,
    )
    guard_decisions_total.inc(decision=decision.decision, reason=decision.primary_reason)
    if decision.decision == "allow":
        logger.debug("resource_guard allow task=%s enforced=%s", task.id, decision.enforced)
    else:
        logger.warning(
            "resource_guard %s task=%s enforced=%s risk=%s reasons=%s",
            decision.decision, task.id, decision.enforced, decision.risk_level,
            ",".join(r.code for r in decision.reasons),
        )
    return decision


def decision_detail(decision: GuardDecision) -> str:
    """把 GuardDecision 拼成给用户看的中文拒绝/排队说明（HTTP 429 detail 用）。"""
    head = "已排队" if decision.decision == "queue" else "已拒绝"
    parts = [r.message for r in decision.reasons]
    body = "；".join(parts) if parts else "资源准入未通过"
    if decision.suggestions:
        body += "。建议：" + "；".join(decision.suggestions)
    return f"{head}：{body}"
