"""SQL 执行统计 —— 按 sql_hash 聚合 (count / total_ms / max_ms / last_seen) (P2-2)。

**用途**:
- admin 看"top 10 慢 SQL"判断哪些 SQL 模板是优化重点
- 配合 sql_log_sanitizer.sql_fingerprint 不暴露字面值 → 跨用户跨执行聚合
- 通过 `/api/sql-workbench/stats` endpoint 暴露给 admin 视图

**实现**:
- in-memory dict + Lock,**进程级**统计(多 worker / 多容器不汇总,生产可后期接 Redis)
- 不持久化 —— 进程重启清零,反映"近期热点"语义
- LRU cap 防止 hash 字典无限膨胀:超过 _MAX_DISTINCT_HASHES 时清掉最旧的一半

**字段语义**:
- count: 总执行次数(包含成功 + 失败)
- success_count / failed_count: 分别计数
- total_ms / max_ms / min_ms: 时间分布
- last_seen_at / last_seen_user: 最近一次执行的 UTC 时间 + 用户名(用户名做 audit hint,不入安全决策)
- last_sql_preview: sanitize 后的 SQL 预览片段(prod 模式已脱敏)
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field

from app.utils.sql_log_sanitizer import sanitize_sql_for_log, sql_fingerprint


_MAX_DISTINCT_HASHES = 10_000  # cap 防膨胀


@dataclass
class SqlStat:
    sql_hash: str
    count: int = 0
    success_count: int = 0
    failed_count: int = 0
    total_ms: float = 0.0
    max_ms: float = 0.0
    min_ms: float = 0.0
    last_seen_at: float = 0.0      # epoch seconds
    last_seen_user: str = ""
    last_sql_preview: str = ""

    @property
    def avg_ms(self) -> float:
        return self.total_ms / self.count if self.count > 0 else 0.0

    def to_dict(self) -> dict:
        return {
            "sql_hash": self.sql_hash,
            "count": self.count,
            "success_count": self.success_count,
            "failed_count": self.failed_count,
            "total_ms": round(self.total_ms, 2),
            "avg_ms": round(self.avg_ms, 2),
            "max_ms": round(self.max_ms, 2),
            "min_ms": round(self.min_ms, 2),
            "last_seen_at": self.last_seen_at,
            "last_seen_user": self.last_seen_user,
            "last_sql_preview": self.last_sql_preview,
        }


@dataclass
class _Registry:
    # OrderedDict 当 LRU 用:最久没用的最先被清
    stats: "OrderedDict[str, SqlStat]" = field(default_factory=OrderedDict)
    lock: threading.Lock = field(default_factory=threading.Lock)


_registry = _Registry()


def record(
    *,
    sql: str,
    elapsed_ms: float,
    success: bool,
    user: str = "",
) -> str:
    """记录一次 SQL 执行。返回 sql_hash(便于 caller 在日志里也带上)。

    线程安全。性能:O(1) dict 操作 + 短锁。
    """
    h = sql_fingerprint(sql)
    preview = sanitize_sql_for_log(sql, max_chars=200)
    now = time.time()
    with _registry.lock:
        stat = _registry.stats.get(h)
        if stat is None:
            stat = SqlStat(sql_hash=h)
            _registry.stats[h] = stat
            # LRU 清理:超过 cap 时把最旧的一半删掉
            if len(_registry.stats) > _MAX_DISTINCT_HASHES:
                _evict_half()
        # LRU 顺序:每次访问都 move_to_end 表示"刚用过"
        _registry.stats.move_to_end(h)

        stat.count += 1
        if success:
            stat.success_count += 1
        else:
            stat.failed_count += 1
        stat.total_ms += elapsed_ms
        stat.max_ms = max(stat.max_ms, elapsed_ms)
        # min_ms 第一次设为当前值,之后取 min
        stat.min_ms = elapsed_ms if stat.min_ms == 0.0 else min(stat.min_ms, elapsed_ms)
        stat.last_seen_at = now
        stat.last_seen_user = user
        stat.last_sql_preview = preview
    return h


def _evict_half() -> None:
    """在持锁状态下被调。删 OrderedDict 头部一半(最旧的)。"""
    target = len(_registry.stats) // 2
    for _ in range(target):
        _registry.stats.popitem(last=False)


def top_slow(
    *,
    limit: int = 20,
    metric: str = "avg_ms",
    min_count: int = 1,
) -> list[dict]:
    """返回慢 SQL 排行榜。

    metric:
    - "avg_ms": 平均耗时(默认 —— 反映 SQL 模板本身)
    - "max_ms": 最长一次耗时(反映 worst-case)
    - "total_ms": 累计耗时(反映"虽然单次不慢但跑得多")
    - "count": 执行次数(反映"高频 SQL")

    min_count 过滤:只统计执行过 ≥ min_count 次的(避免 1 次偶发抖动占榜)。
    """
    metric_key = metric if metric in {"avg_ms", "max_ms", "total_ms", "count"} else "avg_ms"
    with _registry.lock:
        items = [s for s in _registry.stats.values() if s.count >= min_count]
    items.sort(key=lambda s: getattr(s, metric_key), reverse=True)
    return [s.to_dict() for s in items[:limit]]


def get_summary() -> dict:
    """全局 summary:总 SQL 数 / 模板数 / 总耗时 / 失败率。"""
    with _registry.lock:
        total_runs = sum(s.count for s in _registry.stats.values())
        total_fail = sum(s.failed_count for s in _registry.stats.values())
        total_ms = sum(s.total_ms for s in _registry.stats.values())
        distinct = len(_registry.stats)
    return {
        "distinct_sql_templates": distinct,
        "total_executions": total_runs,
        "total_failures": total_fail,
        "failure_rate": round(total_fail / total_runs, 4) if total_runs else 0.0,
        "total_ms": round(total_ms, 2),
    }


def reset_for_tests() -> None:
    with _registry.lock:
        _registry.stats.clear()
