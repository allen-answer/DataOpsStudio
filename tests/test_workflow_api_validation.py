"""Tests for workflow CRUD validation in app.api.routes._ensure_workflow_node_targets.

Specifically guarding the SQL override check: a compare node with
`source_sql_override="id=${user_id}"` must be rejected at save time
instead of failing later inside the engine when sql_guard runs.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.routes import _ensure_workflow_node_targets
from app.models import WorkflowCreate
from app.services.repositories import task_store


@pytest.fixture
def stub_task(monkeypatch):
    """Make task_store.get return a non-None marker for any id so the
    `task does not exist` branch doesn't fire ahead of override validation."""
    sentinel = object()
    monkeypatch.setattr(task_store, "get", lambda _tid: sentinel)
    return sentinel


def _payload(node_config: dict) -> WorkflowCreate:
    return WorkflowCreate(
        name="t",
        nodes=[
            {"id": "n1", "type": "compare", "name": "", "config": node_config, "depends_on": [], "when": ""}
        ],
        default_variables={},
    )


def test_accepts_full_select_override(stub_task):
    payload = _payload({
        "task_id": "anything",
        "source_sql_override": "SELECT id FROM users WHERE id = ${user_id}",
        "target_sql_override": "SELECT id FROM users WHERE id = ${user_id}",
    })
    _ensure_workflow_node_targets(payload)   # no exception


def test_accepts_with_override(stub_task):
    payload = _payload({
        "task_id": "anything",
        "source_sql_override": "WITH t AS (SELECT 1) SELECT * FROM t WHERE id = ${user_id}",
    })
    _ensure_workflow_node_targets(payload)


def test_rejects_where_fragment_override(stub_task):
    payload = _payload({
        "task_id": "anything",
        "source_sql_override": "id=${user_id}",
    })
    with pytest.raises(HTTPException) as excinfo:
        _ensure_workflow_node_targets(payload)
    assert excinfo.value.status_code == 400
    assert "source_sql_override" in excinfo.value.detail
    assert "完整的 SELECT/WITH 查询" in excinfo.value.detail


def test_rejects_dml_override(stub_task):
    payload = _payload({
        "task_id": "anything",
        "target_sql_override": "DELETE FROM users WHERE id = ${user_id}",
    })
    with pytest.raises(HTTPException) as excinfo:
        _ensure_workflow_node_targets(payload)
    assert excinfo.value.status_code == 400
    assert "target_sql_override" in excinfo.value.detail


def test_empty_override_is_allowed(stub_task):
    """留空 = 不覆盖，使用任务原始 SQL。空字符串不应触发校验。"""
    payload = _payload({
        "task_id": "anything",
        "source_sql_override": "",
        "target_sql_override": "   ",
    })
    _ensure_workflow_node_targets(payload)


def test_override_with_sql_in_filter_passes(stub_task):
    payload = _payload({
        "task_id": "anything",
        "source_sql_override": "SELECT id FROM users WHERE id IN (${ids | sql_in})",
    })
    _ensure_workflow_node_targets(payload)


def test_rejects_when_with_empty_lhs(stub_task):
    """`when` 表达式空 LHS 在保存时就拒掉，不留到运行时。"""
    payload = WorkflowCreate(
        name="t",
        nodes=[
            {"id": "n1", "type": "compare", "name": "", "config": {"task_id": "x"},
             "depends_on": [], "when": ' == "prod"'}
        ],
        default_variables={},
    )
    with pytest.raises(HTTPException) as excinfo:
        _ensure_workflow_node_targets(payload)
    assert excinfo.value.status_code == 400
    assert "when" in excinfo.value.detail.lower()


def test_accepts_valid_when_expression(stub_task):
    payload = WorkflowCreate(
        name="t",
        nodes=[
            {"id": "n1", "type": "compare", "name": "", "config": {"task_id": "x"},
             "depends_on": [], "when": '${env} == "prod" && ${count} > 0'}
        ],
        default_variables={},
    )
    _ensure_workflow_node_targets(payload)


def test_empty_when_passes(stub_task):
    payload = WorkflowCreate(
        name="t",
        nodes=[
            {"id": "n1", "type": "compare", "name": "", "config": {"task_id": "x"},
             "depends_on": [], "when": ""}
        ],
        default_variables={},
    )
    _ensure_workflow_node_targets(payload)
