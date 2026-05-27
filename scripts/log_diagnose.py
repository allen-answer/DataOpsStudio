#!/usr/bin/env python3
"""DataOpsStudio 日志诊断脚本 — 扫 app.log + audit.jsonl 生成报告。

用法:
    python scripts/log_diagnose.py logs/app.log
    python scripts/log_diagnose.py logs/app.log --audit logs/audit.jsonl
    python scripts/log_diagnose.py logs/app.log --top 30
    python scripts/log_diagnose.py logs/app.log --format markdown > report.md

输出包括:
- 时间范围 + 总日志条数
- ERROR / WARNING / Traceback 统计
- 慢查询排行(elapsed > 阈值)
- 各 endpoint 调用次数 + 错误率(从 audit.jsonl 统计)
- 各 datasource 查询次数 / 失败率
- 最常见的错误类型 top N
- 已知 bug 模式匹配(如 PATH 溢出 / unknown scope / OOM 等)

设计原则:
- 纯 Python 标准库,不依赖第三方包(scripts/ 都是这风格)
- 单文件,扔到 portable 包里也能跑
- 支持 plain text / markdown 两种输出
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path


# ─── log line parser ────────────────────────────────────────────────────────

# 2026-05-27 19:32:04 ERROR [app.api._error_handler] msg... rid=xxx
LOG_LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2})\s+"
    r"(?P<level>DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+"
    r"\[(?P<logger>[^\]]+)\]\s+"
    r"(?P<msg>.*?)(?:\s+rid=(?P<rid>[a-f0-9]*))?$"
)

# elapsed=0.034s / elapsed=3.080s
ELAPSED_RE = re.compile(r"elapsed=([\d.]+)s")
# datasource=X db_type=Y
DS_RE = re.compile(r"datasource=(\S+)\s+db_type=(\S+)")
# query failed datasource=... 子串
QUERY_FAIL_RE = re.compile(r"query failed.*?datasource=(\S+)")
# scope=foo key=bar(metadata 路径)
META_FAIL_RE = re.compile(r"metadata live fetch.*?scope=(\S+)\s+key=(\S+).*?failed:\s*(.*?)\s*rid=")


@dataclass
class LogStats:
    total_lines: int = 0
    by_level: collections.Counter = field(default_factory=collections.Counter)
    by_logger: collections.Counter = field(default_factory=collections.Counter)
    first_ts: str = ""
    last_ts: str = ""
    tracebacks: int = 0
    # query 维度
    queries_started: int = 0
    queries_succeeded: int = 0
    queries_failed: int = 0
    slow_queries: list[tuple[str, float, str]] = field(default_factory=list)  # (ts, elapsed, ds_msg)
    by_datasource: collections.Counter = field(default_factory=collections.Counter)
    failed_by_datasource: collections.Counter = field(default_factory=collections.Counter)
    # 错误信息
    error_messages: collections.Counter = field(default_factory=collections.Counter)
    # 已知 bug 模式匹配
    bug_signatures: collections.Counter = field(default_factory=collections.Counter)
    # metadata 失败维度
    metadata_failures: collections.Counter = field(default_factory=collections.Counter)


# 已知 bug 模式 — 命中即给出修复建议
BUG_PATTERNS = {
    "path_overflow": (
        re.compile(r"environment variable is longer than 32767"),
        "DB2 PATH 累积溢出 — 已在 commit 19ee9dc 修(drivers.add_db2_dll_directories 加幂等)",
    ),
    "scope_unknown": (
        re.compile(r"unknown scope: (\S+)"),
        "metadata_cache.SCOPES 闭集不全 — 已在 commit 19ee9dc 修(加 columns-bulk)",
    ),
    "more_than_max_rows": (
        re.compile(r"returned more than max_rows=(\d+)"),
        "metadata 查询行数超阈值 — 已在 commit 19ee9dc 修(list_tables 改 raise_on_overflow=False)",
    ),
    "preview_oom": (
        re.compile(r"memory cap reached: result truncated"),
        "Preview cell 内存截断(包 A 防御机制工作中)",
    ),
    "concurrency_429": (
        re.compile(r"query concurrency limit reached"),
        "并发限制拒新查询 — 调 DATAOPS_QUERY_CONCURRENCY_PER_USER_DS 提高上限",
    ),
    "rate_limit_429": (
        re.compile(r"rate limit"),
        "API 限流拒请求 — 调 DATAOPS_RATELIMIT_* 系列 env",
    ),
    "lob_oom": (
        re.compile(r"CELL_TRUNCATED"),
        "大 LOB 单元格截断(executor.py 64KB 上限保护)",
    ),
}


def parse_log(path: Path, slow_threshold: float = 1.0) -> LogStats:
    stats = LogStats()
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            stats.total_lines += 1
            # Traceback 行单独计数
            if line.startswith("Traceback"):
                stats.tracebacks += 1
                continue
            m = LOG_LINE_RE.match(line.rstrip())
            if not m:
                continue
            ts = m.group("ts")
            if not stats.first_ts:
                stats.first_ts = ts
            stats.last_ts = ts

            level = m.group("level")
            logger_name = m.group("logger")
            msg = m.group("msg") or ""
            stats.by_level[level] += 1
            stats.by_logger[logger_name] += 1

            # Query 统计
            if "query start" in msg:
                stats.queries_started += 1
                ds_m = DS_RE.search(msg)
                if ds_m:
                    stats.by_datasource[(ds_m.group(1), ds_m.group(2))] += 1
            elif "query success" in msg:
                stats.queries_succeeded += 1
                # 慢查询
                e = ELAPSED_RE.search(msg)
                if e and float(e.group(1)) >= slow_threshold:
                    stats.slow_queries.append((ts, float(e.group(1)), msg[:120]))
            elif "query failed" in msg:
                stats.queries_failed += 1
                fm = QUERY_FAIL_RE.search(msg)
                if fm:
                    stats.failed_by_datasource[fm.group(1)] += 1

            # ERROR / WARNING 消息聚合
            if level in ("ERROR", "WARNING"):
                # 摘短一点用于聚合(去 rid / 时间戳 / 数字)
                short = re.sub(r"\b[0-9a-f]{32}\b", "<rid>", msg)
                short = re.sub(r"\b\d+\b", "N", short)[:120]
                stats.error_messages[short] += 1

            # metadata 失败
            mm = META_FAIL_RE.search(msg)
            if mm:
                scope, key, _err = mm.groups()
                stats.metadata_failures[(scope, key)] += 1

            # 已知 bug 模式
            for sig, (pat, _) in BUG_PATTERNS.items():
                if pat.search(line):
                    stats.bug_signatures[sig] += 1

    stats.slow_queries.sort(key=lambda x: x[1], reverse=True)
    return stats


def parse_audit(path: Path) -> dict[str, dict[str, int]]:
    """audit.jsonl: 一行一个 JSON,统计 method+path 调用次数 + 状态分布。"""
    by_endpoint: dict[str, dict[str, int]] = collections.defaultdict(lambda: {"count": 0, "errors": 0, "users": set()})  # type: ignore[var-annotated]
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            method = rec.get("method", "")
            url_path = rec.get("path", "")
            status = int(rec.get("status_code") or 0)
            user = rec.get("user", "") or rec.get("username", "")
            key = f"{method} {_normalize_path(url_path)}"
            by_endpoint[key]["count"] += 1
            if status >= 400:
                by_endpoint[key]["errors"] += 1
            if user:
                by_endpoint[key]["users"].add(user)
    # 转 set → count
    for v in by_endpoint.values():
        v["users"] = len(v["users"])  # type: ignore[assignment]
    return dict(by_endpoint)


_ID_RE = re.compile(r"/[a-f0-9]{16,}")
_NUM_RE = re.compile(r"/\d+(?=/|$)")


def _normalize_path(p: str) -> str:
    """把 path 里 uuid / 数字 id 归一化为 * 防 cardinality 爆炸。"""
    p = _ID_RE.sub("/*", p)
    p = _NUM_RE.sub("/*", p)
    return p


# ─── render ─────────────────────────────────────────────────────────────────

def render(stats: LogStats, audit: dict | None, *, top_n: int = 20, md: bool = False) -> str:
    out: list[str] = []
    h1 = lambda s: f"\n## {s}\n" if md else f"\n=== {s} ===\n"
    h2 = lambda s: f"\n### {s}\n" if md else f"\n--- {s} ---\n"

    out.append(f"{'# ' if md else ''}DataOpsStudio 日志诊断报告")
    out.append("")
    out.append(f"- 时间范围: {stats.first_ts}  ->  {stats.last_ts}")
    out.append(f"- 总日志行数: {stats.total_lines:,}")
    out.append(f"- Traceback 数: {stats.tracebacks}")

    out.append(h1("Level 分布"))
    for lvl, n in stats.by_level.most_common():
        out.append(f"  - {lvl:<8} {n:>6,}")

    out.append(h1("已知 Bug 模式命中"))
    if stats.bug_signatures:
        for sig, n in stats.bug_signatures.most_common():
            _, hint = BUG_PATTERNS[sig]
            out.append(f"  - **{sig}**: {n} 次")
            out.append(f"    -> {hint}")
    else:
        out.append("  无")

    out.append(h1("Query 统计"))
    out.append(f"  - 发起: {stats.queries_started:,}")
    out.append(f"  - 成功: {stats.queries_succeeded:,}")
    out.append(f"  - 失败: {stats.queries_failed:,}")
    if stats.queries_started:
        fail_rate = stats.queries_failed / stats.queries_started * 100
        out.append(f"  - 失败率: {fail_rate:.2f}%")

    out.append(h2(f"慢查询 Top {top_n} (>=1.0s)"))
    for ts, elapsed, msg in stats.slow_queries[:top_n]:
        out.append(f"  [{ts}] {elapsed:.2f}s  {msg}")

    out.append(h2(f"按数据源调用次数 Top {top_n}"))
    for (ds, db_type), n in stats.by_datasource.most_common(top_n):
        failed = stats.failed_by_datasource.get(ds, 0)
        out.append(f"  - {ds} ({db_type}): {n} (failed: {failed})")

    out.append(h1(f"WARNING/ERROR 消息聚合 Top {top_n}"))
    for msg, n in stats.error_messages.most_common(top_n):
        out.append(f"  [{n:>4}] {msg}")

    if stats.metadata_failures:
        out.append(h1(f"Metadata 拉取失败 Top {top_n}"))
        for (scope, key), n in stats.metadata_failures.most_common(top_n):
            out.append(f"  - scope={scope} key={key}: {n}")

    if audit:
        out.append(h1(f"API 调用 Top {top_n} (来自 audit.jsonl)"))
        items = sorted(audit.items(), key=lambda x: x[1]["count"], reverse=True)[:top_n]
        for endpoint, info in items:
            err_pct = (info["errors"] / info["count"] * 100) if info["count"] else 0
            out.append(f"  - {endpoint:<60}  {info['count']:>6} 次  err {err_pct:.1f}%  users {info['users']}")

    out.append("")
    return "\n".join(out)


# ─── main ───────────────────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("log", type=Path, help="app.log 路径")
    p.add_argument("--audit", type=Path, help="audit.jsonl 路径(可选)")
    p.add_argument("--top", type=int, default=20, help="各排行榜 top N")
    p.add_argument("--slow", type=float, default=1.0, help="慢查询阈值(秒)")
    p.add_argument("--format", choices=["plain", "markdown"], default="plain")
    args = p.parse_args()

    if not args.log.exists():
        print(f"[ERROR] 日志文件不存在: {args.log}", file=sys.stderr)
        return 1

    print(f"[info] 扫描日志: {args.log} ({args.log.stat().st_size / 1024:.1f} KB)", file=sys.stderr)
    stats = parse_log(args.log, slow_threshold=args.slow)
    audit = None
    if args.audit and args.audit.exists():
        print(f"[info] 扫描审计: {args.audit}", file=sys.stderr)
        audit = parse_audit(args.audit)

    print(render(stats, audit, top_n=args.top, md=(args.format == "markdown")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
