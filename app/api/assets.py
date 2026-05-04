"""Phase 10 第 4 项：/api/assets/* —— 资产详情查询。

把 table / task / field 当一等资产暴露详情页。MVP 仅 table 类型。
跟 DataHub / Atlan 的 entity API 思路对齐：'按资产名查 → 谁引用它'。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query


router = APIRouter()


@router.get("/api/assets/table/{name:path}")
def get_table_asset_api(
    name: str,
    project_id: str = Query("", description="项目空间过滤；空 = 不过滤"),
) -> dict[str, Any]:
    """返回表资产的详情：基本信息（schema/basename）+ 反向引用（tasks /
    workflows / lineage_scripts / history）。

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
