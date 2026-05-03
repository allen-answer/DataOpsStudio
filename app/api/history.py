"""compare 任务的历史结果列表 / 删除 / 多选合并导出。"""
from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import FileResponse

from app.models import HistoryItem, OkResponse
from app.services.history import delete_result, list_result_history
from app.services.history_exporter import export_history_sheets


router = APIRouter()


@router.get("/api/history", response_model=list[HistoryItem])
def result_history_api(task_id: str = "", project_id: str = ""):
    return list_result_history(task_id, project_id)


@router.delete("/api/history/{run_id}", response_model=OkResponse)
def delete_history_api(run_id: str):
    try:
        delete_result(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Result not found") from exc
    return {"ok": True}


@router.post("/history/export")
def export_history_page(
    run_ids: list[str] = Form(default=[]),
    sheet_names: list[str] = Form(default=[]),
):
    try:
        path = export_history_sheets(run_ids, sheet_names)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(path, filename=path.name)
