"""资产 aspect（classification）服务。

数据流：
- Schema 定义在 `config/asset_aspects.yml`（example 在 `config/asset_aspects.example.yml`）
- 实际值落 `data/dataops.db.asset_aspects` 表（Phase 10 enhancement）
- API：`/api/assets/table/{name}` 现在 includes `aspects` 字段；
  `/api/assets/aspects` PUT/DELETE 让 editor+ 编辑

跟 DataHub / Atlan custom aspect 思路对齐 —— schema 外置在 yaml，新加 aspect_type
不需要改 SQLite 表结构。每个 (asset_kind, asset_name, aspect_type, project_id) 唯一。

加载策略：
- yaml 文件不存在 → fallback 到 example（保证基础 type 永远可用）
- yaml 坏 → log warning + 用 fallback，不拖崩主流程
- 修改 yaml 后无需重启 —— `_load_schema()` 按 mtime 缓存失效
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services import sqlite_store
from app.utils.paths import CONFIG_DIR


logger = logging.getLogger(__name__)


_ASPECT_SCHEMA_FILE = CONFIG_DIR / "asset_aspects.yml"
_ASPECT_SCHEMA_EXAMPLE = CONFIG_DIR / "asset_aspects.example.yml"

_ASSET_KINDS = {"table", "task", "field"}


# ─── Schema 加载 ─────────────────────────────────────────────────────────────


_schema_cache: dict[str, Any] = {}
_schema_mtime: float = 0.0


def _load_schema() -> dict[str, Any]:
    """读 yaml schema。mtime 缓存失效（修改文件后下次调用自动重读）。"""
    global _schema_cache, _schema_mtime
    path = _ASPECT_SCHEMA_FILE if _ASPECT_SCHEMA_FILE.exists() else _ASPECT_SCHEMA_EXAMPLE
    if not path.exists():
        # 两个都不在 → 返回空 schema（所有 aspect_type 校验都会拒）
        return {"aspects": {}}
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    if _schema_cache and mtime == _schema_mtime:
        return _schema_cache
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        logger.warning("asset_aspects: failed to parse %s: %s; falling back to last known schema", path, exc)
        return _schema_cache or {"aspects": {}}
    if not isinstance(data, dict):
        return {"aspects": {}}
    aspects = data.get("aspects") or {}
    if not isinstance(aspects, dict):
        aspects = {}
    _schema_cache = {"aspects": aspects, "_source_path": str(path)}
    _schema_mtime = mtime
    return _schema_cache


def list_aspect_types() -> list[dict[str, Any]]:
    """前端拉可用 aspect type 列表用。返回每个 type 的 label / description /
    schema / color。"""
    schema = _load_schema()
    out: list[dict[str, Any]] = []
    for type_key, spec in (schema.get("aspects") or {}).items():
        if not isinstance(spec, dict):
            continue
        out.append({
            "type": type_key,
            "label": spec.get("label") or type_key,
            "description": spec.get("description") or "",
            "schema": spec.get("schema") or {},
            "color": spec.get("color") or "slate",
        })
    out.sort(key=lambda x: x["type"])
    return out


# ─── 校验 ────────────────────────────────────────────────────────────────────


def _validate_value(aspect_type: str, value: dict[str, Any]) -> None:
    """根据 yaml schema 校验 value dict。失败抛 ValueError。"""
    schema = _load_schema()
    spec = (schema.get("aspects") or {}).get(aspect_type)
    if not spec:
        raise ValueError(f"unknown aspect_type: {aspect_type}")
    fields = spec.get("schema") or {}
    if not isinstance(value, dict):
        raise ValueError("value must be a JSON object")
    for field_name, field_spec in fields.items():
        if not isinstance(field_spec, dict):
            continue
        required = bool(field_spec.get("required"))
        ftype = str(field_spec.get("type") or "string")
        present = field_name in value and value[field_name] not in (None, "")
        if required and not present:
            raise ValueError(f"field '{field_name}' is required for aspect '{aspect_type}'")
        if not present:
            continue
        v = value[field_name]
        if ftype == "string":
            if not isinstance(v, str):
                raise ValueError(f"field '{field_name}' must be string")
        elif ftype == "list":
            if not isinstance(v, list):
                raise ValueError(f"field '{field_name}' must be list")
        elif ftype == "enum":
            allowed = field_spec.get("values") or []
            if v not in allowed:
                raise ValueError(f"field '{field_name}' must be one of {allowed}")


# ─── CRUD ────────────────────────────────────────────────────────────────────


def list_aspects_for_asset(
    asset_kind: str, asset_name: str, *, project_id: str = ""
) -> list[dict[str, Any]]:
    """读 asset 的所有 aspects。project_id 空 = 不过滤；非空 = 仅返回 project 内 +
    全局（project_id="")。
    """
    if asset_kind not in _ASSET_KINDS:
        return []
    if not asset_name:
        return []
    with sqlite_store.connect() as conn:
        if project_id:
            cur = conn.execute(
                "SELECT aspect_type, value, project_id, updated_at, updated_by "
                "FROM asset_aspects WHERE asset_kind=? AND asset_name=? "
                "AND (project_id=? OR project_id='') ORDER BY aspect_type",
                (asset_kind, asset_name, project_id),
            )
        else:
            cur = conn.execute(
                "SELECT aspect_type, value, project_id, updated_at, updated_by "
                "FROM asset_aspects WHERE asset_kind=? AND asset_name=? "
                "ORDER BY aspect_type",
                (asset_kind, asset_name),
            )
        rows = cur.fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            value = json.loads(row["value"]) if row["value"] else {}
        except Exception:
            value = {}
        out.append({
            "aspect_type": row["aspect_type"],
            "value": value,
            "project_id": row["project_id"],
            "updated_at": row["updated_at"],
            "updated_by": row["updated_by"],
        })
    return out


def upsert_aspect(
    *,
    asset_kind: str,
    asset_name: str,
    aspect_type: str,
    value: dict[str, Any],
    project_id: str = "",
    updated_by: str = "",
) -> dict[str, Any]:
    """创建或更新一条 aspect。校验失败抛 ValueError。"""
    if asset_kind not in _ASSET_KINDS:
        raise ValueError(f"asset_kind must be one of {sorted(_ASSET_KINDS)}")
    asset_name = str(asset_name or "").strip()
    if not asset_name:
        raise ValueError("asset_name is required")
    aspect_type = str(aspect_type or "").strip()
    _validate_value(aspect_type, value)

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    value_json = json.dumps(value, ensure_ascii=False)
    with sqlite_store.connect() as conn:
        # UPSERT 用 INSERT ... ON CONFLICT；唯一键是 (kind, name, type, project)
        conn.execute(
            "INSERT INTO asset_aspects (asset_kind, asset_name, aspect_type, value, "
            "project_id, updated_at, updated_by) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(asset_kind, asset_name, aspect_type, project_id) DO UPDATE SET "
            "value=excluded.value, updated_at=excluded.updated_at, updated_by=excluded.updated_by",
            (asset_kind, asset_name, aspect_type, value_json, project_id, now, updated_by),
        )
    return {
        "aspect_type": aspect_type,
        "value": value,
        "project_id": project_id,
        "updated_at": now,
        "updated_by": updated_by,
    }


def delete_aspect(
    *,
    asset_kind: str,
    asset_name: str,
    aspect_type: str,
    project_id: str = "",
) -> bool:
    """删一条 aspect。命中返回 True；没找到返回 False。"""
    with sqlite_store.connect() as conn:
        cur = conn.execute(
            "DELETE FROM asset_aspects WHERE asset_kind=? AND asset_name=? "
            "AND aspect_type=? AND project_id=?",
            (asset_kind, asset_name, aspect_type, project_id),
        )
        return cur.rowcount > 0


def bulk_aspects_index(
    aspect_types: list[str],
    *,
    asset_kind: str = "table",
    project_id: str = "",
) -> dict[str, list[dict[str, Any]]]:
    """批量按 aspect_type 拉所有命中资产，返回 {asset_name: [aspect, ...]}。

    给 lineage graph "节点叠 PII / SLA / owner 徽章"用 —— 一次拉所有 PII / SLA /
    owner 标了的表，前端按 name lookup 决定哪个节点画徽章。

    无 aspect_types → 返回 {}。空列表语义不歧义（"我啥也不要"），不当 fallback。
    """
    if not aspect_types:
        return {}
    types = [str(t).strip() for t in aspect_types if str(t).strip()]
    if not types:
        return {}
    placeholders = ",".join("?" * len(types))
    out: dict[str, list[dict[str, Any]]] = {}
    with sqlite_store.connect() as conn:
        if project_id:
            cur = conn.execute(
                f"SELECT asset_name, aspect_type, value, project_id, updated_at, updated_by "
                f"FROM asset_aspects WHERE asset_kind=? AND aspect_type IN ({placeholders}) "
                f"AND (project_id=? OR project_id='')",
                (asset_kind, *types, project_id),
            )
        else:
            cur = conn.execute(
                f"SELECT asset_name, aspect_type, value, project_id, updated_at, updated_by "
                f"FROM asset_aspects WHERE asset_kind=? AND aspect_type IN ({placeholders})",
                (asset_kind, *types),
            )
        for row in cur.fetchall():
            try:
                value = json.loads(row["value"]) if row["value"] else {}
            except Exception:
                value = {}
            out.setdefault(row["asset_name"], []).append({
                "aspect_type": row["aspect_type"],
                "value": value,
                "project_id": row["project_id"],
                "updated_at": row["updated_at"],
                "updated_by": row["updated_by"],
            })
    return out


def search_assets_by_aspect(
    aspect_type: str,
    *,
    asset_kind: str = "table",
    project_id: str = "",
    limit: int = 100,
) -> list[dict[str, Any]]:
    """反向查找：哪些资产标了某个 aspect_type。让 admin 一眼看清"哪些表标 PII"。"""
    with sqlite_store.connect() as conn:
        if project_id:
            cur = conn.execute(
                "SELECT asset_kind, asset_name, value, project_id, updated_at, updated_by "
                "FROM asset_aspects WHERE aspect_type=? AND asset_kind=? "
                "AND (project_id=? OR project_id='') ORDER BY updated_at DESC LIMIT ?",
                (aspect_type, asset_kind, project_id, limit),
            )
        else:
            cur = conn.execute(
                "SELECT asset_kind, asset_name, value, project_id, updated_at, updated_by "
                "FROM asset_aspects WHERE aspect_type=? AND asset_kind=? "
                "ORDER BY updated_at DESC LIMIT ?",
                (aspect_type, asset_kind, limit),
            )
        rows = cur.fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            value = json.loads(row["value"]) if row["value"] else {}
        except Exception:
            value = {}
        out.append({
            "asset_kind": row["asset_kind"],
            "asset_name": row["asset_name"],
            "aspect_type": aspect_type,
            "value": value,
            "project_id": row["project_id"],
            "updated_at": row["updated_at"],
            "updated_by": row["updated_by"],
        })
    return out


__all__ = [
    "list_aspect_types",
    "list_aspects_for_asset",
    "upsert_aspect",
    "delete_aspect",
    "search_assets_by_aspect",
    "bulk_aspects_index",
]
