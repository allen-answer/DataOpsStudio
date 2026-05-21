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
    allowed_project_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """列历史结果。
    - task_id 非空：仅匹配该 task 的 run
    - project_id 非空：仅匹配该项目下 task 的 run + task 已删的孤儿 run（保留历史）
    - allowed_project_ids 非空：用户级项目隔离（见 docs/PROJECT_AUTHORIZATION.md）。
      只返回归属于这些项目（或全局 task）的 run。`None` = 不限制（admin）。
      非 admin 场景下 task 已删 / 无 task_id 的孤儿 run 一律隐藏（无法核实归属）。
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
    # 用户级项目隔离：预算「可见 task id 集合」（全局 task + 用户项目下的 task）。
    # 不在集合里的 result_task_id（含孤儿 / 空）一律跳过。
    accessible_task_ids: set[str] | None = None
    if allowed_project_ids is not None:
        from app.services.repositories import task_store
        accessible_task_ids = {
            t.id for t in task_store.list()
            if not (t.project_id or "") or t.project_id in allowed_project_ids
        }
    # 两类 result 文件：
    # - legacy：RESULTS_DIR/<run_id>.json（writer slice A 老格式）
    # - parquet：RESULTS_DIR/<run_id>/meta.json（writer slice B 新目录格式）
    legacy_paths = list(RESULTS_DIR.glob("*.json"))
    parquet_metas = [p for p in RESULTS_DIR.glob("*/meta.json") if p.is_file()]
    paths = legacy_paths + parquet_metas
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
        is_parquet = path.name == "meta.json" and path.parent != RESULTS_DIR
        # parquet 模式：data["run_id"] 跟目录名应当一致；fallback 用目录名
        run_id = data.get("run_id") or (path.parent.name if is_parquet else path.stem)
        excel_name = f"{run_id}.xlsx"
        excel_path = RESULTS_DIR / excel_name
        result_task_id = data.get("task_id", "")
        if task_id and result_task_id != task_id:
            continue
        # 用户级隔离：result 必须挂在用户可见的 task 上。孤儿 / 无 task_id 的
        # run 不在 accessible_task_ids 里 → 跳过（非 admin 无法核实归属）。
        if accessible_task_ids is not None and result_task_id not in accessible_task_ids:
            continue
        if project_task_ids is not None and result_task_id and result_task_id not in project_task_ids:
            # 已知 task 但不归当前项目 —— 跳过；
            # task 已删（result_task_id 不在 task_store）的孤儿 run 仍展示
            from app.services.repositories import task_store as _task_store
            if _task_store.get(result_task_id) is not None:
                continue
        sort_time = _history_sort_time(data, path)
        result_type = _classify_result(data)
        # parquet meta.json 的 result_filename 走 `<run_id>/meta.json` —— 前端
        # 直链下载这文件能拿到 envelope；切片 D 加 detail UI 时再换成
        # /api/runs/<id>/meta endpoint。
        result_filename = f"{run_id}/meta.json" if is_parquet else path.name
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
                "result_filename": result_filename,
                "excel_filename": excel_name if excel_path.exists() else "",
                "type": result_type,
                "format": data.get("format", "parquet" if is_parquet else "json"),
            }
        )
    items.sort(key=lambda item: item["sort_time"], reverse=True)
    if limit is not None:
        return items[:limit]
    return items


def delete_result(run_id: str) -> None:
    """删 run 产物。两种格式都尝试，至少删了一个才算成功。"""
    from app.services.run_result import delete_run

    delete_run(run_id)


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
