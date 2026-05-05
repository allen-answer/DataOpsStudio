"""Phase 10 第 4 项：/api/assets/* —— 资产详情查询。

把 table / task / field 当一等资产暴露详情页。MVP 仅 table 类型。
跟 DataHub / Atlan 的 entity API 思路对齐：'按资产名查 → 谁引用它'。

Phase 10 enhancement：custom aspect —— editor+ 给资产挂 owner / pii / sla /
sensitive / tag / business_term。schema 由 config/asset_aspects.yml 外置定义。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.models.user import User
from app.services.auth import require_role


router = APIRouter()


@router.get("/api/assets/table/{name:path}")
def get_table_asset_api(
    name: str,
    project_id: str = Query("", description="项目空间过滤；空 = 不过滤"),
) -> dict[str, Any]:
    """返回表资产的详情：基本信息（schema/basename）+ 反向引用（tasks /
    workflows / lineage_scripts / history）+ aspects。

    `name` 用 path-converter `:path` 接收，让 `ods.t_users` 这种含点的表名
    能直接走 URL（不用 URL-encode 点号）。
    """
    from app.services.assets import get_table_asset
    try:
        return get_table_asset(name, project_id=project_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/assets/datasources")
def list_datasource_assets_api(
    project_id: str = Query("", description="项目空间过滤；空 = 不过滤"),
) -> list[dict[str, Any]]:
    """所有 datasource 列表（资产视图，跟 /api/datasources 不同的是不含 password）。"""
    from app.services.assets import list_datasource_assets
    return list_datasource_assets(project_id=project_id)


# ─── Aspect / classification API ─────────────────────────────────────────────


class AspectUpsertBody(BaseModel):
    """PUT /api/assets/aspects —— 创建或覆盖一条 aspect。"""

    asset_kind: str = Field(..., description="table / task / field（前期仅 table）")
    asset_name: str = Field(..., min_length=1, description="表名 ods.t_users，含 schema")
    aspect_type: str = Field(..., min_length=1, description="见 GET /api/assets/aspects/types")
    value: dict[str, Any] = Field(default_factory=dict, description="JSON value，结构由 yml schema 决定")
    project_id: str = Field("", description="资产可见范围；空 = 全局")


@router.get("/api/assets/aspects/types")
def list_aspect_types_api() -> list[dict[str, Any]]:
    """前端拉可用 aspect type schema —— 决定编辑器渲染什么字段。"""
    from app.services.asset_aspects import list_aspect_types
    return list_aspect_types()


@router.put("/api/assets/aspects")
def upsert_aspect_api(
    body: AspectUpsertBody,
    current: User = Depends(require_role("editor")),
) -> dict[str, Any]:
    """新建或更新一条 aspect。需 editor+。同 (kind, name, type, project) 触发 UPSERT。"""
    from app.services.asset_aspects import upsert_aspect
    try:
        return upsert_aspect(
            asset_kind=body.asset_kind,
            asset_name=body.asset_name,
            aspect_type=body.aspect_type,
            value=body.value,
            project_id=body.project_id,
            updated_by=current.username,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/api/assets/aspects")
def delete_aspect_api(
    asset_kind: str = Query(..., description="table / task / field"),
    asset_name: str = Query(..., min_length=1),
    aspect_type: str = Query(..., min_length=1),
    project_id: str = Query("", description="必须跟当初 upsert 时一致"),
    _: User = Depends(require_role("editor")),
) -> dict[str, Any]:
    """删除一条 aspect。命中返回 deleted=true；未命中 deleted=false（不抛 404，
    幂等接口让前端不用先查再删）。"""
    from app.services.asset_aspects import delete_aspect
    deleted = delete_aspect(
        asset_kind=asset_kind,
        asset_name=asset_name,
        aspect_type=aspect_type,
        project_id=project_id,
    )
    return {"deleted": deleted}


@router.get("/api/assets/aspects/search")
def search_assets_by_aspect_api(
    aspect_type: str = Query(..., min_length=1),
    asset_kind: str = Query("table"),
    project_id: str = Query("", description="项目空间过滤"),
    limit: int = Query(100, ge=1, le=500),
) -> list[dict[str, Any]]:
    """反向查找：哪些资产标了某个 aspect_type。让 admin 一眼看清"哪些表标 PII"。"""
    from app.services.asset_aspects import search_assets_by_aspect
    return search_assets_by_aspect(
        aspect_type,
        asset_kind=asset_kind,
        project_id=project_id,
        limit=limit,
    )
