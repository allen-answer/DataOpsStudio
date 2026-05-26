"""compare 节点：跑一个 CompareTask，可注入 SQL / key 覆盖。"""
from __future__ import annotations

from typing import Any


def run_compare_node(config: dict[str, Any], variables: dict[str, str], **kwargs: Any) -> dict[str, Any]:
    """Run a CompareTask by id and return its CompareResult.

    Optional config overrides (already variable-interpolated by the engine):
      - source_sql_override / target_sql_override: replace the task's SQL
      - key_columns_override: replace the task's key columns

    These let one CompareTask be reused across runs that pass different
    parameter values via ${var} substitution in the override SQL.

    Wave 3 #13:透传 `workflow_run_id` 和 `owner_user_id`(从 workflow_engine
    传进 kwargs)给 `run_task()`,让 run_index 能反查这条 compare run 是被哪个
    workflow_run 触发的 + 归属哪个用户。绕过 guard 的旧 bug 收口。
    """
    from app.services.repositories import task_store
    from app.services.runner import run_task

    task_id = str(config.get("task_id") or "").strip()
    if not task_id:
        raise ValueError("compare node requires config.task_id")

    workflow_run_id = str(kwargs.get("workflow_run_id") or "")
    owner_user_id = str(kwargs.get("owner_user_id") or "")

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
            from app.compare.engine import compare_rows, compare_sorted_row_iterators  # noqa: F401
            return _run_task_with_override(
                task_id, patched,
                workflow_run_id=workflow_run_id, owner_user_id=owner_user_id,
            ).model_dump(mode="json")

    result = run_task(task_id, workflow_run_id=workflow_run_id, owner_user_id=owner_user_id)
    return result.model_dump(mode="json")


def _run_task_with_override(task_id: str, patched_task, *, workflow_run_id: str = "", owner_user_id: str = "") -> Any:
    """Run a CompareTask using `patched_task` instead of looking up by id.
    Mirrors services.runner.run_task but skips the store lookup."""
    from app.services.runner import run_task
    from app.services.repositories import task_store
    original_get = task_store.get
    def patched_get(tid: str):
        if tid == task_id:
            return patched_task
        return original_get(tid)
    task_store.get = patched_get   # type: ignore[assignment]
    try:
        return run_task(task_id, workflow_run_id=workflow_run_id, owner_user_id=owner_user_id)
    finally:
        task_store.get = original_get   # type: ignore[assignment]
