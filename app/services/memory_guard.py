"""Wave 4 #15:cgroup 感知的 run 级内存预算。

deep-research 报告动机:`resource_guard._mem_stats()` 读 `/proc/meminfo`
的宿主机视角,在容器限内存场景不等于「当前容器还能分配多少」。结果 guard
看到宿主机宽裕但 cgroup 限额内被 OOM kill。

设计:
- `effective_limit_bytes = cgroup_limit_bytes or env_limit_bytes or host_available`
- `run_budget = min(35% of effective, env_run_budget)`
- 三模式 `off / observe / enforce`,生产 `enforce`
- 每 run 一个 `MemoryGuard` 实例,在关键路径(reader / writer / index)
  采样 `/proc/self/statm` RSS,达 hard_ratio 抛 `MemoryBudgetExceeded`
- runner 高层捕获 → 清理半成品 + 标 `run_index.aborted_guard` +
  `guard_reason=memory_hard_limit` + 记 `peak_rss_mb`

#15 spec 全部 env:
- `DATAOPS_MEMORY_GUARD_MODE` —— off / observe / enforce(默认 observe;
  生产 prod env 推荐设 enforce)
- `DATAOPS_MEMORY_LIMIT_MB` —— 显式覆盖 cgroup / host 检测(给非 Linux dev 用)
- `DATAOPS_MEMORY_HEADROOM_MB` —— 系统预留;默认 256 或 15% 取大
- `DATAOPS_MEMORY_SOFT_RATIO` —— soft 告警阈值(默认 0.80)
- `DATAOPS_MEMORY_HARD_RATIO` —— hard 中止阈值(默认 0.90)
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


logger = logging.getLogger(__name__)


Mode = Literal["off", "observe", "enforce"]


class MemoryBudgetExceeded(RuntimeError):
    """run 级 RSS 超过 hard_ratio × run_budget → 主动中止 + 清理半成品。

    跟 `DiskWatermarkExceeded` / `RunQuotaExceeded` 同等级别,runner 高层用
    `except RuntimeError` 兜底捕获后调 finalize(status='aborted_guard',
    guard_reason='memory_hard_limit')。
    """


@dataclass
class MemorySnapshot:
    rss_bytes: int = 0
    limit_bytes: int | None = None  # 来源:cgroup / env / host_available
    run_budget_bytes: int = 0
    soft_bytes: int = 0
    hard_bytes: int = 0
    source: str = ""  # cgroup_v2 / cgroup_v1 / env / host


def _read_cgroup_limit_bytes() -> tuple[int | None, str]:
    """cgroup v2 优先,v1 fallback。返 (limit_bytes, source) 或 (None, '')。"""
    v2 = Path("/sys/fs/cgroup/memory.max")
    if v2.exists():
        try:
            raw = v2.read_text().strip()
            if raw and raw != "max":
                return int(raw), "cgroup_v2"
        except (OSError, ValueError):
            pass
    v1 = Path("/sys/fs/cgroup/memory/memory.limit_in_bytes")
    if v1.exists():
        try:
            raw = int(v1.read_text().strip())
            # 某些宿主给 huge 值代表无限制(`1 << 62` 量级),过滤掉
            if 0 < raw < (1 << 60):
                return raw, "cgroup_v1"
        except (OSError, ValueError):
            pass
    return None, ""


def _read_rss_bytes() -> int:
    """读 `/proc/self/statm` RSS 页数 × PAGE_SIZE。非 Linux fallback 0。"""
    try:
        with open("/proc/self/statm", "r", encoding="utf-8") as f:
            pages = int(f.read().split()[1])
        page_size = os.sysconf("SC_PAGE_SIZE")
        return pages * page_size
    except (OSError, FileNotFoundError, ValueError, AttributeError):
        return 0


def _read_host_available_bytes() -> int:
    """fallback:/proc/meminfo::MemAvailable。非 Linux 返 0(此时 limit 走 env)。"""
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    kb = int(line.split()[1])
                    return kb * 1024
    except (OSError, FileNotFoundError, ValueError):
        pass
    return 0


def _env_int_mb(name: str) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return 0
    try:
        return int(raw) * 1024 * 1024
    except ValueError:
        return 0


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def get_mode() -> Mode:
    """读 env mode。生产 env 默认 enforce,其它默认 observe。"""
    raw = os.getenv("DATAOPS_MEMORY_GUARD_MODE", "").strip().lower()
    if raw in ("off", "observe", "enforce"):
        return raw  # type: ignore[return-value]
    if os.getenv("DATAOPS_ENV", "").strip().lower() in {"prod", "production"}:
        return "enforce"
    return "observe"


def compute_run_budget_bytes(*, copies_factor: float = 2.0) -> tuple[int, int | None, str]:
    """根据 cgroup / env / host 算单 run 预算字节。

    `copies_factor` 由 caller(runner)按路径传入,这里不用 —— 后续 Phase 5
    metrics 可以记录 estimated_bytes × copies_factor vs actual peak 调优。

    返回 (run_budget_bytes, effective_limit_bytes, source)。
    effective_limit = None 时表示根本没找到 limit(非 Linux + 无 env),
    caller 自决:observe 模式继续走、enforce 模式可以 raise(此处不强制)。
    """
    # 1. 决定 effective_limit
    env_limit = _env_int_mb("DATAOPS_MEMORY_LIMIT_MB")
    cgroup_limit, cgroup_source = _read_cgroup_limit_bytes()
    host_avail = _read_host_available_bytes()

    if env_limit > 0:
        effective_limit = env_limit
        source = "env"
    elif cgroup_limit is not None:
        effective_limit = cgroup_limit
        source = cgroup_source
    elif host_avail > 0:
        effective_limit = host_avail
        source = "host"
    else:
        return 0, None, "none"

    # 2. 系统预留(256 MiB 与 15% 取大)
    env_headroom = _env_int_mb("DATAOPS_MEMORY_HEADROOM_MB")
    default_headroom = max(256 * 1024 * 1024, int(0.15 * effective_limit))
    headroom = env_headroom if env_headroom > 0 else default_headroom

    app_budget = max(effective_limit - headroom, 0)

    # 3. run 预算 = min(35% effective, 50% app_budget)
    run_budget = min(
        int(0.35 * effective_limit),
        int(0.50 * app_budget),
    )
    return run_budget, effective_limit, source


class MemoryGuard:
    """单 run 持有一个实例,关键路径调 `check()` 采样。

    无副作用 mode='off' / mode='observe':只 log 不抛;'enforce':达 hard
    阈值即 raise MemoryBudgetExceeded。
    """

    def __init__(
        self,
        *,
        run_budget_bytes: int = 0,
        soft_ratio: float | None = None,
        hard_ratio: float | None = None,
        mode: Mode | None = None,
    ) -> None:
        self.mode: Mode = mode or get_mode()
        if run_budget_bytes <= 0:
            budget, _, _ = compute_run_budget_bytes()
            run_budget_bytes = budget
        self.run_budget_bytes = max(run_budget_bytes, 0)
        soft = soft_ratio if soft_ratio is not None else _env_float("DATAOPS_MEMORY_SOFT_RATIO", 0.80)
        hard = hard_ratio if hard_ratio is not None else _env_float("DATAOPS_MEMORY_HARD_RATIO", 0.90)
        self.soft_bytes = int(run_budget_bytes * soft) if run_budget_bytes else 0
        self.hard_bytes = int(run_budget_bytes * hard) if run_budget_bytes else 0
        self.peak_rss_bytes = 0
        self._soft_warned = False

    def snapshot(self) -> MemorySnapshot:
        rss = _read_rss_bytes()
        if rss > self.peak_rss_bytes:
            self.peak_rss_bytes = rss
        return MemorySnapshot(
            rss_bytes=rss,
            run_budget_bytes=self.run_budget_bytes,
            soft_bytes=self.soft_bytes,
            hard_bytes=self.hard_bytes,
        )

    def peak_rss_mb(self) -> float:
        return self.peak_rss_bytes / (1024 * 1024)

    def check(self, *, stage: str = "", rows: int = 0) -> MemorySnapshot:
        """采样 RSS,enforce 模式下达 hard_bytes 即 raise。
        observe 模式只记 log;off 模式 no-op 返当前 snapshot。
        """
        if self.mode == "off" or self.run_budget_bytes == 0:
            return self.snapshot()
        snap = self.snapshot()
        if snap.rss_bytes >= self.hard_bytes and self.hard_bytes > 0:
            msg = (
                f"memory hard limit exceeded at {stage or 'unknown'}: "
                f"rss={snap.rss_bytes // (1024 * 1024)}MiB >= "
                f"hard={self.hard_bytes // (1024 * 1024)}MiB "
                f"(run_budget={self.run_budget_bytes // (1024 * 1024)}MiB, rows={rows})"
            )
            if self.mode == "enforce":
                raise MemoryBudgetExceeded(msg)
            logger.warning("[observe] %s", msg)
        elif snap.rss_bytes >= self.soft_bytes and self.soft_bytes > 0 and not self._soft_warned:
            self._soft_warned = True
            logger.warning(
                "memory soft threshold crossed at %s: rss=%dMiB / soft=%dMiB",
                stage or "unknown",
                snap.rss_bytes // (1024 * 1024),
                self.soft_bytes // (1024 * 1024),
            )
        return snap
