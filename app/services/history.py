from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.utils.paths import RESULTS_DIR


def list_result_history(task_id: str = "") -> list[dict[str, Any]]:
    items = []
    for path in RESULTS_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        run_id = data.get("run_id") or path.stem
        excel_name = f"{run_id}.xlsx"
        excel_path = RESULTS_DIR / excel_name
        if task_id and data.get("task_id", "") != task_id:
            continue
        sort_time = _history_sort_time(data, path)
        result_type = _classify_result(data)
        items.append(
            {
                "run_id": run_id,
                "task_id": data.get("task_id", ""),
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
