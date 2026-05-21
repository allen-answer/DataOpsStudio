"""项目级授权 helper。

凡是直接接收 `datasource_id` 并执行 SQL / EXPLAIN / 预览 / introspect 的
endpoint，都必须调用 `require_datasource_access` 校验当前用户是否有权访问
该 datasource.project_id —— 否则任意持有 datasource_id 的用户都能跨项目
读取数据。

授权语义跟 `/api/projects` list 过滤一致：
- admin 全权
- 全局 datasource（project_id 为空）所有登录用户都可访问
- 否则 user 必须是该 project 的 owner 或 member
"""
from __future__ import annotations

from fastapi import HTTPException

from app.api.projects import project_store
from app.models import DataSource, User
from app.services.repositories import datasource_store


def can_access_project(current: User, project_id: str) -> bool:
    """Project 级访问判定。空 project_id = 全局资源，所有登录用户可见。"""
    if current.role == "admin":
        return True
    if not project_id:
        return True
    project = project_store.get(project_id)
    if project is None:
        return False
    return project.owner_id == current.id or current.id in project.members


def require_datasource_access(
    current: User,
    datasource_id: str,
    *,
    detail: str = "Datasource not found",
) -> DataSource:
    """校验 current user 对 datasource 所属 project 有访问权，返回该 datasource。

    - datasource 不存在 → 404（`detail` 文案）
    - datasource.project_id 当前用户没权访问 → 403

    返回 datasource 让调用方复用，避免再调一次 `datasource_store.get`。
    """
    datasource = datasource_store.get(datasource_id)
    if datasource is None:
        raise HTTPException(status_code=404, detail=detail)
    if not can_access_project(current, datasource.project_id or ""):
        raise HTTPException(status_code=403, detail="无权访问该数据源所属项目")
    return datasource
