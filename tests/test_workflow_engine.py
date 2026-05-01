"""Tests for app.services.workflow_engine — Phase 3 first slice.

Covers variable interpolation, sequential execution, abort-on-failure, and
the runners-injection seam. Real node runners (compare, etc.) are tested
through their own integration paths; here we use stub runners so the engine
under test stays the only thing on the hot path.
"""
from __future__ import annotations

import pytest

from app.models import (
    NodeRunStatus,
    Workflow,
    WorkflowNode,
    WorkflowNodeType,
    WorkflowRunStatus,
)
from app.services.workflow_engine import run_workflow


def _wf(nodes: list[WorkflowNode], default_variables: dict[str, str] | None = None) -> Workflow:
    return Workflow(
        id="wf1",
        name="test",
        nodes=nodes,
        default_variables=default_variables or {},
    )


def test_runs_single_node_to_success():
    captured: dict = {}

    def fake_runner(config, variables):
        captured["config"] = config
        captured["variables"] = dict(variables)
        return {"echoed": config}

    workflow = _wf([WorkflowNode(id="n1", type=WorkflowNodeType.COMPARE, name="echo", config={"k": "v"})])
    run = run_workflow(workflow, runners={WorkflowNodeType.COMPARE: fake_runner})

    assert run.status == WorkflowRunStatus.SUCCESS
    assert len(run.nodes) == 1
    assert run.nodes[0].status == NodeRunStatus.SUCCESS
    assert run.nodes[0].output == {"echoed": {"k": "v"}}
    assert captured["config"] == {"k": "v"}


def test_variable_interpolation_in_string_values():
    received: dict = {}

    def runner(config, variables):
        received["config"] = config
        return {}

    workflow = _wf(
        [WorkflowNode(id="n1", type=WorkflowNodeType.COMPARE, config={"date": "${biz_date}", "label": "run-${biz_date}"})],
        default_variables={"biz_date": "2026-01-01"},
    )
    run_workflow(workflow, runners={WorkflowNodeType.COMPARE: runner})

    assert received["config"] == {"date": "2026-01-01", "label": "run-2026-01-01"}


def test_caller_variables_override_default_variables():
    received: dict = {}

    def runner(config, variables):
        received["config"] = config
        return {}

    workflow = _wf(
        [WorkflowNode(id="n1", type=WorkflowNodeType.COMPARE, config={"date": "${biz_date}"})],
        default_variables={"biz_date": "2026-01-01"},
    )
    run_workflow(workflow, variables={"biz_date": "2026-12-31"}, runners={WorkflowNodeType.COMPARE: runner})

    assert received["config"]["date"] == "2026-12-31"


def test_built_in_today_variable_available():
    received: dict = {}

    def runner(config, variables):
        received["config"] = config
        return {}

    workflow = _wf([WorkflowNode(id="n1", type=WorkflowNodeType.COMPARE, config={"d": "${today}"})])
    run_workflow(workflow, runners={WorkflowNodeType.COMPARE: runner})

    # Don't pin the date — just confirm it resolved to YYYY-MM-DD shape.
    assert isinstance(received["config"]["d"], str)
    assert len(received["config"]["d"]) == 10
    assert received["config"]["d"][4] == "-"


def test_unresolved_variable_fails_node_and_aborts():
    workflow = _wf([
        WorkflowNode(id="n1", type=WorkflowNodeType.COMPARE, config={"x": "${nope}"}),
        WorkflowNode(id="n2", type=WorkflowNodeType.COMPARE, config={"y": "ok"}),
    ])

    def runner(config, variables):
        return {}

    run = run_workflow(workflow, runners={WorkflowNodeType.COMPARE: runner})

    assert run.status == WorkflowRunStatus.FAILED
    assert run.nodes[0].status == NodeRunStatus.FAILED
    assert "nope" in run.nodes[0].error
    assert run.nodes[1].status == NodeRunStatus.SKIPPED


def test_node_failure_aborts_chain_and_skips_rest():
    calls: list[str] = []

    def runner(config, variables):
        calls.append(config["tag"])
        if config["tag"] == "boom":
            raise RuntimeError("kaboom")
        return {"ok": True}

    workflow = _wf([
        WorkflowNode(id="n1", type=WorkflowNodeType.COMPARE, config={"tag": "first"}),
        WorkflowNode(id="n2", type=WorkflowNodeType.COMPARE, config={"tag": "boom"}),
        WorkflowNode(id="n3", type=WorkflowNodeType.COMPARE, config={"tag": "third"}),
    ])
    run = run_workflow(workflow, runners={WorkflowNodeType.COMPARE: runner})

    assert calls == ["first", "boom"]
    assert run.status == WorkflowRunStatus.FAILED
    assert run.nodes[0].status == NodeRunStatus.SUCCESS
    assert run.nodes[1].status == NodeRunStatus.FAILED
    assert "kaboom" in run.nodes[1].error
    assert run.nodes[2].status == NodeRunStatus.SKIPPED
    assert run.error == run.nodes[1].error


def test_unknown_node_type_aborts_with_clear_error():
    workflow = _wf([WorkflowNode(id="n1", type=WorkflowNodeType.COMPARE, config={})])
    run = run_workflow(workflow, runners={})  # empty registry

    assert run.status == WorkflowRunStatus.FAILED
    assert run.nodes[0].status == NodeRunStatus.FAILED
    assert "no runner" in run.nodes[0].error.lower()


def test_empty_workflow_succeeds_trivially():
    run = run_workflow(_wf([]))
    assert run.status == WorkflowRunStatus.SUCCESS
    assert run.nodes == []


def test_interpolation_walks_nested_dict_and_list():
    received: dict = {}

    def runner(config, variables):
        received["config"] = config
        return {}

    workflow = _wf(
        [WorkflowNode(
            id="n1",
            type=WorkflowNodeType.COMPARE,
            config={
                "params": {"path": "/data/${biz_date}.csv"},
                "tags": ["${biz_date}", "static"],
                "n": 42,  # non-string passes through
            },
        )],
        default_variables={"biz_date": "2026-05-01"},
    )
    run_workflow(workflow, runners={WorkflowNodeType.COMPARE: runner})

    assert received["config"] == {
        "params": {"path": "/data/2026-05-01.csv"},
        "tags": ["2026-05-01", "static"],
        "n": 42,
    }
