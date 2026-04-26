from __future__ import annotations

import json
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
                "result_filename": path.name,
                "excel_filename": excel_name if excel_path.exists() else "",
            }
        )
    return sorted(items, key=lambda item: item.get("started_at") or item["run_id"], reverse=True)


def delete_result(run_id: str) -> None:
    deleted = False
    for suffix in (".json", ".xlsx"):
        path = (RESULTS_DIR / f"{run_id}{suffix}").resolve()
        if RESULTS_DIR.resolve() in path.parents and path.exists():
            path.unlink()
            deleted = True
    if not deleted:
        raise KeyError(run_id)
