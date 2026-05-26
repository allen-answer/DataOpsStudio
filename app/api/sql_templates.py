"""SQL 工作台 v0.4 模板库 endpoints。

URL 风格跟同期 SQL 工作台保持一致(`/api/sql-templates/...`)。

editor+ 才能 POST/PUT/DELETE,viewer 只读列表 + 详情(GET)。内置模板 (id 前缀
`builtin:`) 永远在响应里,但 update / delete 时 store 自己拒,返回 403。

import/export 是显式两个 endpoint,前端"上传 JSON" / "下载 JSON" 按钮直接调。
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.models import SQLTemplate, SQLTemplateCreate, SQLTemplateUpdate, User
from app.services.auth import require_role
from app.sqlide.template_store import sql_template_store

logger = logging.getLogger(__name__)


# 全局 viewer+:列表 / 详情可读;具体 POST/PUT/DELETE 用 require_role("editor")
router = APIRouter(dependencies=[Depends(require_role("viewer"))])


def _parse_csv(v: str) -> list[str]:
    return [x.strip() for x in v.split(",") if x.strip()] if v else []


@router.get("/api/sql-templates")
def list_templates(
    q: str = Query(default=""),
    tag: str = Query(default="", description="逗号分隔多 tag,AND 命中"),
    db_type: str = Query(default=""),
    project_id: str | None = Query(default=None),
) -> dict[str, Any]:
    """列模板,带过滤。返回 {items, count}。"""
    items = sql_template_store.list(
        q=q, tags=_parse_csv(tag) or None,
        db_type=db_type, project_id=project_id,
    )
    return {"items": [t.model_dump(mode="json") for t in items], "count": len(items)}


@router.get("/api/sql-templates/{template_id}")
def get_template(template_id: str) -> dict[str, Any]:
    t = sql_template_store.get(template_id)
    if not t:
        raise HTTPException(404, "template not found")
    return t.model_dump(mode="json")


@router.post("/api/sql-templates")
def create_template(
    payload: SQLTemplateCreate = Body(...),
    current: User = Depends(require_role("editor")),
) -> dict[str, Any]:
    t = sql_template_store.create(payload, created_by=current.id)
    return t.model_dump(mode="json")


@router.put("/api/sql-templates/{template_id}")
def update_template(
    template_id: str,
    payload: SQLTemplateUpdate = Body(...),
    current: User = Depends(require_role("editor")),
) -> dict[str, Any]:
    try:
        t = sql_template_store.update(template_id, payload)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except KeyError:
        raise HTTPException(404, "template not found")
    return t.model_dump(mode="json")


@router.delete("/api/sql-templates/{template_id}")
def delete_template(
    template_id: str,
    current: User = Depends(require_role("editor")),
) -> dict[str, Any]:
    try:
        sql_template_store.delete(template_id)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except KeyError:
        raise HTTPException(404, "template not found")
    return {"ok": True, "id": template_id}


@router.post("/api/sql-templates/import")
def import_templates(
    payload: dict = Body(..., description="{templates: [...], overwrite_by_name?: bool}"),
    current: User = Depends(require_role("editor")),
) -> dict[str, Any]:
    items = payload.get("templates") or []
    if not isinstance(items, list):
        raise HTTPException(400, "templates 必须是 list")
    overwrite = bool(payload.get("overwrite_by_name", False))
    report = sql_template_store.import_templates(
        items, created_by=current.id, overwrite_by_name=overwrite,
    )
    return {"ok": True, **report}


@router.get("/api/sql-templates/export/json")
def export_templates(
    include_builtin: bool = Query(default=False),
    project_id: str | None = Query(default=None),
) -> dict[str, Any]:
    """返 {templates: [...]} 让前端直接下载为 .json 文件。"""
    items = sql_template_store.export(include_builtin=include_builtin, project_id=project_id)
    return {"templates": items, "count": len(items)}
