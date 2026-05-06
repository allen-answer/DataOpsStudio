"""Workflow engine — Phase 3.

Executes a Workflow as a DAG. Nodes declare upstream dependencies via
`depends_on`. Topological order is deterministic: when multiple nodes are
ready, the one with the smallest array index runs first.

Failure semantics: a node whose upstream is FAILED or SKIPPED is itself
SKIPPED (transitively). Unrelated branches continue. The whole run is
FAILED if any node ended FAILED, or if cancel was requested.

Output references: any string in a node's config can interpolate
- `${variable_name}` — workflow / runtime variable
- `${nodes.<id>.<dot.path>}` — output of an already-completed node

Execution remains sequential within a single thread; parallel branches
land in a later slice once the DAG shape and tests have settled.
"""
from __future__ import annotations

import ast
import bisect
import logging
import re
import time
import uuid
from datetime import date, datetime
from typing import Any, Callable, Mapping

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


def _transitive_descendants(nodes: list[WorkflowNode], start_id: str) -> set[str]:
    """Set of node ids consisting of `start_id` plus every node that
    transitively depends on it. Used by partial rerun to decide which
    nodes must re-execute."""
    children: dict[str, list[str]] = {node.id: [] for node in nodes}
    for node in nodes:
        for dep in node.depends_on:
            if dep in children:
                children[dep].append(node.id)
    result: set[str] = set()
    stack = [start_id]
    while stack:
        cur = stack.pop()
        if cur in result:
            continue
        result.add(cur)
        stack.extend(children.get(cur, []))
    return result


def transitive_ancestors(nodes: list[WorkflowNode], start_id: str) -> set[str]:
    """Set of node ids whose successful completion is required for
    `start_id` to have valid inputs. Used by the rerun API to validate
    that all ancestors previously succeeded."""
    by_id = {node.id: node for node in nodes}
    result: set[str] = set()
    stack = [start_id]
    while stack:
        cur = stack.pop()
        node = by_id.get(cur)
        if node is None:
            continue
        for dep in node.depends_on:
            if dep not in result:
                result.add(dep)
                stack.append(dep)
    return result


def topological_order(nodes: list[WorkflowNode]) -> list[int]:
    """Return indices into `nodes` in execution order.

    Raises ValueError on cycles, self-references, or depends_on entries that
    point to nodes that don't exist in the workflow."""
    n = len(nodes)
    id_to_index = {node.id: idx for idx, node in enumerate(nodes)}
    if len(id_to_index) != n:
        # Find the duplicate for a clearer error
        seen = set()
        for node in nodes:
            if node.id in seen:
                raise ValueError(f"duplicate node id: {node.id!r}")
            seen.add(node.id)

    in_degree = [0] * n
    children: list[list[int]] = [[] for _ in range(n)]
    for index, node in enumerate(nodes):
        for dep_id in node.depends_on:
            if dep_id == node.id:
                raise ValueError(f"node {node.id!r} cannot depend on itself")
            if dep_id not in id_to_index:
                raise ValueError(f"node {node.id!r}: depends_on references unknown node {dep_id!r}")
            parent = id_to_index[dep_id]
            children[parent].append(index)
            in_degree[index] += 1

    ready = sorted([i for i in range(n) if in_degree[i] == 0])
    order: list[int] = []
    while ready:
        idx = ready.pop(0)
        order.append(idx)
        for child in children[idx]:
            in_degree[child] -= 1
            if in_degree[child] == 0:
                bisect.insort(ready, child)

    if len(order) != n:
        unresolved = [nodes[i].id for i in range(n) if i not in set(order)]
        raise ValueError(f"workflow has a cycle involving nodes: {unresolved}")
    return order


def run_workflow(
    workflow: Workflow,
    variables: Mapping[str, str] | None = None,
    runners: Mapping[Any, NodeRunner] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    resume_from: WorkflowRun | None = None,
    from_node_id: str | None = None,
) -> WorkflowRun:
    """Execute `workflow` as a DAG. See module docstring for semantics.

    `runners` is a test seam — pass a custom registry to substitute fakes;
    default is the production NODE_RUNNERS. `cancel_check` is polled before
    each node; returning True aborts pending nodes.

    Partial rerun: pass `resume_from` (a previous WorkflowRun) and
    `from_node_id` to re-execute that node + its transitive descendants.
    All其他 nodes inherit the previous run's outputs (status=success,
    reused=True). Caller MUST guarantee every transitive ancestor of
    `from_node_id` was previously SUCCESS — otherwise we'd execute
    downstream nodes against stale or missing inputs.
    """
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

    try:
        execution_order = topological_order(workflow.nodes)
    except ValueError as exc:
        run.status = WorkflowRunStatus.FAILED
        run.error = f"invalid DAG: {exc}"
        run.finished_at = datetime.now().isoformat(timespec="seconds")
        run.elapsed_seconds = round(time.perf_counter() - start_perf, 3)
        for node_run in run.nodes:
            node_run.status = NodeRunStatus.SKIPPED
        logger.warning("workflow %s rejected: %s", workflow.id, exc)
        return run

    # Partial rerun: figure out which nodes execute vs. reuse.
    rerun_targets: set[str] | None = None
    previous_node_runs: dict[str, WorkflowNodeRun] = {}
    if resume_from is not None and from_node_id is not None:
        node_ids = {node.id for node in workflow.nodes}
        if from_node_id not in node_ids:
            run.status = WorkflowRunStatus.FAILED
            run.error = f"from_node_id {from_node_id!r} not in workflow"
            run.finished_at = datetime.now().isoformat(timespec="seconds")
            run.elapsed_seconds = round(time.perf_counter() - start_perf, 3)
            for node_run in run.nodes:
                node_run.status = NodeRunStatus.SKIPPED
            return run
        rerun_targets = _transitive_descendants(workflow.nodes, from_node_id)
        previous_node_runs = {node_run.node_id: node_run for node_run in resume_from.nodes}
        run.resumed_from = {"run_id": resume_from.run_id, "from_node_id": from_node_id}
        logger.info(
            "workflow rerun id=%s previous_run=%s from_node=%s rerun_targets=%d",
            workflow.id, resume_from.run_id, from_node_id, len(rerun_targets),
        )

    logger.info("workflow start id=%s name=%s nodes=%d", workflow.id, workflow.name, len(workflow.nodes))

    blocked_ids: set[str] = set()  # nodes whose upstream failed or was cancelled
    completed_outputs: dict[str, dict[str, Any]] = {}  # for ${nodes.<id>.<path>} lookups
    cancelled = False

    for index in execution_order:
        node = workflow.nodes[index]
        node_run = run.nodes[index]

        # Partial rerun: nodes outside rerun_targets reuse the previous run's
        # output. Their depends_on still resolved (from previous_node_runs);
        # we copy timing / status / output verbatim and continue.
        if rerun_targets is not None and node.id not in rerun_targets:
            previous = previous_node_runs.get(node.id)
            if previous is None or previous.status != NodeRunStatus.SUCCESS:
                node_run.status = NodeRunStatus.FAILED
                node_run.error = (
                    f"cannot reuse previous output for node {node.id!r}: "
                    f"previous status was {previous.status.value if previous else 'missing'}"
                )
                blocked_ids.add(node.id)
                continue
            node_run.status = NodeRunStatus.SUCCESS
            node_run.output = dict(previous.output or {})
            node_run.started_at = previous.started_at
            node_run.finished_at = previous.finished_at
            node_run.elapsed_seconds = previous.elapsed_seconds
            node_run.reused = True
            completed_outputs[node.id] = node_run.output
            # 同 normal-run 一样，params 节点把结果合并回 variable scope，
            # 让下游 ${biz_date} / ${sec_code | sql_in} 直接引用上次的解析值。
            if str(getattr(node.type, "value", node.type)) == "params":
                _merge_params_output(node_run.output, resolved_vars)
            continue

        # Transitive skip: any failed/skipped upstream blocks this node.
        if any(dep in blocked_ids for dep in node.depends_on):
            node_run.status = NodeRunStatus.SKIPPED
            blocked_ids.add(node.id)
            continue

        if cancel_check is not None and cancel_check():
            cancelled = True
            node_run.status = NodeRunStatus.SKIPPED
            blocked_ids.add(node.id)
            continue

        runner = runners.get(node.type)
        if runner is None:
            node_run.status = NodeRunStatus.FAILED
            node_run.error = f"no runner registered for node type {node.type}"
            blocked_ids.add(node.id)
            continue

        # Evaluate `when:` (if any) BEFORE config interpolation. A False
        # `when` skips the node — but does NOT block dependents (this is
        # the "conditionally optional" pattern from GHA). We track skip
        # via a separate set so dependents see status=SKIPPED but don't
        # auto-block their downstream the way a failed parent would.
        if node.when:
            try:
                should_run = evaluate_when(node.when, resolved_vars, completed_outputs)
            except (KeyError, ValueError) as exc:
                node_run.status = NodeRunStatus.FAILED
                node_run.error = f"`when` evaluation failed: {exc}"
                blocked_ids.add(node.id)
                continue
            if not should_run:
                node_run.status = NodeRunStatus.SKIPPED
                node_run.error = ""  # not an error — intentional skip
                # NOTE: not added to blocked_ids — downstream nodes may still
                # run (with empty output for this skipped node, which they
                # can detect via missing keys). If you want skip-cascade,
                # add a `depends_on` from the downstream node to this one.
                continue

        try:
            resolved_config = _interpolate(node.config, resolved_vars, completed_outputs)
        except KeyError as exc:
            node_run.status = NodeRunStatus.FAILED
            node_run.error = f"unresolved variable: {exc.args[0]}"
            blocked_ids.add(node.id)
            continue

        node_started = datetime.now()
        node_run.status = NodeRunStatus.RUNNING
        node_run.started_at = node_started.isoformat(timespec="seconds")
        node_perf = time.perf_counter()
        try:
            # outputs / depends_on / run_id 提供给需要它们的 runner（如 excel_export）。
            # 大多数 runner 用 **_ 吃掉这些 kwargs。
            output = runner(
                resolved_config,
                dict(resolved_vars),
                outputs=completed_outputs,
                depends_on=list(node.depends_on or []),
                run_id=run.run_id,
            )
            node_run.output = output if isinstance(output, dict) else {"value": output}
            # runner 不知道自己 node_id，统一在引擎层补 artifacts.node_id。
            # 同时兜底 run_id（避免 runner 拿不到 run_id 时留空）。
            artifacts_out = node_run.output.get("artifacts")
            if isinstance(artifacts_out, list):
                for art in artifacts_out:
                    if isinstance(art, dict):
                        art["node_id"] = node.id
                        if not art.get("run_id"):
                            art["run_id"] = run.run_id
            node_run.status = NodeRunStatus.SUCCESS
            completed_outputs[node.id] = node_run.output
            # `params` nodes feed their resolved values back into the
            # workflow-level variable scope, so downstream nodes can use
            # ${biz_date} directly instead of ${nodes.params.biz_date}.
            # list/dict 也回写（保留原类型）—— 让 ${ids | sql_in} 这种 IN 子句
            # 渲染能直接吃到 list；以前只接标量，list 被静默丢导致下游 unresolved。
            if str(getattr(node.type, "value", node.type)) == "params":
                _merge_params_output(node_run.output, resolved_vars)
        except Exception as exc:
            node_run.status = NodeRunStatus.FAILED
            node_run.error = f"{type(exc).__name__}: {exc}"
            blocked_ids.add(node.id)
            logger.exception("workflow node failed node_id=%s type=%s", node.id, node.type)
        finally:
            node_run.finished_at = datetime.now().isoformat(timespec="seconds")
            node_run.elapsed_seconds = round(time.perf_counter() - node_perf, 3)

    run.finished_at = datetime.now().isoformat(timespec="seconds")
    run.elapsed_seconds = round(time.perf_counter() - start_perf, 3)
    if cancelled:
        run.status = WorkflowRunStatus.FAILED
        run.error = "cancelled"
    elif any(node_run.status == NodeRunStatus.FAILED for node_run in run.nodes):
        run.status = WorkflowRunStatus.FAILED
        first_failed = next(node_run for node_run in run.nodes if node_run.status == NodeRunStatus.FAILED)
        run.error = first_failed.error or "workflow failed"
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


def _merge_params_output(output: Mapping[str, Any], resolved_vars: dict[str, Any]) -> None:
    """Merge a `params` node's output back into the workflow-level variable scope.

    标量 (str/int/float/bool) 转 str（保留旧行为：模板插值时 None→"None" 这类
    退化路径已经在用 str 了）；list/dict 保留原类型，让 ${ids | sql_in} /
    ${row.col} 这种 typed lookup 能拿到真实值而不是 repr 字符串。其他类型
    （None / 自定义对象）跳过 —— 落 variable scope 没意义还可能让 SQL 渲染
    成 `None` 字面量。"""
    for key, value in output.items():
        if isinstance(value, (list, dict)):
            resolved_vars[key] = value
        elif isinstance(value, (str, int, float, bool)):
            resolved_vars[key] = str(value)


def _interpolate(value: Any, variables: Mapping[str, str], outputs: Mapping[str, Mapping[str, Any]]) -> Any:
    """Recursively walk dict/list/str values, replacing ${...} placeholders.
    Other types pass through. Raises KeyError(name) when a placeholder can't
    be resolved."""
    if isinstance(value, str):
        return _interpolate_string(value, variables, outputs)
    if isinstance(value, dict):
        return {key: _interpolate(item, variables, outputs) for key, item in value.items()}
    if isinstance(value, list):
        return [_interpolate(item, variables, outputs) for item in value]
    return value


def _interpolate_string(value: str, variables: Mapping[str, str], outputs: Mapping[str, Mapping[str, Any]]) -> str:
    def replace(match: re.Match[str]) -> str:
        body = match.group(1).strip()
        # `${name | filter}` syntax — split on the first `|` only so a
        # filter argument that itself contains a `|` (none today, but
        # future-proof) doesn't get over-split.
        if "|" in body:
            parts = [s.strip() for s in body.split("|")]
            ref, filters = parts[0], parts[1:]
            resolved = _resolve_placeholder_value(ref, variables, outputs)
            for f in filters:
                resolved = _apply_filter(f, resolved)
            return str(resolved)
        return _resolve_placeholder(body, variables, outputs)

    return _VARIABLE_PATTERN.sub(replace, value)


def _resolve_placeholder(
    name: str,
    variables: Mapping[str, str],
    outputs: Mapping[str, Mapping[str, Any]],
) -> str:
    """String form of `_resolve_placeholder_value`. Used for config interpolation."""
    return str(_resolve_placeholder_value(name, variables, outputs))


def _apply_filter(name: str, value: Any) -> Any:
    """Apply a `${var | filter}` filter to a resolved value.

    Filters keep the placeholder result SQL-safe / format-shape-correct so
    multi-value or sql_result parameters can be used in real SQL clauses.
    """
    if name == "sql_in":
        return _format_sql_in(value)
    if name == "csv":
        return _format_csv(value)
    raise ValueError(f"unknown placeholder filter: {name!r}")


def _format_sql_in(value: Any) -> str:
    """Render a value as the body of a SQL IN(...) clause.
    Numbers stay raw, strings get single-quoted (with `'` escaped to `''`),
    None becomes NULL. Empty list → NULL so the query stays syntactically
    valid (matches no rows) instead of producing `IN ()`."""
    items = value if isinstance(value, (list, tuple)) else [value]
    if not items:
        return "NULL"
    rendered: list[str] = []
    for item in items:
        if item is None:
            rendered.append("NULL")
        elif isinstance(item, bool):
            rendered.append("1" if item else "0")
        elif isinstance(item, (int, float)):
            rendered.append(repr(item) if isinstance(item, float) else str(item))
        else:
            escaped = str(item).replace("'", "''")
            rendered.append(f"'{escaped}'")
    return ", ".join(rendered)


def _format_csv(value: Any) -> str:
    """Render a list as a comma-separated string. No quoting — assumes
    caller knows their context (typically internal IDs / numeric tokens)."""
    items = value if isinstance(value, (list, tuple)) else [value]
    return ", ".join("" if v is None else str(v) for v in items)


def _resolve_placeholder_value(
    name: str,
    variables: Mapping[str, str],
    outputs: Mapping[str, Mapping[str, Any]],
) -> Any:
    """Resolve a placeholder to its typed Python value (preserves int/bool/list/etc).

    `nodes.<id>.<dot.path>` walks completed node outputs (Airflow XCom /
    GitHub Actions `outputs.<step>.<key>` style). Anything else is a plain
    variable lookup."""
    if name.startswith("nodes."):
        parts = name[len("nodes."):].split(".")
        if len(parts) < 2:
            raise KeyError(f"nodes reference must be nodes.<id>.<key>: {name}")
        node_id, *path = parts
        if node_id not in outputs:
            raise KeyError(f"node output not available (not yet run?): nodes.{node_id}")
        cursor: Any = outputs[node_id]
        for key in path:
            if isinstance(cursor, dict) and key in cursor:
                cursor = cursor[key]
            elif isinstance(cursor, list):
                try:
                    cursor = cursor[int(key)]
                except (ValueError, IndexError) as exc:
                    raise KeyError(f"index {key!r} not valid for list at {name}") from exc
            else:
                raise KeyError(f"key {key!r} not found in {name}")
        return cursor
    if name not in variables:
        raise KeyError(name)
    return variables[name]


# --- when: <expression> evaluator ---------------------------------------
#
# Tiny safe expression language for conditional execution. Steps:
#   1. Replace ${...} placeholders with their typed values rendered as Python
#      literals (numbers stay numeric, strings get quoted, booleans → True/False).
#   2. Pre-process JS-style keywords: null→None, true→True, false→False, &&→and, ||→or, !→not.
#   3. Parse with ast.parse(mode='eval').
#   4. Walk the AST and reject any node type not in the allowlist.
#   5. compile + eval with empty globals/locals.
#
# The allowlist enforces NO function calls, NO attribute access, NO subscripting,
# NO comprehensions, NO assignments. Only literals, comparisons, and boolean ops.

_WHEN_ALLOWED_AST_NODES: set[type[ast.AST]] = {
    ast.Expression, ast.BoolOp, ast.UnaryOp, ast.Compare,
    ast.Constant, ast.Load,
    ast.And, ast.Or, ast.Not,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
    ast.USub, ast.UAdd,
}


def _interpolate_for_expression(
    text: str,
    variables: Mapping[str, str],
    outputs: Mapping[str, Mapping[str, Any]],
) -> str:
    """Render ${...} placeholders inline as Python literals so the resulting
    string parses cleanly under restricted ast.parse."""
    def replace(match: re.Match[str]) -> str:
        value = _resolve_placeholder_value(match.group(1).strip(), variables, outputs)
        if value is None:
            return "None"
        if isinstance(value, bool):
            return "True" if value else "False"
        if isinstance(value, (int, float)):
            return repr(value)
        # Strings, lists, dicts — repr quoting is safe under our AST allowlist
        # which rejects subscript / attribute access anyway.
        return repr(value if isinstance(value, str) else str(value))

    return _VARIABLE_PATTERN.sub(replace, text)


def validate_when_syntax(expression: str) -> None:
    """Static syntax check for `when:` expressions.

    Used at save time so坏表达式（空 LHS、未闭合括号等）早点拒掉。
    Replaces ${...} placeholders with a neutral identifier — runtime values
    aren't known yet, but token shape is enough to catch syntax errors.

    Empty / whitespace is fine (means "always run").
    """
    if not expression or not expression.strip():
        return
    # 用整数字面量 1 替占位 —— 是 ast.Constant（在 allowlist 内），
    # 又能在 ==、>、< 等比较里语法上成立。runtime 才会用真实值评估。
    test_expr = _VARIABLE_PATTERN.sub("1", expression)
    test_expr = test_expr.replace("&&", " and ").replace("||", " or ")
    test_expr = re.sub(r"(?<![=!<>])!(?!=)", " not ", test_expr)
    test_expr = re.sub(r"\bnull\b", "None", test_expr)
    test_expr = re.sub(r"\btrue\b", "True", test_expr)
    test_expr = re.sub(r"\bfalse\b", "False", test_expr)
    try:
        tree = ast.parse(test_expr, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"`when` 语法错误：{exc.msg}") from exc
    for node in ast.walk(tree):
        if type(node) not in _WHEN_ALLOWED_AST_NODES:
            raise ValueError(
                f"`when` 不允许的构造：{type(node).__name__}"
            )


def evaluate_when(
    expression: str,
    variables: Mapping[str, str],
    outputs: Mapping[str, Mapping[str, Any]],
) -> bool:
    """Evaluate a `when:` expression. Empty / whitespace = True (always run)."""
    if not expression or not expression.strip():
        return True

    rendered = _interpolate_for_expression(expression, variables, outputs)
    # GitHub Actions / JS-style sugar mapped to Python equivalents.
    rendered = (
        rendered
        .replace("&&", " and ")
        .replace("||", " or ")
    )
    # ! → not, but only when not part of != ; be conservative — only at token boundaries.
    rendered = re.sub(r"(?<![=!<>])!(?!=)", " not ", rendered)
    rendered = re.sub(r"\bnull\b", "None", rendered)
    rendered = re.sub(r"\btrue\b", "True", rendered)
    rendered = re.sub(r"\bfalse\b", "False", rendered)

    try:
        tree = ast.parse(rendered, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"`when` expression is not valid: {expression!r} → {exc.msg}") from exc

    for node in ast.walk(tree):
        if type(node) not in _WHEN_ALLOWED_AST_NODES:
            raise ValueError(
                f"`when` expression rejected: disallowed construct {type(node).__name__} in {expression!r}"
            )

    return bool(eval(compile(tree, "<when>", "eval"), {"__builtins__": {}}, {}))
