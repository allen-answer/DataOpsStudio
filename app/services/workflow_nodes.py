"""Workflow node runners. A runner takes the resolved (variable-interpolated)
node config plus the live variables dict and returns a JSON-serializable output.

To register a new node type:
    1. Add a value to WorkflowNodeType in app/models.py
    2. Implement a runner function here with signature (config, variables) -> dict
    3. Register it in NODE_RUNNERS below
"""
from __future__ import annotations

from typing import Any, Callable

from app.models import WorkflowNodeType


NodeRunner = Callable[[dict[str, Any], dict[str, str]], dict[str, Any]]


def run_compare_node(config: dict[str, Any], variables: dict[str, str]) -> dict[str, Any]:
    """Run an existing CompareTask by id and return its CompareResult."""
    # Imported lazily so unit tests for the engine don't drag the whole
    # compare runtime (DB drivers, exporter, etc.) into the import graph.
    from app.services.runner import run_task

    task_id = str(config.get("task_id") or "").strip()
    if not task_id:
        raise ValueError("compare node requires config.task_id")
    result = run_task(task_id)
    return result.model_dump(mode="json")


NODE_RUNNERS: dict[WorkflowNodeType, NodeRunner] = {
    WorkflowNodeType.COMPARE: run_compare_node,
}
