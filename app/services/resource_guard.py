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
    # 相对当前任务的 scope：与它同项目 / 同数据源 / 同 owner 的活跃对比作业数。
    per_project_running: int = 0
    per_datasource_running: int = 0
    per_user_running: int = 0  # Phase 14: owner_user_id 维度
    project_disk_used_mb: float = 0.0  # Phase 14: 当前 project 累积已落盘 MB


class TaskShape(BaseModel):
    task_id: str
    project_id: str = ""
    owner_user_id: str = ""  # Phase 14: 给 user_cap 用,scheduler 触发可填 "system"
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
    max_jobs_per_project: int = 2
    max_queries_per_datasource: int = 2
    max_jobs_per_user: int = 1  # Phase 14: per-user 并发上限(1 = 同用户串行)
    project_disk_quota_mb: int = 0  # Phase 14: per-project 累积配额(MB);0=无限
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
            max_jobs_per_project=_env_int("DATAOPS_MAX_JOBS_PER_PROJECT", 2),
            max_queries_per_datasource=_env_int("DATAOPS_MAX_QUERIES_PER_DATASOURCE", 2),
            max_jobs_per_user=_env_int("DATAOPS_MAX_JOBS_PER_USER", 1),
            project_disk_quota_mb=_env_int("DATAOPS_PROJECT_DISK_QUOTA_MB", 0),
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


class DiskWatermarkExceeded(RuntimeError):
    """mid-run 磁盘水位检查触发 —— 把当前 run 的 streaming 写入主动中止。

    `evaluate` 是 admission control(任务进入前看一次磁盘),长 run 跑到一半
    把盘写爆是空档。runner 在 streaming compare 循环每 N 行查一次,达 critical
    raise 这个,caller 捕获后 cleanup 临时目录避免半成品累积。
    """


class RunQuotaExceeded(DiskWatermarkExceeded):
    """单 run 落盘字节超过 task.limits.run_disk_quota_mb 配额。

    继承 DiskWatermarkExceeded —— caller 用 `except DiskWatermarkExceeded` 能
    一并接住两种 mid-run 中止(主机磁盘 + 单 run 配额),走同一条 cleanup 路径。
    上层区分时再 `isinstance` 判断。
    """


def check_disk_critical(
    config: "GuardConfig | None" = None,
) -> tuple[bool, str | None]:
    """mid-run 用:磁盘是否处于 critical(应中止写入)?

    Critical = 剩余 < `results_min_free_gb` 或 使用率 > `results_max_disk_usage_pct`,
    阈值同 admission control(guard.evaluate)。返回 `(critical, reason)`:
    `(True, "磁盘剩余 ... < ...")` 让 caller 抛 `DiskWatermarkExceeded`。

    `config=None` 时每次从 env 读 —— 改 env 后无需重启。
    """
    cfg = config or GuardConfig.from_env()
    free_gb, usage_pct = _disk_stats()
    if free_gb < cfg.results_min_free_gb:
        return True, (
            f"磁盘剩余 {free_gb:.2f}GB 低于阈值 {cfg.results_min_free_gb}GB "
            f"(DATAOPS_RESULTS_MIN_FREE_GB)"
        )
    if usage_pct > cfg.results_max_disk_usage_pct:
        return True, (
            f"磁盘使用率 {usage_pct:.1f}% 高于阈值 {cfg.results_max_disk_usage_pct}% "
            f"(DATAOPS_RESULTS_MAX_DISK_USAGE_PERCENT)"
        )
    return False, None


def check_run_quota(
    run_dir: "Path | None", quota_mb: int | None,
) -> tuple[bool, str | None]:
    """mid-run 用:单 run 落盘字节是否超过 task.limits.run_disk_quota_mb?

    `quota_mb=None` → 无限制,直接返 `(False, None)`(老 task 默认行为)。
    `run_dir=None` 或不存在 → 写入还没落到目录(early phase),返 `(False, None)`。

    把 `run_dir/**` 累计字节折成 MB,> quota_mb 时返 `(True, reason)`。文件
    系统调用失败(权限 / race)吞掉返健康值,绝不因测量失败误拦真查询。
    """
    if quota_mb is None or quota_mb <= 0:
        return False, None
    if run_dir is None:
        return False, None
    try:
        if not run_dir.exists():
            return False, None
        total_bytes = sum(
            f.stat().st_size for f in run_dir.rglob("*") if f.is_file()
        )
    except (OSError, ValueError):
        return False, None
    total_mb = total_bytes / (1024 ** 2)
    if total_mb > quota_mb:
        return True, (
            f"该 run 已落盘 {total_mb:.1f}MB 超出配额 {quota_mb}MB "
            f"(task.limits.run_disk_quota_mb)"
        )
    return False, None


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


def queue_snapshot(
    *,
    project_id: str = "",
    datasource_ids: tuple[str, ...] = (),
    owner_user_id: str = "",
) -> QueueState:
    """采当前队列状态。给了 project_id / datasource_ids / owner_user_id 时,
    额外算「与之同项目 / 同数据源 / 同 owner」的活跃对比作业数 —— 反查每个
    活跃 job 的 task。"""
    from app.services.jobs import (
        active_compare_owner_ids,
        active_compare_task_ids,
        active_job_counts,
    )

    counts = active_job_counts()
    ds_set = {d for d in datasource_ids if d}
    per_project = 0
    per_datasource = 0
    if project_id or ds_set:
        from app.services.repositories import task_store

        for task_id in active_compare_task_ids():
            task = task_store.get(task_id)
            if task is None:
                continue
            if project_id and (task.project_id or "") == project_id:
                per_project += 1
            if ds_set and ({task.source_id, task.target_id} & ds_set):
                per_datasource += 1
    per_user = 0
    if owner_user_id:
        per_user = sum(1 for o in active_compare_owner_ids() if o == owner_user_id)
    project_disk_mb = 0.0
    if project_id:
        project_disk_mb = _project_disk_usage_mb(project_id)
    return QueueState(
        compare_running=counts["compare_running"],
        export_running=counts["export_running"],
        queue_depth=counts["active_total"],
        per_project_running=per_project,
        per_datasource_running=per_datasource,
        per_user_running=per_user,
        project_disk_used_mb=project_disk_mb,
    )


def _project_disk_usage_mb(project_id: str) -> float:
    """扫 results/ 下所有 run(json 单文件 + parquet 目录)对应 task → 反查
    project_id 累积字节折成 MB。Phase 14 per-project 配额用。

    实现成本说明:reservation per file 走 stat() —— results 目录 100 个 run /
    每个 ~50 文件 ≈ 5000 次 stat,在 SSD 上 < 50ms。caller 在 guard.evaluate
    入口调一次,不在 hot loop,可接受。失败吞掉返 0(measurement 失败不该误拦)。
    """
    try:
        from app.services.repositories import task_store

        # 建 task_id → project_id 索引
        task_to_proj: dict[str, str] = {
            t.id: (t.project_id or "") for t in task_store.list()
        }
        total_bytes = 0
        # results/<run_id>.json 形态:run_id 前缀是 task_id;parquet 也用同 prefix
        for entry in RESULTS_DIR.iterdir():
            run_id = entry.name
            # 老 run 用 task_id_<uuid>;Phase 12 起 run_id 用时间戳格式,跟 task
            # 解耦 → 这种 run 无法反查 project,跳过(配额 best-effort 不强求 100%)
            task_id = run_id.split("_")[0] if "_" in run_id else run_id
            if task_to_proj.get(task_id) != project_id:
                continue
            try:
                if entry.is_file():
                    total_bytes += entry.stat().st_size
                elif entry.is_dir():
                    total_bytes += sum(
                        f.stat().st_size for f in entry.rglob("*") if f.is_file()
                    )
            except (OSError, ValueError):
                continue
        return round(total_bytes / (1024 ** 2), 2)
    except Exception:
        return 0.0


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
    if shape.project_id and queue.per_project_running >= config.max_jobs_per_project:
        enqueue.append(GuardReason(
            code="project_cap", severity="medium",
            message=(
                f"本项目已有 {queue.per_project_running} 个对比作业在运行"
                f"（上限 {config.max_jobs_per_project}），任务排队"
            ),
            field="per_project_running", actual=str(queue.per_project_running),
        ))
    if queue.per_datasource_running >= config.max_queries_per_datasource:
        enqueue.append(GuardReason(
            code="datasource_cap", severity="medium",
            message=(
                f"目标数据源已有 {queue.per_datasource_running} 个对比查询在运行"
                f"（上限 {config.max_queries_per_datasource}），任务排队"
            ),
            field="per_datasource_running", actual=str(queue.per_datasource_running),
        ))
    # Phase 14 per-user cap:owner_user_id 显式给了才检查;空 owner 当系统兼容
    # (老 task 默认行为不变),scheduler 触发的 system owner 不参与该约束
    if shape.owner_user_id and shape.owner_user_id != "system" and queue.per_user_running >= config.max_jobs_per_user:
        enqueue.append(GuardReason(
            code="user_cap", severity="medium",
            message=(
                f"当前用户已有 {queue.per_user_running} 个对比作业在运行"
                f"（上限 {config.max_jobs_per_user}),任务排队"
            ),
            field="per_user_running", actual=str(queue.per_user_running),
        ))
    # Phase 14 per-project 累积配额:project_disk_quota_mb=0 视作无限
    if (
        config.project_disk_quota_mb > 0
        and shape.project_id
        and queue.project_disk_used_mb >= config.project_disk_quota_mb
    ):
        deny.append(GuardReason(
            code="project_disk_quota", severity="high",
            message=(
                f"项目 {shape.project_id} 已落盘 {queue.project_disk_used_mb}MB,"
                f"超出配额 {config.project_disk_quota_mb}MB"
                f"(DATAOPS_PROJECT_DISK_QUOTA_MB)"
            ),
            field="project_disk_used_mb",
            actual=f"{queue.project_disk_used_mb}MB",
            expected=f"<={config.project_disk_quota_mb}MB",
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
    "project_cap": "本项目前序对比作业完成后会自动开始",
    "datasource_cap": "该数据源前序查询完成后会自动开始",
    "user_cap": "本用户前序对比作业完成后会自动开始",
    "project_disk_quota": "清理本项目历史结果或调高 DATAOPS_PROJECT_DISK_QUOTA_MB 后重试",
}


def _suggestions(reasons: list[GuardReason]) -> list[str]:
    out: list[str] = []
    for reason in reasons:
        tip = _SUGGESTION_BY_CODE.get(reason.code)
        if tip and tip not in out:
            out.append(tip)
    return out


# ─── 端点入口 ────────────────────────────────────────────────────────────────


def task_shape(
    task: CompareTask,
    *,
    kind: str = "compare",
    owner_user_id: str = "",
) -> TaskShape:
    limits = task.limits
    return TaskShape(
        task_id=task.id,
        project_id=task.project_id or "",
        owner_user_id=owner_user_id,
        kind=kind,
        max_rows=limits.max_rows,
        result_format=limits.result_format,
        stream_compare=limits.stream_compare,
        persist_same_bucket=limits.persist_same_bucket,
        export_max_rows=limits.export_max_rows,
    )


def guard_compare_run(
    task: CompareTask,
    *,
    kind: str = "compare",
    owner_user_id: str = "",
) -> GuardDecision:
    """端点入口：现采主机/队列快照 + 读 env，评估并打指标、记日志。

    返回 `GuardDecision`；调用方据 `.enforced` + `.decision` 决定是否拦。
    Phase 14:caller 可以传 `owner_user_id` 让 per-user cap 生效;不传默认 ""
    跳过该约束(向后兼容)。
    """
    config = GuardConfig.from_env()
    decision = evaluate(
        task_shape(task, kind=kind, owner_user_id=owner_user_id),
        host=host_snapshot(),
        queue=queue_snapshot(
            project_id=task.project_id or "",
            datasource_ids=(task.source_id, task.target_id),
            owner_user_id=owner_user_id,
        ),
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
