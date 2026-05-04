"""app.ai.usage_log —— AI 调用使用记录（Phase 9 Day 4 新增）。

每次 provider 调用追加一条 JSONL 到 `logs/ai_usage.jsonl`，便于：
- admin 在 SchedulerMonitor / AIConfigView 看 token 消耗
- 排查"AI 突然变慢 / 报错率上升"
- 计费 / 配额管理

格式（一行一条 JSON）：
```
{
  "ts": "2026-05-04T10:23:45.123+00:00",
  "kind": "enrichment" | "inference" | "column_attribution" | "error_translate" | "column_mapping",
  "provider": "openai" | "anthropic" | "mock" | "ollama" | ...,
  "model": "kimi-k2-thinking",
  "elapsed_ms": 1234,
  "status": "ok" | "error" | "timeout",
  "input_tokens": 1024,
  "output_tokens": 256,
  "error": "...optional...",
  "request_id": "...optional ContextVar from middleware..."
}
```

best-effort：写文件失败只 log warning，不抛错。
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

UsageKind = Literal[
    "enrichment",
    "inference",
    "dynamic_sql",
    "column_attribution",
    "error_translate",
    "column_mapping",
]

UsageStatus = Literal["ok", "error", "timeout"]

_LOG_PATH = Path("logs/ai_usage.jsonl")
_LOCK = threading.Lock()


def log_call(
    *,
    kind: UsageKind,
    provider: str,
    model: str,
    elapsed_ms: int,
    status: UsageStatus,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    error: str | None = None,
    request_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """记一条 AI 调用使用记录到 logs/ai_usage.jsonl。

    线程安全（threading.Lock 保护 append）。失败静默降级（不能拖崩主流程）。
    """
    record: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        "provider": provider,
        "model": model,
        "elapsed_ms": int(elapsed_ms),
        "status": status,
    }
    if input_tokens is not None:
        record["input_tokens"] = int(input_tokens)
    if output_tokens is not None:
        record["output_tokens"] = int(output_tokens)
    if error:
        record["error"] = str(error)[:500]
    if request_id:
        record["request_id"] = request_id
    if extra:
        record.update(extra)

    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            with _LOG_PATH.open("a", encoding="utf-8") as fp:
                fp.write(json.dumps(record, ensure_ascii=False))
                fp.write("\n")
    except Exception as exc:
        logger.warning("ai_usage_log write failed: %s", exc)


def read_recent(limit: int = 100) -> list[dict[str, Any]]:
    """读最近 N 条记录，按时间倒序。失败返回空列表。"""
    if not _LOG_PATH.exists():
        return []
    try:
        with _LOG_PATH.open("r", encoding="utf-8") as fp:
            lines = fp.readlines()
    except Exception as exc:
        logger.warning("ai_usage_log read failed: %s", exc)
        return []
    out: list[dict[str, Any]] = []
    # 倒序读最后 limit 行
    for line in lines[-limit:][::-1]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


__all__ = ["log_call", "read_recent", "UsageKind", "UsageStatus"]
