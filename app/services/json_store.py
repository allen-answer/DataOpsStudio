from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path
from typing import Generic, TypeVar

from pydantic import BaseModel


ModelT = TypeVar("ModelT", bound=BaseModel)
CreateT = TypeVar("CreateT", bound=BaseModel)


class JsonStore(Generic[ModelT, CreateT]):
    def __init__(self, path: Path, model: type[ModelT]) -> None:
        self.path = path
        self.model = model
        self._lock = threading.RLock()
        self._cache: list[dict] | None = None
        self._cache_mtime_ns: int | None = None

    def list(self) -> list[ModelT]:
        with self._lock:
            return [self.model.model_validate(item) for item in self._read_raw()]

    def get(self, item_id: str) -> ModelT | None:
        with self._lock:
            for item in self._read_raw():
                if item.get("id") == item_id:
                    return self.model.model_validate(item)
            return None

    def create(self, payload: CreateT) -> ModelT:
        with self._lock:
            raw_items = self._read_raw()
            item = self.model.model_validate({"id": uuid.uuid4().hex, **payload.model_dump()})
            raw_items.append(item.model_dump(mode="json"))
            self._write_raw(raw_items)
            return item

    def update(self, item_id: str, payload: CreateT) -> ModelT:
        with self._lock:
            raw_items = self._read_raw()
            for index, existing in enumerate(raw_items):
                if existing.get("id") == item_id:
                    item = self.model.model_validate({"id": item_id, **payload.model_dump()})
                    raw_items[index] = item.model_dump(mode="json")
                    self._write_raw(raw_items)
                    return item
            raise KeyError(item_id)

    def delete(self, item_id: str) -> None:
        with self._lock:
            raw_items = self._read_raw()
            next_items = [item for item in raw_items if item.get("id") != item_id]
            if len(next_items) == len(raw_items):
                raise KeyError(item_id)
            self._write_raw(next_items)

    def invalidate_cache(self) -> None:
        with self._lock:
            self._cache = None
            self._cache_mtime_ns = None

    def _read_raw(self) -> list[dict]:
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._replace_file([])
        mtime_ns = self.path.stat().st_mtime_ns
        if self._cache is not None and self._cache_mtime_ns == mtime_ns:
            return [dict(item) for item in self._cache]
        content = self.path.read_text(encoding="utf-8").strip() or "[]"
        data = json.loads(content)
        if not isinstance(data, list):
            raise ValueError(f"{self.path} must contain a JSON array")
        self._cache = [dict(item) for item in data]
        self._cache_mtime_ns = mtime_ns
        return [dict(item) for item in self._cache]

    def _write_raw(self, data: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._replace_file(data)
        self._cache = [dict(item) for item in data]
        self._cache_mtime_ns = self.path.stat().st_mtime_ns

    def _replace_file(self, data: list[dict]) -> None:
        import os
        import stat as _stat

        content = json.dumps(data, ensure_ascii=False, indent=2)
        tmp_path = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        tmp_path.write_text(content, encoding="utf-8")
        # 数据源 JSON 含明文密码 —— 在 POSIX 系统上限制为 600（仅 owner 可读
        # 写）防止系统其它用户 cat 出来。Windows 上 chmod 行为不同（不分用户
        # 组），os.chmod 仍然能调，但实际 ACL 控制不在 mode 位上 —— noop 即可，
        # 没有副作用。
        try:
            os.chmod(tmp_path, _stat.S_IRUSR | _stat.S_IWUSR)
        except OSError:
            pass
        tmp_path.replace(self.path)
