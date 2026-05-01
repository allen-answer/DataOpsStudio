"""Workflow engine — Phase 3 first slice.

Executes the nodes of a Workflow in order. Each node's config is recursively
interpolated against `${var}` placeholders before the node runs. Failure of
any node aborts the run; later nodes are marked SKIPPED.

This first slice intentionally keeps the model linear (no DAG topo-sort, no
fan-out, no conditional branches). Those land in later slices once the
storage shape and runner contract have shaken out.
"""
from __future__ import annotations

import logging
import re
import time
import uuid
from datetime import date, datetime
from typing import Any, Mapping

from app.models import (
    NodeRunStatus,
    Workflow,
    WorkflowNode,
    WorkflowNodeRun,
    WorkflowRun,
    WorkflowRunStatus,
)
from app.services.workflow_nodes import NODE_RUNNERS, NodeRunner


logger = logging.getLogger(__name__)
_VARIABLE_PATTERN = re.compile(r"\$\{([^}]+)\}")


def run_workflow(
    workflow: Workflow,
    variables: Mapping[str, str] | None = None,
    runners: Mapping[Any, NodeRunner] | None = None,
) -> WorkflowRun:
    """Execute `workflow` end-to-end. Caller-supplied `variables` override
    `workflow.default_variables`. `runners` is a test seam — pass a custom
    registry to substitute fakes; default is `NODE_RUNNERS`."""
    runners = NODE_RUNNERS if runners is None else runners
    resolved_vars = {**_default_variables(), **workflow.default_variables, **(variables or {})}
    started = datetime.now()
    start_perf = time.perf_counter()
    run = WorkflowRun(
        run_id=uuid.uuid4().hex,
        workflow_id=workflow.id,
        workflow_name=workflow.name,
        status=WorkflowRunStatus.RUNNING,
        variables=resolved_vars,
        nodes=[_pending_node_run(node) for node in workflow.nodes],
        started_at=started.isoformat(timespec="seconds"),
    )

    logger.info("workflow start id=%s name=%s nodes=%d", workflow.id, workflow.name, len(workflow.nodes))

    aborted = False
    for index, node in enumerate(workflow.nodes):
        node_run = run.nodes[index]
        if aborted:
            node_run.status = NodeRunStatus.SKIPPED
            continue
        runner = runners.get(node.type)
        if runner is None:
            node_run.status = NodeRunStatus.FAILED
            node_run.error = f"no runner registered for node type {node.type}"
            aborted = True
            continue
        try:
            resolved_config = _interpolate(node.config, resolved_vars)
        except KeyError as exc:
            node_run.status = NodeRunStatus.FAILED
            node_run.error = f"unresolved variable: {exc.args[0]}"
            aborted = True
            continue

        node_started = datetime.now()
        node_run.status = NodeRunStatus.RUNNING
        node_run.started_at = node_started.isoformat(timespec="seconds")
        node_perf = time.perf_counter()
        try:
            output = runner(resolved_config, dict(resolved_vars))
            node_run.output = output if isinstance(output, dict) else {"value": output}
            node_run.status = NodeRunStatus.SUCCESS
        except Exception as exc:
            node_run.status = NodeRunStatus.FAILED
            node_run.error = f"{type(exc).__name__}: {exc}"
            aborted = True
            logger.exception("workflow node failed node_id=%s type=%s", node.id, node.type)
        finally:
            node_run.finished_at = datetime.now().isoformat(timespec="seconds")
            node_run.elapsed_seconds = round(time.perf_counter() - node_perf, 3)

    run.finished_at = datetime.now().isoformat(timespec="seconds")
    run.elapsed_seconds = round(time.perf_counter() - start_perf, 3)
    if aborted:
        run.status = WorkflowRunStatus.FAILED
        first_failed = next((n for n in run.nodes if n.status == NodeRunStatus.FAILED), None)
        run.error = first_failed.error if first_failed else "workflow aborted"
    else:
        run.status = WorkflowRunStatus.SUCCESS

    logger.info(
        "workflow finish id=%s status=%s elapsed=%.3fs",
        workflow.id,
        run.status.value,
        run.elapsed_seconds,
    )
    return run


def _pending_node_run(node: WorkflowNode) -> WorkflowNodeRun:
    return WorkflowNodeRun(node_id=node.id, type=node.type, name=node.name)


def _default_variables() -> dict[str, str]:
    """Built-in variables every workflow can reference. `today` and
    `now` are convenient enough that hardcoding them beats forcing the
    caller to compute them every run."""
    today = date.today()
    now = datetime.now()
    return {
        "today": today.isoformat(),
        "now": now.isoformat(timespec="seconds"),
        "year": str(today.year),
        "month": f"{today.month:02d}",
        "day": f"{today.day:02d}",
    }


def _interpolate(value: Any, variables: Mapping[str, str]) -> Any:
    """Recursively walk dict/list/str values, replacing ${var} placeholders.
    Other types pass through. Missing variables raise KeyError(var_name)."""
    if isinstance(value, str):
        return _interpolate_string(value, variables)
    if isinstance(value, dict):
        return {key: _interpolate(item, variables) for key, item in value.items()}
    if isinstance(value, list):
        return [_interpolate(item, variables) for item in value]
    return value


def _interpolate_string(value: str, variables: Mapping[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1).strip()
        if name not in variables:
            raise KeyError(name)
        return str(variables[name])

    return _VARIABLE_PATTERN.sub(replace, value)
