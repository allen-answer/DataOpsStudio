from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.utils.paths import RESULTS_DIR


def list_result_history(
    task_id: str = "",
    project_id: str = "",
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """列历史结果。
    - task_id 非空：仅匹配该 task 的 run
    - project_id 非空：仅匹配该项目下 task 的 run + task 已删的孤儿 run（保留历史）
    - limit：截断返回前 N 条（None = 全量保 backward compat）。设了 limit 时
      先按文件 mtime DESC 排序（fs metadata 免读），只读 `limit*2+10` 个候选
      JSON，再按 `sort_time`（started_at 优先 / mtime 兜底）二次排序。跟
      `list_workflow_runs` 同套路，避免「目录里几千条历史每次都全量读」。
    """
    # project 过滤要 join task_store —— lazy import 避免循环
    project_task_ids: set[str] | None = None
    if project_id:
        from app.services.repositories import task_store
        project_task_ids = {
            t.id for t in task_store.list()
            if (t.project_id or "") == project_id or not (t.project_id or "")
        }
    paths = list(RESULTS_DIR.glob("*.json"))
    if limit is not None:
        # 先按 mtime 预排，限制读取量。读取预算 hedge 2× 应对 mtime ≠ sort_time
        # 偏离（compare 任务跑得久才落盘）。
        paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        read_budget = max(limit * 2 + 10, limit + 20)
    else:
        read_budget = None
    items = []
    for path in paths:
        if read_budget is not None and len(items) >= read_budget:
            break
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
    items.sort(key=lambda item: item["sort_time"], reverse=True)
    if limit is not None:
        return items[:limit]
    return items


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
