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
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from app.models import WorkflowRun
from app.utils.paths import WORKFLOW_RUNS_DIR


logger = logging.getLogger(__name__)


# ─── 跨服务共享的 run payload 缓存 ────────────────────────────────────────────
# 多个服务（assets.get_table_columns / assets._build_column_edge_index /
# assets._scan_lineage_scripts_referencing / search._search_lineage_scripts）
# 走同一组 list_workflow_runs(limit=N) + get_workflow_run(rid) 把 JSON 解析。
# 各自扫一遍 = 无谓的重复磁盘 I/O + JSON parse。这层缓存解析后的 payloads
# 给所有 caller 共享。TTL 300s + run-count 失效（粗粒度但足够）。
_PAYLOAD_TTL = 300.0
_run_payloads_cache: dict[int, dict[str, Any]] = {}
_run_payloads_lock = threading.RLock()


def get_cached_run_payloads(run_limit: int) -> list[dict[str, Any]]:
    """返回最近 `run_limit` 个 workflow_run 的完整 payload 列表（newest first）。
    多个聚合可共享 —— 一次 JSON parse 给多个服务用。
    """
    now = time.time()
    with _run_payloads_lock:
        entry = _run_payloads_cache.get(run_limit)
        try:
            current_run_count = len(list_workflow_runs(limit=run_limit))
        except Exception:  # pragma: no cover
            current_run_count = 0
        if entry is not None and (
            now - entry["built_at"] < _PAYLOAD_TTL
            and entry["source_run_count"] == current_run_count
        ):
            return entry["payloads"]
        payloads: list[dict[str, Any]] = []
        for summary in list_workflow_runs(limit=run_limit):
            rid = str(summary.get("run_id") or "")
            if not rid:
                continue
            full = get_workflow_run(rid)
            if full:
                payloads.append(full)
        _run_payloads_cache[run_limit] = {
            "built_at": now,
            "source_run_count": current_run_count,
            "payloads": payloads,
        }
        return payloads


def invalidate_run_payloads_cache() -> None:
    """admin / 测试用：清空所有 run_limit 的 cache 槽。"""
    with _run_payloads_lock:
        _run_payloads_cache.clear()


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
    Pass `workflow_id` to filter, or empty string for all.

    Perf：先按文件 mtime 排序（fs metadata 免读 JSON），再按这个顺序读，
    最后按 `started_at` 二次排序得到精确顺序。读到 `limit*2+10` 个匹配项
    （或 filter 场景的等同 limit）就停 —— 避免「项目跑了几千个 run，每次列
    页都全量解析 JSON」这种线性放大。

    安全 hedge：mtime 跟 `started_at` 在「跑很久才落盘」的长任务上会偏离
    （例如开始时间 10:00、结束 11:00 → mtime=11:00），所以读取量取 2× 留
    缓冲，再按 `started_at` 重排，确保返回的最终顺序和精确语义一致。
    """
    if not WORKFLOW_RUNS_DIR.exists():
        return []
    # 按 mtime DESC 预排序（不读 JSON 内容）
    paths = sorted(
        WORKFLOW_RUNS_DIR.glob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    # 读取预算：2× limit + 10 留 hedge 给 mtime≠started_at 的偏离。filter 场景
    # 同样按 limit 预算（命中数足够就停；命中不足就读完所有）。
    read_budget = max(limit * 2 + 10, limit + 20)
    items: list[dict[str, Any]] = []
    for path in paths:
        if len(items) >= read_budget:
            break
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
