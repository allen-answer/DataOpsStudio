"""Phase 11 MVP — services/trace_compare.py + /api/lineage/trace-compare 测试。

覆盖：
- 单跳直传 → 1 个 compare 节点，正确填 task_id / sample_keys SQL / key_columns_override
- 多跳 BFS → 多个 compare 节点，hop 顺序 + 父节点解析
- datasource_map 缺一表 → unmapped_tables + warnings
- per_table_keys 按表覆盖 PK
- 标识符校验拦 SQL injection 形式参数
- depth / sample_keys / project_id 边界
- HTTP endpoint 鉴权（editor+）+ 400 on missing table
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from main import app
from app.models import (
    NodeRunStatus, WorkflowNodeRun, WorkflowNodeType,
    WorkflowRun, WorkflowRunStatus,
)
from app.services import workflow_history
from app.services.trace_compare import trace_compare


def _persist_lineage(insert_mappings: list[dict], run_id: str = "trace-run") -> None:
    run = WorkflowRun(
        run_id=run_id, workflow_id="wf", workflow_name="t",
        status=WorkflowRunStatus.SUCCESS,
        nodes=[WorkflowNodeRun(
            node_id="n1", type=WorkflowNodeType.LINEAGE,
            status=NodeRunStatus.SUCCESS,
            output={"insert_mappings": insert_mappings},
        )],
        started_at="2026-05-05T10:00:00",
        finished_at="2026-05-05T10:00:01", elapsed_seconds=1.0,
    )
    workflow_history.persist_workflow_run(run)


# ─── 服务层 ─────────────────────────────────────────────────────────────────


def test_single_hop_emits_one_compare_node(isolated_storage):
    _persist_lineage([
        {"target_table": "dwd.orders", "target_column": "amount",
         "source_tables": ["ods.t_orders"], "source_columns": ["amount"]},
    ])
    out = trace_compare(
        table="dwd.orders", column="amount", key_column="id",
        base_task_id="shell-task",
        sample_keys=[1, 2, 3],
        datasource_map={"ods.t_orders": "ds-ods", "dwd.orders": "ds-dwd"},
    )
    assert out["focal"] == {"table": "dwd.orders", "column": "amount"}
    nodes = out["workflow_draft"]["nodes"]
    assert len(nodes) == 1
    node = nodes[0]
    assert node["type"] == "compare"
    assert node["config"]["task_id"] == "shell-task"
    assert node["config"]["key_columns_override"] == ["id"]
    src = node["config"]["source_sql_override"]
    tgt = node["config"]["target_sql_override"]
    assert "ods.t_orders" in src and "amount" in src
    assert "dwd.orders" in tgt and "amount" in tgt
    assert "id IN (1, 2, 3)" in src
    assert src.endswith("ORDER BY id")
    meta = node["config"]["_trace_compare"]
    assert meta["hop"] == 1
    assert meta["strategy"] == "direct"
    assert meta["datasource_source"] == "ds-ods"
    assert meta["datasource_target"] == "ds-dwd"
    assert meta["unmapped_tables"] == []


def test_no_sample_keys_omits_where(isolated_storage):
    _persist_lineage([
        {"target_table": "dwd.x", "target_column": "v",
         "source_tables": ["ods.x"], "source_columns": ["v"]},
    ])
    out = trace_compare(
        table="dwd.x", column="v", key_column="id", base_task_id="shell",
    )
    src = out["workflow_draft"]["nodes"][0]["config"]["source_sql_override"]
    assert "WHERE" not in src
    assert src.endswith("ORDER BY id")


def test_string_sample_keys_are_quoted(isolated_storage):
    _persist_lineage([
        {"target_table": "dwd.x", "target_column": "v",
         "source_tables": ["ods.x"], "source_columns": ["v"]},
    ])
    out = trace_compare(
        table="dwd.x", column="v", key_column="code", base_task_id="shell",
        sample_keys=["A", "O'Reilly"],
    )
    src = out["workflow_draft"]["nodes"][0]["config"]["source_sql_override"]
    assert "code IN ('A', 'O''Reilly')" in src


def test_multi_hop_emits_one_node_per_edge(isolated_storage):
    # ods.t → dwd.x → dws.report 链
    _persist_lineage([
        {"target_table": "dwd.x", "target_column": "v",
         "source_tables": ["ods.t"], "source_columns": ["v"]},
        {"target_table": "dws.report", "target_column": "metric",
         "source_tables": ["dwd.x"], "source_columns": ["v"]},
    ])
    out = trace_compare(
        table="dws.report", column="metric", key_column="id",
        base_task_id="shell", depth=3,
    )
    nodes = out["workflow_draft"]["nodes"]
    assert len(nodes) == 2
    # hop=1: dwd.x.v → dws.report.metric
    # hop=2: ods.t.v → dwd.x.v
    hops = [n["config"]["_trace_compare"]["hop"] for n in nodes]
    assert hops == [1, 2]
    upstreams = [n["config"]["_trace_compare"]["upstream"]["table"] for n in nodes]
    assert upstreams == ["dwd.x", "ods.t"]


def test_unmapped_datasource_emits_warning(isolated_storage):
    _persist_lineage([
        {"target_table": "dwd.x", "target_column": "v",
         "source_tables": ["ods.t"], "source_columns": ["v"]},
    ])
    out = trace_compare(
        table="dwd.x", column="v", key_column="id", base_task_id="shell",
        datasource_map={"ods.t": "ds-ods"},  # dwd.x missing
    )
    meta = out["workflow_draft"]["nodes"][0]["config"]["_trace_compare"]
    assert meta["datasource_source"] == "ds-ods"
    assert meta["datasource_target"] == ""
    assert "dwd.x" in meta["unmapped_tables"]
    assert any("dwd.x" in w for w in out["warnings"])


def test_per_table_keys_overrides_default(isolated_storage):
    _persist_lineage([
        {"target_table": "dwd.x", "target_column": "v",
         "source_tables": ["ods.t"], "source_columns": ["v"]},
    ])
    out = trace_compare(
        table="dwd.x", column="v", key_column="id", base_task_id="shell",
        per_table_keys={"ods.t": "user_id"},
    )
    src = out["workflow_draft"]["nodes"][0]["config"]["source_sql_override"]
    tgt = out["workflow_draft"]["nodes"][0]["config"]["target_sql_override"]
    assert src.startswith("SELECT user_id, v FROM ods.t")
    # downstream 仍走默认 key_column=id
    assert tgt.startswith("SELECT id, v FROM dwd.x")
    # key_columns_override 反映 upstream 的 key（compare 节点两端必须用同一 key 名）
    assert out["workflow_draft"]["nodes"][0]["config"]["key_columns_override"] == ["user_id"]


def test_invalid_identifier_rejected(isolated_storage):
    with pytest.raises(ValueError, match="非法标识符"):
        trace_compare(
            table="dwd.x; DROP TABLE u", column="v", key_column="id",
            base_task_id="shell",
        )
    with pytest.raises(ValueError, match="非法标识符"):
        trace_compare(
            table="dwd.x", column="v OR 1=1", key_column="id",
            base_task_id="shell",
        )


def test_missing_required_args_raise(isolated_storage):
    with pytest.raises(ValueError, match="table"):
        trace_compare(table="", column="v", key_column="id", base_task_id="x")
    with pytest.raises(ValueError, match="column"):
        trace_compare(table="t", column="", key_column="id", base_task_id="x")
    with pytest.raises(ValueError, match="key_column"):
        trace_compare(table="t", column="v", key_column="", base_task_id="x")
    with pytest.raises(ValueError, match="base_task_id"):
        trace_compare(table="t", column="v", key_column="id", base_task_id="")


def test_depth_clamped_to_bounds(isolated_storage):
    _persist_lineage([
        {"target_table": "dwd.x", "target_column": "v",
         "source_tables": ["ods.t"], "source_columns": ["v"]},
    ])
    # depth=0 → clamped to 1
    out = trace_compare(
        table="dwd.x", column="v", key_column="id",
        base_task_id="shell", depth=0,
    )
    assert "depth=1" in out["workflow_draft"]["description"]
    # depth=999 → clamped to 10
    out2 = trace_compare(
        table="dwd.x", column="v", key_column="id",
        base_task_id="shell", depth=999,
    )
    assert "depth=10" in out2["workflow_draft"]["description"]


def test_no_lineage_data_returns_empty_chain(isolated_storage):
    out = trace_compare(
        table="dwd.unseen", column="v", key_column="id", base_task_id="shell",
    )
    assert out["chain"] == []
    assert out["workflow_draft"]["nodes"] == []
    assert out["stats"]["edge_count"] == 0


# ─── HTTP 端点 ──────────────────────────────────────────────────────────────


# `client` / `client_anon` 来自 conftest.py。本文件保留 _login() helper 兼容旧 inline 头模式。


def _login(client: TestClient) -> str:
    """从 admin client 反查 token，兼容老测试 inline header 写法。"""
    auth_header = client.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def test_endpoint_requires_editor(client_anon):
    """匿名调 trace-compare 必须 401（router 级 get_current_user 拦）。"""
    r = client_anon.post("/api/lineage/trace-compare", json={})
    assert r.status_code == 401


def test_endpoint_400_on_missing_table(client):
    token = _login(client)
    r = client.post(
        "/api/lineage/trace-compare",
        headers={"Authorization": f"Bearer {token}"},
        json={"column": "v", "key_column": "id", "base_task_id": "x"},
    )
    assert r.status_code == 400
    assert "table" in r.json()["detail"]


def test_endpoint_returns_workflow_draft(client):
    _persist_lineage([
        {"target_table": "dwd.x", "target_column": "v",
         "source_tables": ["ods.t"], "source_columns": ["v"]},
    ])
    token = _login(client)
    r = client.post(
        "/api/lineage/trace-compare",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "table": "dwd.x", "column": "v", "key_column": "id",
            "base_task_id": "shell-task",
            "sample_keys": [1, 2],
            "datasource_map": {"ods.t": "ds-1", "dwd.x": "ds-2"},
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["focal"] == {"table": "dwd.x", "column": "v"}
    assert len(body["workflow_draft"]["nodes"]) == 1
    assert "trace-compare" in body["workflow_draft"]["tags"]
