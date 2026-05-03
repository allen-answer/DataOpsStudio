"""Project CRUD —— 多项目空间。

资源（datasource / task / workflow）通过 project_id 字段关联到 project；
admin 可看全部，editor 在 owned/member 项目里 CRUD，viewer 只读。
本轮先做 CRUD + members，权限筛选在下一步迭代加。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException

from app.models import OkResponse, Project, ProjectCreate, User
from app.services.auth import get_current_user, require_role
from app.services.json_store import JsonStore
from app.utils.paths import PROJECTS_FILE

router = APIRouter()

project_store: JsonStore[Project, ProjectCreate] = JsonStore(PROJECTS_FILE, Project)


@router.get("/api/projects", response_model=list[Project])
def list_projects(current: User = Depends(get_current_user)):
    items = project_store.list()
    if current.role == "admin":
        return items
    # editor / viewer 只能看自己 owned 或 member 的项目
    return [p for p in items if p.owner_id == current.id or current.id in p.members]


@router.post("/api/projects", response_model=Project)
def create_project(
    payload: ProjectCreate,
    current: User = Depends(require_role("editor")),
):
    import json
    project = Project(
        id=uuid.uuid4().hex,
        name=payload.name,
        description=payload.description,
        owner_id=current.id,
        # owner 自动加进 members；显式给的额外成员追加（去重）
        members=list({current.id, *payload.members}),
        created_at=datetime.now().isoformat(timespec="seconds"),
    )
    target = project_store.path
    target.parent.mkdir(parents=True, exist_ok=True)
    raw = json.loads(target.read_text(encoding="utf-8") or "[]") if target.exists() else []
    raw.append(project.model_dump(mode="json"))
    target.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    project_store.invalidate_cache()
    return project


@router.put("/api/projects/{project_id}", response_model=Project)
def update_project(
    project_id: str,
    payload: ProjectCreate,
    current: User = Depends(get_current_user),
):
    target = project_store.get(project_id)
    if target is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    # 只有 owner / admin 能改
    if current.role != "admin" and target.owner_id != current.id:
        raise HTTPException(status_code=403, detail="只有项目 owner 或 admin 能修改项目")
    import json
    next_data = target.model_dump()
    next_data["name"] = payload.name
    next_data["description"] = payload.description
    next_data["members"] = list({target.owner_id, *payload.members})
    file = project_store.path
    raw = json.loads(file.read_text(encoding="utf-8") or "[]")
    for i, item in enumerate(raw):
        if item.get("id") == project_id:
            raw[i] = next_data
            break
    file.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    project_store.invalidate_cache()
    return Project.model_validate(next_data)


@router.delete("/api/projects/{project_id}", response_model=OkResponse)
def delete_project(
    project_id: str,
    current: User = Depends(get_current_user),
):
    target = project_store.get(project_id)
    if target is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    if current.role != "admin" and target.owner_id != current.id:
        raise HTTPException(status_code=403, detail="只有项目 owner 或 admin 能删除项目")
    project_store.delete(project_id)
    return {"ok": True}
