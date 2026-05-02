"""compare 节点：跑一个 CompareTask，可注入 SQL / key 覆盖。"""
from __future__ import annotations

from typing import Any


def run_compare_node(config: dict[str, Any], variables: dict[str, str], **_: Any) -> dict[str, Any]:
    """Run a CompareTask by id and return its CompareResult.

    Optional config overrides (already variable-interpolated by the engine):
      - source_sql_override / target_sql_override: replace the task's SQL
      - key_columns_override: replace the task's key columns

    These let one CompareTask be reused across runs that pass different
    parameter values via ${var} substitution in the override SQL.
    """
    from app.services.repositories import task_store
    from app.services.runner import run_task

    task_id = str(config.get("task_id") or "").strip()
    if not task_id:
        raise ValueError("compare node requires config.task_id")

    src_override = config.get("source_sql_override")
    tgt_override = config.get("target_sql_override")
    keys_override = config.get("key_columns_override")
    if src_override or tgt_override or keys_override:
        task = task_store.get(task_id)
        if task is None:
            raise ValueError(f"compare node: task {task_id!r} not found")
        update: dict[str, Any] = {}
        if isinstance(src_override, str) and src_override.strip():
            update["source_sql"] = src_override
        if isinstance(tgt_override, str) and tgt_override.strip():
            update["target_sql"] = tgt_override
        if isinstance(keys_override, list) and keys_override:
            update["key_columns"] = [str(k) for k in keys_override]
        if update:
            patched = task.model_copy(update=update)
            # Persist in-memory only (don't pollute the saved task).
            # task_store reads from disk on each call, so we rebuild for this run.
            from app.compare.engine import compare_rows, compare_sorted_row_iterators  # noqa: F401  (ensures runner sees same engine)
            return _run_task_with_override(task_id, patched).model_dump(mode="json")

    result = run_task(task_id)
    return result.model_dump(mode="json")


def _run_task_with_override(task_id: str, patched_task) -> Any:
    """Run a CompareTask using `patched_task` instead of looking up by id.
    Mirrors services.runner.run_task but skips the store lookup."""
    from app.services.runner import run_task
    # Patch the store lookup just for this call. Cleanest: monkey-patch
    # task_store.get to return the patched task while running.
    from app.services.repositories import task_store
    original_get = task_store.get
    def patched_get(tid: str):
        if tid == task_id:
            return patched_task
        return original_get(tid)
    task_store.get = patched_get   # type: ignore[assignment]
    try:
        return run_task(task_id)
    finally:
        task_store.get = original_get   # type: ignore[assignment]
