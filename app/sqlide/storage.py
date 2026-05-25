"""SQL Workbench 单文件存储 —— `config/sql_workbench.json`。

跟通用 `JsonStore` 不同:
- JsonStore root 是 list,每个文件管一个 model
- 本 store root 是 dict `{"consoles": [...], "history": [...]}`,把 console + history
  收在同一文件(用户偏好 + Phase 1 规模小)
- 多用户隔离:每条 console / history 都带 owner_user_id;list 时按 user 过滤

History 是 append-only + ring buffer(cap 5000),防 file 无限膨胀。
"""
from __future__ import annotations

import json
import os
import stat as _stat
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.sqlide.models import Console, ConsoleCreate, ConsoleUpdate, HistoryEntry
from app.utils.paths import CONFIG_DIR


SQL_WORKBENCH_FILE = CONFIG_DIR / "sql_workbench.json"
_HISTORY_CAP = 5000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SqlWorkbenchStore:
    """Single-file store for {"consoles": [...], "history": [...]}.

    Thread-safe via RLock。多用户场景下 list_* 方法接 owner_user_id 过滤,admin
    传 owner_user_id="" 看全部。
    """

    def __init__(self, path: Path = SQL_WORKBENCH_FILE) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._cache: dict[str, list[dict]] | None = None
        self._cache_mtime_ns: int | None = None

    # ─── consoles ──────────────────────────────────────────────────────

    def list_consoles(self, *, owner_user_id: str = "") -> list[Console]:
        with self._lock:
            data = self._read_raw()
            items = data.get("consoles", [])
            if owner_user_id:
                items = [it for it in items if it.get("owner_user_id") == owner_user_id]
            return [Console.model_validate(it) for it in items]

    def get_console(self, console_id: str) -> Console | None:
        with self._lock:
            data = self._read_raw()
            for it in data.get("consoles", []):
                if it.get("id") == console_id:
                    return Console.model_validate(it)
            return None

    def create_console(self, payload: ConsoleCreate, *, owner_user_id: str) -> Console:
        with self._lock:
            data = self._read_raw()
            now = _now()
            console = Console(
                id=uuid.uuid4().hex,
                name=payload.name,
                datasource_id=payload.datasource_id,
                sql=payload.sql,
                project_id=payload.project_id,
                owner_user_id=owner_user_id,
                created_at=now,
                updated_at=now,
            )
            data.setdefault("consoles", []).append(console.model_dump(mode="json"))
            self._write_raw(data)
            return console

    def update_console(self, console_id: str, payload: ConsoleUpdate) -> Console:
        with self._lock:
            data = self._read_raw()
            for i, it in enumerate(data.get("consoles", [])):
                if it.get("id") != console_id:
                    continue
                # partial update —— payload 哪个字段非 None 才更新
                for field in ("name", "datasource_id", "sql", "project_id"):
                    value = getattr(payload, field)
                    if value is not None:
                        it[field] = value
                it["updated_at"] = _now()
                data["consoles"][i] = it
                self._write_raw(data)
                return Console.model_validate(it)
            raise KeyError(console_id)

    def delete_console(self, console_id: str) -> None:
        with self._lock:
            data = self._read_raw()
            before = data.get("consoles", [])
            after = [it for it in before if it.get("id") != console_id]
            if len(after) == len(before):
                raise KeyError(console_id)
            data["consoles"] = after
            self._write_raw(data)

    # ─── history ───────────────────────────────────────────────────────

    def append_history(self, entry: HistoryEntry) -> HistoryEntry:
        with self._lock:
            data = self._read_raw()
            history = data.setdefault("history", [])
            history.append(entry.model_dump(mode="json"))
            # ring buffer:超过 cap 砍最旧
            if len(history) > _HISTORY_CAP:
                data["history"] = history[-_HISTORY_CAP:]
            self._write_raw(data)
            return entry

    def list_history(
        self,
        *,
        owner_user_id: str = "",
        datasource_id: str = "",
        limit: int = 100,
    ) -> list[HistoryEntry]:
        with self._lock:
            data = self._read_raw()
            items = data.get("history", [])
            if owner_user_id:
                items = [it for it in items if it.get("executed_by") == owner_user_id]
            if datasource_id:
                items = [it for it in items if it.get("datasource_id") == datasource_id]
            # 倒序最近优先 + cap limit
            items = list(reversed(items))[:max(1, min(limit, 1000))]
            return [HistoryEntry.model_validate(it) for it in items]

    def invalidate_cache(self) -> None:
        with self._lock:
            self._cache = None
            self._cache_mtime_ns = None

    # ─── IO ────────────────────────────────────────────────────────────

    def _read_raw(self) -> dict[str, list[dict]]:
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._replace_file({"consoles": [], "history": []})
        mtime_ns = self.path.stat().st_mtime_ns
        if self._cache is not None and self._cache_mtime_ns == mtime_ns:
            return _deep_clone(self._cache)
        content = self.path.read_text(encoding="utf-8").strip() or "{}"
        data = json.loads(content)
        if not isinstance(data, dict):
            raise ValueError(f"{self.path} must contain a JSON object")
        data.setdefault("consoles", [])
        data.setdefault("history", [])
        self._cache = _deep_clone(data)
        self._cache_mtime_ns = mtime_ns
        return _deep_clone(self._cache)

    def _write_raw(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._replace_file(data)
        self._cache = _deep_clone(data)
        self._cache_mtime_ns = self.path.stat().st_mtime_ns

    def _replace_file(self, data: dict[str, Any]) -> None:
        content = json.dumps(data, ensure_ascii=False, indent=2)
        tmp_path = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        tmp_path.write_text(content, encoding="utf-8")
        # 跟 datasources.json 同口径 —— SQL 可能含敏感 schema/payload,owner-only
        try:
            os.chmod(tmp_path, _stat.S_IRUSR | _stat.S_IWUSR)
        except OSError:
            pass
        tmp_path.replace(self.path)


def _deep_clone(data: dict[str, Any]) -> dict[str, Any]:
    return {key: [dict(item) for item in value] if isinstance(value, list) else value for key, value in data.items()}


# Module-level singleton —— 跟 repositories.py 的 task_store / workflow_store 风格一致
sql_workbench_store = SqlWorkbenchStore()
