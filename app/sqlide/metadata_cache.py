"""SQL 工作台元数据缓存 —— 每 datasource 一个 JSON 文件,按 scope 分段写入。

设计目标:
1. **每个数据源独立**:一份 datasource 不会被另一份 datasource 的 cache 污染,
   切 datasource / 删 datasource 时只动一个文件。
2. **scope 维度分写**:schemas / tables / columns / indexes / views 五个维度
   分别带 `fetched_at`,可以单独刷新某一维度而不丢其他维度数据。
3. **stale-while-error**:`load_cache` 永远返回某种值(空或旧),解析失败 / 文件
   不在都不抛;真正"是否需要重拉"由 service 层判断。
4. **线程安全**:每 ds 一把 RLock,避免并发刷新写坏文件。

文件结构示例:
    config/metadata_cache/<datasource_id>.json
    {
      "datasource_id": "ds-uuid",
      "schemas": {"fetched_at": "2026-05-26T03:00:00+00:00", "items": [...]},
      "tables":  {"fetched_at": "...", "items": {"public": [...], "ods": [...]}},
      "columns": {"fetched_at": "...", "items": {"public.users": [...]}},
      "indexes": {"fetched_at": "...", "items": {"public.users": [...]}},
      "views":   {"fetched_at": "...", "items": {"public": [...]}}
    }

scope=None 表示"该维度从未拉过",前端可以据此提示"未缓存"。
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

# 默认 cache 目录。tests / 外部脚本可通过 set_cache_dir 改写,避免污染真实目录。
_DEFAULT_CACHE_DIR = Path("config/metadata_cache")
_cache_dir: Path = _DEFAULT_CACHE_DIR

Scope = Literal["schemas", "tables", "columns", "indexes", "views"]
SCOPES: tuple[Scope, ...] = ("schemas", "tables", "columns", "indexes", "views")

# per-ds 锁。dict 本身需要再加一把 guard,防止 lock 字典并发 race。
_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


def set_cache_dir(path: Path) -> None:
    """测试 hook:把缓存目录改到 tmp,避免污染 config/metadata_cache/。"""
    global _cache_dir
    _cache_dir = Path(path)


def get_cache_dir() -> Path:
    return _cache_dir


def _safe_filename(ds_id: str) -> str:
    """datasource_id 通常是 uuid hex,但用户可能起了非法文件名 → 白名单收口。"""
    return "".join(c if c.isalnum() or c in "_-" else "_" for c in ds_id) or "_unnamed"


def _cache_path(datasource_id: str) -> Path:
    return _cache_dir / f"{_safe_filename(datasource_id)}.json"


def _lock_for(ds_id: str) -> threading.RLock:
    with _locks_guard:
        lock = _locks.get(ds_id)
        if lock is None:
            lock = threading.RLock()
            _locks[ds_id] = lock
        return lock


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _empty_cache(ds_id: str) -> dict[str, Any]:
    return {
        "datasource_id": ds_id,
        **{scope: None for scope in SCOPES},
    }


def load_cache(datasource_id: str) -> dict[str, Any]:
    """读 ds 的 cache。文件不存在 / JSON 坏返空骨架,绝不抛 —— 让 service 层
    自行决定是否触发 live fetch。"""
    path = _cache_path(datasource_id)
    with _lock_for(datasource_id):
        if not path.exists():
            return _empty_cache(datasource_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("metadata cache %s 读失败,降级到空 cache: %s", path, exc)
            return _empty_cache(datasource_id)
        if not isinstance(data, dict):
            logger.warning("metadata cache %s 不是 dict,降级", path)
            return _empty_cache(datasource_id)
        # 补齐缺失字段(老 cache 缺新 scope 时不抛)
        for scope in SCOPES:
            data.setdefault(scope, None)
        data["datasource_id"] = datasource_id
        return data


def save_scope(datasource_id: str, scope: Scope, items: Any) -> dict[str, Any]:
    """覆盖单个 scope,其他 scope 保留原值。原子写(写临时文件再 rename)。返回写后 cache。"""
    if scope not in SCOPES:
        raise ValueError(f"unknown scope: {scope}")
    path = _cache_path(datasource_id)
    with _lock_for(datasource_id):
        cache = load_cache(datasource_id)
        cache[scope] = {"fetched_at": _now_iso(), "items": items}
        _write_atomic(path, cache)
        return cache


def get_scope(cache: dict[str, Any], scope: Scope) -> tuple[Any, str | None]:
    """从 load_cache 的返回里取某 scope 的 (items, fetched_at)。未拉过返 (None, None)。"""
    entry = cache.get(scope)
    if not isinstance(entry, dict):
        return None, None
    return entry.get("items"), entry.get("fetched_at")


def clear_cache(datasource_id: str, scope: Scope | None = None) -> None:
    """scope=None 删整个文件;指定 scope 仅清该维度(其他维度保留)。"""
    path = _cache_path(datasource_id)
    with _lock_for(datasource_id):
        if not path.exists():
            return
        if scope is None:
            try:
                path.unlink()
            except OSError as exc:
                logger.warning("删 cache 文件 %s 失败: %s", path, exc)
            return
        cache = load_cache(datasource_id)
        cache[scope] = None
        _write_atomic(path, cache)


def cache_summary(datasource_id: str) -> dict[str, str | None]:
    """返回每 scope 的 fetched_at(无值时 None),用于前端"缓存于 HH:MM"显示。"""
    cache = load_cache(datasource_id)
    summary: dict[str, str | None] = {}
    for scope in SCOPES:
        _, fetched_at = get_scope(cache, scope)
        summary[scope] = fetched_at
    return summary


def _write_atomic(path: Path, cache: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
