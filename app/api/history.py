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
    offset: int = Query(0, ge=0, description="标准分页起点；HistoryView loadMore 第 N 页传 N*PAGE_SIZE"),
    current: User = Depends(get_current_user),
):
    return list_result_history(
        task_id,
        project_id,
        limit=limit,
        offset=offset,
        allowed_project_ids=accessible_project_ids(current),
    )


@router.get("/api/runs/index")
def runs_index_api(
    project_id: str = "",
    status: str = Query("", description="reserved/running/success/failed/cancelled/aborted_guard/deleted;空 = 不过滤"),
    limit: int = Query(100, ge=1, le=1000),
    current: User = Depends(get_current_user),
):
    """Wave 5 #21:直接从 run_index 表读 — O(1) SQL,跟 /api/history 的扫文件
    路径互补。给前端 dashboard / quota 监控 / guard abort 审计页用。

    用户级项目隔离:非 admin 仅能看 accessible_project_ids 内的 run。
    admin 不传 project_id 时看全部。
    """
    from app.services import run_index as _run_index_mod
    accessible = accessible_project_ids(current)

    if project_id:
        if accessible is not None and project_id not in accessible:
            raise HTTPException(status_code=403, detail="无权查看该项目")
        rows = _run_index_mod.list_by_project(project_id, status=status or None, limit=limit)
    elif accessible is None:
        # admin:跨所有 project
        # 暂未提供 list_all,简化为按状态过滤汇总(后续 P3 enhancement 再扩)
        from app.services.sqlite_store import connect as _sqc
        sql = "SELECT * FROM run_index"
        params: list = []
        if status:
            sql += " WHERE status=?"
            params.append(status)
        sql += " ORDER BY requested_at DESC LIMIT ?"
        params.append(limit)
        with _sqc() as conn:
            cur = conn.execute(sql, tuple(params))
            rows = [_run_index_mod._row_to_record(r) for r in cur.fetchall()]
    else:
        # 非 admin 不传 project_id 时遍历自己可访问的项目
        rows = []
        for pid in accessible:
            rows.extend(_run_index_mod.list_by_project(pid, status=status or None, limit=limit))
        rows.sort(key=lambda r: r.requested_at, reverse=True)
        rows = rows[:limit]

    return {
        "items": [
            {
                "run_id": r.run_id, "task_id": r.task_id, "job_id": r.job_id,
                "workflow_run_id": r.workflow_run_id,
                "project_id": r.project_id, "owner_user_id": r.owner_user_id,
                "source_ds_id": r.source_ds_id, "target_ds_id": r.target_ds_id,
                "status": r.status,
                "requested_at": r.requested_at, "started_at": r.started_at,
                "finished_at": r.finished_at,
                "result_format": r.result_format, "stream_compare": r.stream_compare,
                "max_rows": r.max_rows,
                "disk_bytes": r.disk_bytes, "peak_rss_mb": r.peak_rss_mb,
                "guard_reason": r.guard_reason, "error": r.error,
            }
            for r in rows
        ],
        "count": len(rows),
    }


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
