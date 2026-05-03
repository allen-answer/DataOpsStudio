from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.utils.paths import RESULTS_DIR


def list_result_history(task_id: str = "", project_id: str = "") -> list[dict[str, Any]]:
    """列历史结果。
    - task_id 非空：仅匹配该 task 的 run
    - project_id 非空：仅匹配该项目下 task 的 run + task 已删的孤儿 run（保留历史）
    """
    # project 过滤要 join task_store —— lazy import 避免循环
    project_task_ids: set[str] | None = None
    if project_id:
        from app.services.repositories import task_store
        project_task_ids = {
            t.id for t in task_store.list()
            if (t.project_id or "") == project_id or not (t.project_id or "")
        }
    items = []
    for path in RESULTS_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        run_id = data.get("run_id") or path.stem
        excel_name = f"{run_id}.xlsx"
        excel_path = RESULTS_DIR / excel_name
        result_task_id = data.get("task_id", "")
        if task_id and result_task_id != task_id:
            continue
        if project_task_ids is not None and result_task_id and result_task_id not in project_task_ids:
            # 已知 task 但不归当前项目 —— 跳过；
            # task 已删（result_task_id 不在 task_store）的孤儿 run 仍展示
            from app.services.repositories import task_store as _task_store
            if _task_store.get(result_task_id) is not None:
                continue
        sort_time = _history_sort_time(data, path)
        result_type = _classify_result(data)
        items.append(
            {
                "run_id": run_id,
                "task_id": result_task_id,
                "task_name": data.get("task_name", ""),
                "started_at": data.get("started_at", ""),
                "elapsed_seconds": data.get("elapsed_seconds", 0),
                "source_rows": data.get("source_rows", 0),
                "target_rows": data.get("target_rows", 0),
                "summary": data.get("summary", {}),
                "sort_time": sort_time.isoformat(timespec="seconds"),
                "result_filename": path.name,
                "excel_filename": excel_name if excel_path.exists() else "",
                "type": result_type,
            }
        )
    return sorted(items, key=lambda item: item["sort_time"], reverse=True)


def delete_result(run_id: str) -> None:
    deleted = False
    for suffix in (".json", ".xlsx"):
        path = (RESULTS_DIR / f"{run_id}{suffix}").resolve()
        if RESULTS_DIR.resolve() in path.parents and path.exists():
            path.unlink()
            deleted = True
    if not deleted:
        raise KeyError(run_id)


def _history_sort_time(data: dict[str, Any], path: Path) -> datetime:
    started_at = data.get("started_at")
    if isinstance(started_at, str) and started_at.strip():
        try:
            return datetime.fromisoformat(started_at.strip())
        except ValueError:
            pass
    return datetime.fromtimestamp(path.stat().st_mtime)


def _classify_result(data: dict[str, Any]) -> str:
    if "files" in data or "table_edges" in data:
        return "lineage"
    return "compare"
