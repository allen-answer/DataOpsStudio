"""WorkflowRun persistence — mirrors services/history.py for compare runs.

Each finished WorkflowRun is serialized to results/workflow_runs/<run_id>.json.
Listings glob the directory and decode summaries; full run detail is read on
demand. Storage layout deliberately separate from compare results so the two
histories don't accidentally collide on filenames.
"""
from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from app.models import WorkflowRun
from app.utils.paths import WORKFLOW_RUNS_DIR


logger = logging.getLogger(__name__)


def persist_workflow_run(run: WorkflowRun) -> Path:
    """Write `run` to results/workflow_runs/<run_id>.json. Returns the path.
    Failures are logged but not raised — persistence is best-effort and
    must not break the run itself."""
    try:
        WORKFLOW_RUNS_DIR.mkdir(parents=True, exist_ok=True)
        path = WORKFLOW_RUNS_DIR / f"{run.run_id}.json"
        path.write_text(json.dumps(run.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
        return path
    except Exception:
        logger.exception("failed to persist workflow run %s", run.run_id)
        return WORKFLOW_RUNS_DIR / f"{run.run_id}.json"  # path is informational; caller doesn't depend on existence


def list_workflow_runs(workflow_id: str = "", limit: int = 50) -> list[dict[str, Any]]:
    """Return summaries (not full payloads) of past runs, newest first.
    Pass `workflow_id` to filter, or empty string for all."""
    if not WORKFLOW_RUNS_DIR.exists():
        return []
    items: list[dict[str, Any]] = []
    for path in WORKFLOW_RUNS_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if workflow_id and data.get("workflow_id") != workflow_id:
            continue
        node_runs = data.get("nodes") or []
        items.append(
            {
                "run_id": data.get("run_id") or path.stem,
                "workflow_id": data.get("workflow_id", ""),
                "workflow_name": data.get("workflow_name", ""),
                "status": data.get("status", ""),
                "started_at": data.get("started_at", ""),
                "finished_at": data.get("finished_at", ""),
                "elapsed_seconds": data.get("elapsed_seconds", 0),
                "error": data.get("error", ""),
                "node_count": len(node_runs),
                "node_status_counts": _count_node_statuses(node_runs),
                "_sort_time": _sort_time(data, path),
            }
        )
    items.sort(key=lambda item: item["_sort_time"], reverse=True)
    for item in items:
        item.pop("_sort_time", None)
    return items[:limit]


def get_workflow_run(run_id: str) -> dict[str, Any] | None:
    """Read a full WorkflowRun payload by id. Returns None if not found."""
    path = (WORKFLOW_RUNS_DIR / f"{run_id}.json").resolve()
    if WORKFLOW_RUNS_DIR.resolve() not in path.parents or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("failed to read workflow run %s", run_id)
        return None


def delete_workflow_run(run_id: str) -> None:
    """删 run JSON + 连带清理它所有 artifacts。

    每个 run 的产物归档到 WORKFLOW_RUNS_DIR/<run_id>/（excel_export 等节点
    把文件落到 <run_id>/exports/）。直接 rmtree 整个目录就把 artifacts 清掉，
    不需要遍历 run.artifacts 一个个删。

    路径用 resolve + parents 校验防 traversal——避免 run_id='..' 这类输入
    把 results/ 整个目录干掉。
    """
    json_path = (WORKFLOW_RUNS_DIR / f"{run_id}.json").resolve()
    if WORKFLOW_RUNS_DIR.resolve() not in json_path.parents or not json_path.exists():
        raise KeyError(run_id)
    json_path.unlink()

    artifacts_dir = (WORKFLOW_RUNS_DIR / run_id).resolve()
    if (
        WORKFLOW_RUNS_DIR.resolve() in artifacts_dir.parents
        and artifacts_dir.is_dir()
    ):
        shutil.rmtree(artifacts_dir, ignore_errors=True)


def _count_node_statuses(node_runs: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for node in node_runs:
        status = node.get("status", "")
        counts[status] = counts.get(status, 0) + 1
    return counts


def _sort_time(data: dict[str, Any], path: Path) -> datetime:
    started_at = data.get("started_at")
    if isinstance(started_at, str) and started_at.strip():
        try:
            return datetime.fromisoformat(started_at.strip())
        except ValueError:
            pass
    return datetime.fromtimestamp(path.stat().st_mtime)
