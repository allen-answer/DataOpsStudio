"""compare 任务的历史结果列表 / 删除 / 多选合并导出。

项目级隔离见 docs/PROJECT_AUTHORIZATION.md：list 按用户可见项目过滤，
删除 / 导出按 run_id → task.project_id 反查校验。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Query
from fastapi.responses import FileResponse

from app.api._authz import (
    accessible_project_ids,
    can_access_project,
    compare_result_project_id,
)
from app.models import HistoryItem, OkResponse, User
from app.services.auth import get_current_user, require_role
from app.services.history import delete_result, list_result_history
from app.services.history_exporter import export_history_sheets


# router 级 default：viewer 也要登录读历史。删除 / 多选导出升级 editor。
router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get("/api/history", response_model=list[HistoryItem])
def result_history_api(
    task_id: str = "",
    project_id: str = "",
    limit: int = Query(200, ge=1, le=2000, description="返回前 N 条；项目里历史多的话避免全量解析"),
    current: User = Depends(get_current_user),
):
    return list_result_history(
        task_id,
        project_id,
        limit=limit,
        allowed_project_ids=accessible_project_ids(current),
    )


@router.delete("/api/history/{run_id}", response_model=OkResponse)
def delete_history_api(run_id: str, current: User = Depends(require_role("editor"))):
    # 能解析出归属项目就校验；孤儿 run（无法归属）回落到仅 editor 角色门槛。
    project_id, resolved = compare_result_project_id(run_id)
    if resolved and not can_access_project(current, project_id):
        raise HTTPException(status_code=403, detail="无权删除该项目的历史结果")
    try:
        delete_result(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Result not found") from exc
    return {"ok": True}


@router.post("/history/export")
def export_history_page(
    run_ids: list[str] = Form(default=[]),
    sheet_names: list[str] = Form(default=[]),
    current: User = Depends(require_role("editor")),
):
    # 选中的每个 run 都要可访问 —— 有一个无权就整体 403，不静默漏导。
    for run_id in run_ids:
        project_id, resolved = compare_result_project_id(run_id)
        if resolved and not can_access_project(current, project_id):
            raise HTTPException(status_code=403, detail="选中的历史结果含无权访问的项目")
    try:
        path = export_history_sheets(run_ids, sheet_names)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(path, filename=path.name)
