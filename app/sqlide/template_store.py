"""SQL 工作台 v0.4 模板库 store。

跟 datasource_store 一样的 JsonStore 模式,**额外**做两件事:
1. **内置 example 合并**:仓库带的 `config/sql_templates.example.json` 在 list()
   时跟用户的 `config/sql_templates.json` union;example 条目带 `builtin=true`、
   id 前缀 `builtin:`,update/delete 拒绝改 —— 保证仓库示例永远跟 git 最新,
   不被用户误删/编辑。
2. **过滤参数**:支持 q / tags / db_types / project_id 几个筛选;前端不需要
   再做客户端二次过滤,直接 GET 时塞 query string。

builtin id 形如 `builtin:slow-query-baseline`,普通模板用 uuid hex(由 JsonStore
自动生成)。
"""
from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.models import SQLTemplate, SQLTemplateCreate, SQLTemplateUpdate
from app.utils.paths import SQL_TEMPLATES_EXAMPLE_FILE, SQL_TEMPLATES_FILE

logger = logging.getLogger(__name__)

BUILTIN_PREFIX = "builtin:"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _slugify(name: str) -> str:
    """example 文件里 `id` 字段缺失时,从 name 自动派生。"""
    return re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-") or "unnamed"


class SqlTemplateStore:
    def __init__(
        self,
        store_path: Path = SQL_TEMPLATES_FILE,
        example_path: Path = SQL_TEMPLATES_EXAMPLE_FILE,
    ) -> None:
        self._store_path = store_path
        self._example_path = example_path
        self._lock = threading.RLock()

    # ─── 读 ──────────────────────────────────────────────────────────────
    def list(
        self,
        *,
        q: str = "",
        tags: list[str] | None = None,
        db_type: str = "",
        project_id: str | None = None,
    ) -> list[SQLTemplate]:
        """合并用户 + 内置,按 updated_at desc 排序后过滤。

        过滤语义:
        - q:子串 case-insensitive 匹配 name / description / sql 任一
        - tags:模板必须包含全部 tags(AND)
        - db_type:模板 db_types 含此值或 "all" 才通过(模板"通用"也算)
        - project_id:None=不过滤;""=只看全局(project_id="");"X"=看 X + 全局
        """
        items = self._merge_user_and_builtin()
        if q:
            ql = q.lower()
            items = [
                t for t in items
                if ql in (t.name or "").lower()
                or ql in (t.description or "").lower()
                or ql in (t.sql or "").lower()
            ]
        if tags:
            tag_set = set(tags)
            items = [t for t in items if tag_set.issubset(set(t.tags))]
        if db_type:
            items = [t for t in items if (db_type in t.db_types) or ("all" in t.db_types)]
        if project_id is not None:
            if project_id == "":
                items = [t for t in items if t.project_id == ""]
            else:
                items = [t for t in items if t.project_id in ("", project_id)]
        return items

    def get(self, template_id: str) -> SQLTemplate | None:
        for t in self._merge_user_and_builtin():
            if t.id == template_id:
                return t
        return None

    # ─── 写 ──────────────────────────────────────────────────────────────
    def create(self, payload: SQLTemplateCreate, *, created_by: str) -> SQLTemplate:
        with self._lock:
            raw = self._read_user_raw()
            now = _now_iso()
            template = SQLTemplate(
                id=uuid.uuid4().hex,
                **payload.model_dump(),
                created_by=created_by,
                created_at=now,
                updated_at=now,
                builtin=False,
            )
            raw.append(template.model_dump(mode="json"))
            self._write_user_raw(raw)
            return template

    def update(self, template_id: str, payload: SQLTemplateUpdate) -> SQLTemplate:
        if template_id.startswith(BUILTIN_PREFIX):
            raise PermissionError("内置模板不可编辑,请克隆为新模板再改")
        with self._lock:
            raw = self._read_user_raw()
            for i, item in enumerate(raw):
                if item.get("id") == template_id:
                    merged = {
                        **item,
                        **payload.model_dump(),
                        "id": template_id,
                        "updated_at": _now_iso(),
                        "builtin": False,
                    }
                    template = SQLTemplate.model_validate(merged)
                    raw[i] = template.model_dump(mode="json")
                    self._write_user_raw(raw)
                    return template
            raise KeyError(template_id)

    def delete(self, template_id: str) -> None:
        if template_id.startswith(BUILTIN_PREFIX):
            raise PermissionError("内置模板不可删除")
        with self._lock:
            raw = self._read_user_raw()
            next_items = [i for i in raw if i.get("id") != template_id]
            if len(next_items) == len(raw):
                raise KeyError(template_id)
            self._write_user_raw(next_items)

    # ─── 批量导入/导出(v0.4 #18) ────────────────────────────────────────
    def import_templates(
        self,
        items: list[dict],
        *,
        created_by: str,
        overwrite_by_name: bool = False,
    ) -> dict[str, int]:
        """批量导入。**忽略 builtin 字段**,导入的统一标 builtin=False。
        `overwrite_by_name=True` 时:同名模板覆盖现有(用户的);否则跳过同名。
        返回 {created, skipped, errors}。"""
        report = {"created": 0, "skipped": 0, "errors": 0}
        with self._lock:
            raw = self._read_user_raw()
            existing_by_name = {item["name"]: i for i, item in enumerate(raw)}
            for it in items:
                try:
                    # 忽略 builtin / id / created_at / updated_at,让 store 重新派
                    clean = {k: v for k, v in it.items()
                             if k not in ("id", "builtin", "created_at", "updated_at", "created_by")}
                    payload = SQLTemplateCreate.model_validate(clean)
                except Exception as exc:
                    logger.warning("import skip invalid template %r: %s", it.get("name"), exc)
                    report["errors"] += 1
                    continue
                now = _now_iso()
                if payload.name in existing_by_name and overwrite_by_name:
                    idx = existing_by_name[payload.name]
                    template = SQLTemplate(
                        id=raw[idx]["id"],
                        **payload.model_dump(),
                        created_by=raw[idx].get("created_by", created_by),
                        created_at=raw[idx].get("created_at", now),
                        updated_at=now,
                        builtin=False,
                    )
                    raw[idx] = template.model_dump(mode="json")
                    report["created"] += 1
                elif payload.name in existing_by_name:
                    report["skipped"] += 1
                else:
                    template = SQLTemplate(
                        id=uuid.uuid4().hex,
                        **payload.model_dump(),
                        created_by=created_by,
                        created_at=now,
                        updated_at=now,
                        builtin=False,
                    )
                    raw.append(template.model_dump(mode="json"))
                    existing_by_name[payload.name] = len(raw) - 1
                    report["created"] += 1
            self._write_user_raw(raw)
        return report

    def export(
        self,
        *,
        include_builtin: bool = False,
        project_id: str | None = None,
    ) -> list[dict]:
        """导出 list(模板 dict)。builtin 默认不导出(避免一份 example 被反复
        重复导入);用户也可以选择包含 builtin 当冷备份。"""
        items = self._merge_user_and_builtin()
        if not include_builtin:
            items = [t for t in items if not t.builtin]
        if project_id is not None:
            items = [t for t in items if t.project_id in ("", project_id) or project_id == ""]
        return [t.model_dump(mode="json") for t in items]

    # ─── internal ────────────────────────────────────────────────────────
    def _read_user_raw(self) -> list[dict]:
        if not self._store_path.exists():
            self._store_path.parent.mkdir(parents=True, exist_ok=True)
            self._store_path.write_text("[]", encoding="utf-8")
            return []
        try:
            data = json.loads(self._store_path.read_text(encoding="utf-8") or "[]")
        except Exception as exc:
            logger.warning("sql_templates.json 损坏,降级到空 list: %s", exc)
            return []
        return data if isinstance(data, list) else []

    def _write_user_raw(self, data: list[dict]) -> None:
        self._store_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._store_path.with_name(f".{self._store_path.name}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._store_path)

    def _read_builtin(self) -> list[SQLTemplate]:
        """读 example 文件,补齐 builtin=true / id 前缀 / created_at 等字段。"""
        if not self._example_path.exists():
            return []
        try:
            data = json.loads(self._example_path.read_text(encoding="utf-8") or "[]")
        except Exception as exc:
            logger.warning("sql_templates.example.json 损坏: %s", exc)
            return []
        if not isinstance(data, list):
            return []
        out: list[SQLTemplate] = []
        for entry in data:
            try:
                # example 文件里允许只填业务字段,系统字段自动派
                name = entry.get("name") or "unnamed"
                slug = entry.get("id") or _slugify(name)
                tpl = SQLTemplate(
                    id=f"{BUILTIN_PREFIX}{slug}",
                    name=name,
                    description=entry.get("description", ""),
                    tags=list(entry.get("tags", [])),
                    db_types=list(entry.get("db_types", ["all"])),
                    project_id=entry.get("project_id", ""),
                    risk_level=entry.get("risk_level", "low"),
                    sql=entry.get("sql", ""),
                    created_by="system",
                    created_at=entry.get("created_at", ""),
                    updated_at=entry.get("updated_at", ""),
                    builtin=True,
                )
                out.append(tpl)
            except Exception as exc:
                logger.warning("跳过 example 中无效条目 %r: %s", entry.get("name"), exc)
        return out

    def _merge_user_and_builtin(self) -> list[SQLTemplate]:
        user = [SQLTemplate.model_validate(it) for it in self._read_user_raw()]
        builtin = self._read_builtin()
        # 用户优先(同名时 user 覆盖 builtin 的显示位置,但 builtin 不会被实际改)。
        # 不同 id 互不干涉。
        return sorted(user + builtin, key=lambda t: t.updated_at or t.created_at, reverse=True)


sql_template_store = SqlTemplateStore()
