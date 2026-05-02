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

    def fake_runner(config, variables, **_):
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

    def runner(config, variables, **_):
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

    def runner(config, variables, **_):
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

    def runner(config, variables, **_):
        received["config"] = config
        return {}

    workflow = _wf([WorkflowNode(id="n1", type=WorkflowNodeType.COMPARE, config={"d": "${today}"})])
    run_workflow(workflow, runners={WorkflowNodeType.COMPARE: runner})

    # Don't pin the date — just confirm it resolved to YYYY-MM-DD shape.
    assert isinstance(received["config"]["d"], str)
    assert len(received["config"]["d"]) == 10
    assert received["config"]["d"][4] == "-"


def test_unresolved_variable_fails_node_and_skips_dependents():
    workflow = _wf([
        WorkflowNode(id="n1", type=WorkflowNodeType.COMPARE, config={"x": "${nope}"}),
        WorkflowNode(id="n2", type=WorkflowNodeType.COMPARE, config={"y": "ok"}, depends_on=["n1"]),
    ])

    def runner(config, variables, **_):
        return {}

    run = run_workflow(workflow, runners={WorkflowNodeType.COMPARE: runner})

    assert run.status == WorkflowRunStatus.FAILED
    assert run.nodes[0].status == NodeRunStatus.FAILED
    assert "nope" in run.nodes[0].error
    assert run.nodes[1].status == NodeRunStatus.SKIPPED


def test_node_failure_skips_dependent_chain():
    calls: list[str] = []

    def runner(config, variables, **_):
        calls.append(config["tag"])
        if config["tag"] == "boom":
            raise RuntimeError("kaboom")
        return {"ok": True}

    workflow = _wf([
        WorkflowNode(id="n1", type=WorkflowNodeType.COMPARE, config={"tag": "first"}),
        WorkflowNode(id="n2", type=WorkflowNodeType.COMPARE, config={"tag": "boom"}, depends_on=["n1"]),
        WorkflowNode(id="n3", type=WorkflowNodeType.COMPARE, config={"tag": "third"}, depends_on=["n2"]),
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


def test_cancel_check_aborts_before_next_node():
    calls: list[str] = []

    def runner(config, variables, **_):
        calls.append(config["tag"])
        return {}

    workflow = _wf([
        WorkflowNode(id="n1", type=WorkflowNodeType.COMPARE, config={"tag": "first"}),
        WorkflowNode(id="n2", type=WorkflowNodeType.COMPARE, config={"tag": "second"}, depends_on=["n1"]),
        WorkflowNode(id="n3", type=WorkflowNodeType.COMPARE, config={"tag": "third"}, depends_on=["n2"]),
    ])

    # Flip cancellation on after the first node; second/third must be skipped.
    cancelled = {"value": False}
    def cancel_check():
        if calls == ["first"]:
            cancelled["value"] = True
        return cancelled["value"]

    run = run_workflow(
        workflow,
        runners={WorkflowNodeType.COMPARE: runner},
        cancel_check=cancel_check,
    )

    assert calls == ["first"]
    assert run.status == WorkflowRunStatus.FAILED
    assert run.error == "cancelled"
    assert run.nodes[0].status == NodeRunStatus.SUCCESS
    assert run.nodes[1].status == NodeRunStatus.SKIPPED
    assert run.nodes[2].status == NodeRunStatus.SKIPPED


def test_cancel_check_before_first_node_skips_all():
    def runner(config, variables, **_):
        raise AssertionError("should not run")

    workflow = _wf([
        WorkflowNode(id="n1", type=WorkflowNodeType.COMPARE, config={}),
        WorkflowNode(id="n2", type=WorkflowNodeType.COMPARE, config={}),
    ])
    run = run_workflow(workflow, runners={WorkflowNodeType.COMPARE: runner}, cancel_check=lambda: True)

    assert run.status == WorkflowRunStatus.FAILED
    assert run.error == "cancelled"
    assert all(node.status == NodeRunStatus.SKIPPED for node in run.nodes)


# --- DAG semantics ---

def test_topological_order_respects_depends_on():
    from app.services.workflow_engine import topological_order

    nodes = [
        WorkflowNode(id="n3", type=WorkflowNodeType.COMPARE, depends_on=["n1", "n2"]),
        WorkflowNode(id="n1", type=WorkflowNodeType.COMPARE),
        WorkflowNode(id="n2", type=WorkflowNodeType.COMPARE, depends_on=["n1"]),
    ]
    order = topological_order(nodes)
    # n1 (idx 1) must come before n2 (idx 2); n3 (idx 0) must be last.
    assert order.index(1) < order.index(2)
    assert order.index(2) < order.index(0)


def test_topological_order_breaks_ties_by_array_index():
    """When multiple nodes are ready, smaller array index runs first.
    Preserves the linear-array intuition for users not yet using deps."""
    from app.services.workflow_engine import topological_order

    nodes = [
        WorkflowNode(id="a", type=WorkflowNodeType.COMPARE),
        WorkflowNode(id="b", type=WorkflowNodeType.COMPARE),
        WorkflowNode(id="c", type=WorkflowNodeType.COMPARE),
    ]
    assert topological_order(nodes) == [0, 1, 2]


def test_independent_branches_run_after_unrelated_failure():
    """A failure in one branch must not skip an unrelated parallel branch."""
    calls: list[str] = []

    def runner(config, variables, **_):
        tag = config["tag"]
        calls.append(tag)
        if tag == "boom":
            raise RuntimeError("x")
        return {}

    workflow = _wf([
        WorkflowNode(id="bad", type=WorkflowNodeType.COMPARE, config={"tag": "boom"}),
        WorkflowNode(id="bad_child", type=WorkflowNodeType.COMPARE, config={"tag": "bad_child"}, depends_on=["bad"]),
        WorkflowNode(id="ok", type=WorkflowNodeType.COMPARE, config={"tag": "ok"}),
        WorkflowNode(id="ok_child", type=WorkflowNodeType.COMPARE, config={"tag": "ok_child"}, depends_on=["ok"]),
    ])
    run = run_workflow(workflow, runners={WorkflowNodeType.COMPARE: runner})

    assert run.status == WorkflowRunStatus.FAILED  # because `bad` failed
    statuses = {n.node_id: n.status for n in run.nodes}
    assert statuses["bad"] == NodeRunStatus.FAILED
    assert statuses["bad_child"] == NodeRunStatus.SKIPPED
    assert statuses["ok"] == NodeRunStatus.SUCCESS
    assert statuses["ok_child"] == NodeRunStatus.SUCCESS
    assert "boom" in calls and "ok" in calls and "ok_child" in calls


def test_diamond_dag_runs_all_paths():
    calls: list[str] = []

    def runner(config, variables, **_):
        calls.append(config["tag"])
        return {}

    workflow = _wf([
        WorkflowNode(id="root", type=WorkflowNodeType.COMPARE, config={"tag": "root"}),
        WorkflowNode(id="left", type=WorkflowNodeType.COMPARE, config={"tag": "left"}, depends_on=["root"]),
        WorkflowNode(id="right", type=WorkflowNodeType.COMPARE, config={"tag": "right"}, depends_on=["root"]),
        WorkflowNode(id="join", type=WorkflowNodeType.COMPARE, config={"tag": "join"}, depends_on=["left", "right"]),
    ])
    run = run_workflow(workflow, runners={WorkflowNodeType.COMPARE: runner})

    assert run.status == WorkflowRunStatus.SUCCESS
    assert calls[0] == "root"
    assert calls[-1] == "join"
    assert set(calls[1:3]) == {"left", "right"}


def test_node_output_referenced_in_downstream_config():
    seen: dict = {}

    def n1_runner(config, variables, **_):
        return {"summary": {"diff": 7, "label": "alpha"}}

    def n2_runner(config, variables, **_):
        seen["config"] = config
        return {}

    runners = {WorkflowNodeType.COMPARE: n1_runner}

    # Two-node setup where n2 reads n1's output. Use a small dispatch by id.
    def runner(config, variables, **_):
        return n1_runner(config, variables) if config.get("__which") == "n1" else n2_runner(config, variables)

    workflow = _wf([
        WorkflowNode(id="n1", type=WorkflowNodeType.COMPARE, config={"__which": "n1"}),
        WorkflowNode(
            id="n2",
            type=WorkflowNodeType.COMPARE,
            config={"__which": "n2", "diff_count": "${nodes.n1.summary.diff}", "label": "got-${nodes.n1.summary.label}"},
            depends_on=["n1"],
        ),
    ])
    run = run_workflow(workflow, runners={WorkflowNodeType.COMPARE: runner})

    assert run.status == WorkflowRunStatus.SUCCESS
    assert seen["config"]["diff_count"] == "7"
    assert seen["config"]["label"] == "got-alpha"


def test_unknown_dependency_rejected():
    from app.services.workflow_engine import topological_order

    nodes = [
        WorkflowNode(id="n1", type=WorkflowNodeType.COMPARE, depends_on=["ghost"]),
    ]
    with pytest.raises(ValueError, match="ghost"):
        topological_order(nodes)


def test_self_dependency_rejected():
    from app.services.workflow_engine import topological_order

    nodes = [WorkflowNode(id="n1", type=WorkflowNodeType.COMPARE, depends_on=["n1"])]
    with pytest.raises(ValueError, match="itself"):
        topological_order(nodes)


def test_cycle_rejected():
    from app.services.workflow_engine import topological_order

    nodes = [
        WorkflowNode(id="a", type=WorkflowNodeType.COMPARE, depends_on=["b"]),
        WorkflowNode(id="b", type=WorkflowNodeType.COMPARE, depends_on=["a"]),
    ]
    with pytest.raises(ValueError, match="cycle"):
        topological_order(nodes)


def test_invalid_dag_workflow_run_marks_all_skipped():
    """run_workflow must catch DAG validation errors and produce a FAILED
    run rather than crashing — async jobs depend on this for clean errors."""
    workflow = _wf([
        WorkflowNode(id="a", type=WorkflowNodeType.COMPARE, depends_on=["b"]),
        WorkflowNode(id="b", type=WorkflowNodeType.COMPARE, depends_on=["a"]),
    ])
    run = run_workflow(workflow, runners={WorkflowNodeType.COMPARE: lambda c, v: {}})

    assert run.status == WorkflowRunStatus.FAILED
    assert "cycle" in run.error.lower() or "invalid" in run.error.lower()
    assert all(node.status == NodeRunStatus.SKIPPED for node in run.nodes)


def test_params_node_output_merges_into_workflow_variables():
    """A `params` node's output should propagate as workflow-level variables
    so downstream nodes can use ${biz_date} directly, not just ${nodes.x.y}."""
    seen = []

    def runner(config, variables, **_):
        if config.get("__which") == "params":
            return {"biz_date": "2026-05-01", "batch_id": "B42"}
        seen.append(config)
        return {}

    workflow = _wf([
        WorkflowNode(id="params", type=WorkflowNodeType.PARAMS, config={"__which": "params"}),
        WorkflowNode(id="downstream", type=WorkflowNodeType.COMPARE,
                     config={"date_str": "${biz_date}", "tag": "${batch_id}"},
                     depends_on=["params"]),
    ])
    run_workflow(workflow, runners={
        WorkflowNodeType.PARAMS: runner,
        WorkflowNodeType.COMPARE: runner,
    })

    # Downstream saw the params output substituted into ${biz_date} / ${batch_id}.
    assert seen and seen[0]["date_str"] == "2026-05-01"
    assert seen[0]["tag"] == "B42"


# --- placeholder filters ---

def test_sql_in_filter_quotes_strings_and_leaves_numbers_raw():
    from app.services.workflow_engine import _apply_filter

    assert _apply_filter("sql_in", [1, 2, 3]) == "1, 2, 3"
    assert _apply_filter("sql_in", ["a", "b"]) == "'a', 'b'"
    assert _apply_filter("sql_in", [1, "a", None]) == "1, 'a', NULL"


def test_sql_in_filter_escapes_single_quotes():
    """SQL injection guard: a `'` inside a string item must be doubled."""
    from app.services.workflow_engine import _apply_filter
    assert _apply_filter("sql_in", ["O'Brien"]) == "'O''Brien'"


def test_sql_in_filter_empty_list_renders_NULL():
    """Empty IN clause is invalid SQL; NULL keeps the query syntactically
    valid while matching no rows."""
    from app.services.workflow_engine import _apply_filter
    assert _apply_filter("sql_in", []) == "NULL"


def test_sql_in_filter_wraps_scalar_value():
    from app.services.workflow_engine import _apply_filter
    assert _apply_filter("sql_in", 42) == "42"
    assert _apply_filter("sql_in", "hi") == "'hi'"


def test_sql_in_filter_in_template_via_engine():
    """End-to-end: a multi_value parameter substituted into an IN clause."""
    seen = []

    def runner(config, variables, **_):
        seen.append(config["sql"])
        return {}

    workflow = _wf(
        [WorkflowNode(
            id="n1", type=WorkflowNodeType.COMPARE,
            config={"sql": "SELECT * FROM users WHERE id IN (${vip_ids | sql_in})"},
        )],
        default_variables={},
    )
    # `params` node would normally feed `vip_ids` in; here we simulate that
    # by passing it as a runtime variable. Note: the engine only merges
    # scalars into resolved_vars, but variable lookups can still return
    # the original list IF it's stored as-is — verify by going via
    # ${nodes.x.y} instead.
    nodes_outputs = {}

    # Simpler: use a custom upstream node that emits a list output.
    workflow2 = _wf([
        WorkflowNode(id="src", type=WorkflowNodeType.COMPARE, config={"_emit": "list"}),
        WorkflowNode(id="cmp", type=WorkflowNodeType.COMPARE, depends_on=["src"],
                     config={"sql": "SELECT * FROM users WHERE id IN (${nodes.src.ids | sql_in})"}),
    ])
    captured = {}
    def runner2(config, variables, **_):
        if config.get("_emit") == "list":
            return {"ids": [1, 5, 9]}
        captured["sql"] = config["sql"]
        return {}
    run_workflow(workflow2, runners={WorkflowNodeType.COMPARE: runner2})
    assert captured["sql"] == "SELECT * FROM users WHERE id IN (1, 5, 9)"


def test_unknown_filter_raises_clear_error():
    from app.services.workflow_engine import _apply_filter
    with pytest.raises(ValueError, match="unknown.*filter"):
        _apply_filter("nope", [1, 2])


# --- when: conditional execution ---

def test_when_true_runs_node():
    calls = []

    def runner(config, variables, **_):
        calls.append(config.get("tag"))
        return {}

    workflow = _wf([
        WorkflowNode(id="n1", type=WorkflowNodeType.COMPARE, config={"tag": "x"}, when="true"),
    ])
    run = run_workflow(workflow, runners={WorkflowNodeType.COMPARE: runner})
    assert calls == ["x"]
    assert run.nodes[0].status == NodeRunStatus.SUCCESS


def test_when_false_skips_node_without_blocking_unrelated():
    calls = []

    def runner(config, variables, **_):
        calls.append(config.get("tag"))
        return {}

    workflow = _wf([
        WorkflowNode(id="n1", type=WorkflowNodeType.COMPARE, config={"tag": "skip"}, when="false"),
        WorkflowNode(id="n2", type=WorkflowNodeType.COMPARE, config={"tag": "run"}),
    ])
    run = run_workflow(workflow, runners={WorkflowNodeType.COMPARE: runner})
    assert calls == ["run"]
    assert run.nodes[0].status == NodeRunStatus.SKIPPED
    assert run.nodes[1].status == NodeRunStatus.SUCCESS
    assert run.status == WorkflowRunStatus.SUCCESS  # skipped is not failed


def test_when_branches_on_upstream_output():
    """The compare node returns summary.diff. The downstream `notify` only
    runs when diff > 0 — the canonical use case for conditional execution."""
    seen = []

    def runner(config, variables, **_):
        if config.get("kind") == "compare_no_diff":
            return {"summary": {"diff": 0}}
        if config.get("kind") == "compare_with_diff":
            return {"summary": {"diff": 7}}
        seen.append(config.get("tag"))
        return {}

    # No diff → notify skipped
    wf_no = _wf([
        WorkflowNode(id="c", type=WorkflowNodeType.COMPARE, config={"kind": "compare_no_diff"}),
        WorkflowNode(id="notify", type=WorkflowNodeType.COMPARE, config={"tag": "notify"},
                     depends_on=["c"], when="${nodes.c.summary.diff} > 0"),
    ])
    run_workflow(wf_no, runners={WorkflowNodeType.COMPARE: runner})
    assert seen == []

    # With diff → notify runs
    seen.clear()
    wf_yes = _wf([
        WorkflowNode(id="c", type=WorkflowNodeType.COMPARE, config={"kind": "compare_with_diff"}),
        WorkflowNode(id="notify", type=WorkflowNodeType.COMPARE, config={"tag": "notify"},
                     depends_on=["c"], when="${nodes.c.summary.diff} > 0"),
    ])
    run_workflow(wf_yes, runners={WorkflowNodeType.COMPARE: runner})
    assert seen == ["notify"]


def test_when_supports_string_equality_and_logical_ops():
    calls = []

    def runner(config, variables, **_):
        calls.append(config.get("tag"))
        return {}

    workflow = _wf(
        [
            WorkflowNode(id="a", type=WorkflowNodeType.COMPARE, config={"tag": "a"},
                         when="${env} == 'prod' && ${dryrun} == 'false'"),
            WorkflowNode(id="b", type=WorkflowNodeType.COMPARE, config={"tag": "b"},
                         when="${env} == 'staging' || ${env} == 'prod'"),
        ],
        default_variables={"env": "prod", "dryrun": "false"},
    )
    run_workflow(workflow, runners={WorkflowNodeType.COMPARE: runner})
    assert "a" in calls and "b" in calls


def test_when_invalid_expression_fails_node():
    workflow = _wf([
        WorkflowNode(id="n1", type=WorkflowNodeType.COMPARE, config={}, when="this is not valid &&& nonsense"),
    ])
    run = run_workflow(workflow, runners={WorkflowNodeType.COMPARE: lambda c, v: {}})
    assert run.nodes[0].status == NodeRunStatus.FAILED
    assert "when" in run.nodes[0].error.lower()


def test_when_rejects_function_calls_for_safety():
    """`when` must NOT support arbitrary Python — no function calls, no
    attribute access, no imports. Verifies the AST allowlist."""
    from app.services.workflow_engine import evaluate_when

    with pytest.raises(ValueError, match="disallowed"):
        evaluate_when("__import__('os').system('echo')", {}, {})
    with pytest.raises(ValueError, match="disallowed"):
        evaluate_when("(1).bit_length()", {}, {})
    with pytest.raises(ValueError, match="disallowed"):
        evaluate_when("[1,2,3][0]", {}, {})


def test_when_empty_runs_node():
    """Empty `when` (the default) means always run."""
    from app.services.workflow_engine import evaluate_when
    assert evaluate_when("", {}, {}) is True
    assert evaluate_when("   ", {}, {}) is True


def test_interpolation_walks_nested_dict_and_list():
    received: dict = {}

    def runner(config, variables, **_):
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
