"""Phase 10 #4：资产详情 MVP 测试。

覆盖：
- _split_schema 处理 schema.basename / 单段名 / Oracle DB Link
- get_table_asset 反向查找 task / workflow 引用
- match_role 区分 source / target / source/target
- project_id 过滤
- HTTP 端点 /api/assets/table/{name} 含点号路径
- /api/assets/datasources 列表 + 不含 password
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from main import app
from app.models.compare import CompareTaskCreate
from app.models.datasource import DataSourceCreate
from app.models.workflow import WorkflowCreate, WorkflowNode, WorkflowNodeType
from app.services.assets import _split_schema, get_table_asset
from app.services.repositories import datasource_store, task_store, workflow_store


# ─── 单元 ────────────────────────────────────────────────────────────────────


def test_split_schema_dot_form():
    assert _split_schema("ods.t_users") == ("ods", "t_users")


def test_split_schema_single_segment():
    assert _split_schema("t_users") == ("(默认)", "t_users")


def test_split_schema_oracle_dblink_strips_at():
    assert _split_schema("dim.cust@remote_db") == ("dim", "cust")


def test_split_schema_multi_dot_uses_last():
    """`db.schema.table` → schema=`db.schema`，basename=`table`。"""
    assert _split_schema("db1.public.users") == ("db1.public", "users")


def test_get_table_asset_basic_shape(isolated_storage):
    asset = get_table_asset("ods.t_users")
    assert asset["kind"] == "table"
    assert asset["name"] == "ods.t_users"
    assert asset["schema"] == "ods"
    assert asset["basename"] == "t_users"
    assert asset["primary_role"] is None  # MVP 留空
    assert asset["stats"]["total_references"] == 0


def test_get_table_asset_finds_task_as_source(isolated_storage):
    task_store.create(CompareTaskCreate(
        name="客户表对账", sql_mode="double",
        source_sql="SELECT * FROM ods.t_users WHERE active = 1",
        target_sql="SELECT * FROM dwd.t_users_clean",
        source_id="x", target_id="y", key_columns=["id"],
    ))
    asset = get_table_asset("ods.t_users")
    assert len(asset["references"]["tasks"]) == 1
    ref = asset["references"]["tasks"][0]
    assert ref["name"] == "客户表对账"
    assert ref["match_role"] == "source"
    assert asset["stats"]["task_count"] == 1


def test_get_table_asset_distinguishes_source_target_both(isolated_storage):
    """同一表既被 source_sql 又被 target_sql 引用 → match_role=source/target。"""
    task_store.create(CompareTaskCreate(
        name="自反对账", sql_mode="double",
        source_sql="SELECT * FROM dwd.t_orders",
        target_sql="SELECT * FROM dwd.t_orders",
        source_id="x", target_id="y", key_columns=["id"],
    ))
    asset = get_table_asset("dwd.t_orders")
    assert asset["references"]["tasks"][0]["match_role"] == "source/target"


def test_get_table_asset_finds_workflow_via_node_config(isolated_storage):
    workflow_store.create(WorkflowCreate(
        name="客户清洗作业",
        nodes=[
            WorkflowNode(id="n1", type=WorkflowNodeType.PARAMS, config={
                "where_clause": "WHERE table_name = 'ods.t_users'",
            }),
        ],
    ))
    asset = get_table_asset("ods.t_users")
    assert len(asset["references"]["workflows"]) == 1
    assert asset["references"]["workflows"][0]["name"] == "客户清洗作业"


def test_get_table_asset_project_id_filter(isolated_storage):
    """同一表名在不同 project 的 task 下，project_id 过滤生效。"""
    task_store.create(CompareTaskCreate(
        name="proj-a-task", sql_mode="double",
        source_sql="SELECT * FROM ods.t_orders", target_sql="SELECT * FROM dwd.t_orders",
        source_id="x", target_id="y", key_columns=["id"],
        project_id="proj-a",
    ))
    task_store.create(CompareTaskCreate(
        name="proj-b-task", sql_mode="double",
        source_sql="SELECT * FROM ods.t_orders", target_sql="SELECT * FROM dwd.t_orders",
        source_id="x", target_id="y", key_columns=["id"],
        project_id="proj-b",
    ))
    a = get_table_asset("ods.t_orders", project_id="proj-a")
    assert {t["name"] for t in a["references"]["tasks"]} == {"proj-a-task"}
    full = get_table_asset("ods.t_orders")
    assert {t["name"] for t in full["references"]["tasks"]} == {"proj-a-task", "proj-b-task"}


def test_get_table_asset_empty_name_raises(isolated_storage):
    with pytest.raises(ValueError):
        get_table_asset("")


# ─── HTTP ───────────────────────────────────────────────────────────────────


@pytest.fixture
def client(isolated_storage):
    return TestClient(app)


def test_http_table_asset_with_dotted_name(client, isolated_storage):
    task_store.create(CompareTaskCreate(
        name="orders-task", sql_mode="double",
        source_sql="SELECT * FROM ods.t_orders", target_sql="SELECT * FROM dwd.t_orders",
        source_id="x", target_id="y", key_columns=["id"],
    ))
    response = client.get("/api/assets/table/ods.t_orders")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "ods.t_orders"
    assert body["schema"] == "ods"
    assert body["basename"] == "t_orders"
    assert body["stats"]["task_count"] == 1


def test_http_table_asset_not_referenced_returns_empty_lists(client, isolated_storage):
    response = client.get("/api/assets/table/missing.table")
    assert response.status_code == 200
    body = response.json()
    assert body["stats"]["total_references"] == 0
    assert body["references"]["tasks"] == []


def test_http_datasources_list_excludes_password(client, isolated_storage):
    datasource_store.create(DataSourceCreate(
        name="prod-db", db_type="MySQL", host="db", port=3306,
        database="orders", username="u", password="SECRET",
    ))
    response = client.get("/api/assets/datasources")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert "password" not in body[0]
    assert body[0]["name"] == "prod-db"


# ─── 字段列表（Phase 10 enhancement #1）──────────────────────────────────────


def _persist_lineage_run(insert_mappings: list[dict], run_id: str = "run-1") -> None:
    """Helper：建一个 workflow_run，含 1 个 lineage 节点 + 给定 insert_mappings。"""
    from app.models import (
        NodeRunStatus, WorkflowNodeRun, WorkflowNodeType, WorkflowRun, WorkflowRunStatus,
    )
    from app.services import workflow_history
    run = WorkflowRun(
        run_id=run_id, workflow_id="wf-x", workflow_name="lineage test",
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


def test_get_table_columns_aggregates_writes_and_reads(isolated_storage):
    from app.services.assets import get_table_columns
    _persist_lineage_run([
        # 写 dwd.t_users.id 一次
        {"target_table": "dwd.t_users", "target_column": "id",
         "source_tables": ["ods.t_users"], "source_columns": ["id"]},
        # 写 dwd.t_users.name 一次
        {"target_table": "dwd.t_users", "target_column": "name",
         "source_tables": ["ods.t_users"], "source_columns": ["name"]},
        # 读 ods.t_users.id 两次（出现在两个 mapping 的 source_columns 里）
    ])
    cols = get_table_columns("dwd.t_users")
    by_name = {c["name"]: c for c in cols}
    assert by_name["id"]["write_count"] == 1
    assert by_name["name"]["write_count"] == 1
    assert all(c["read_count"] == 0 for c in cols)  # 没人读 dwd.t_users 的字段

    # 反过来：ods.t_users 是源表，被读了两次（id + name）
    src_cols = get_table_columns("ods.t_users")
    assert {c["name"] for c in src_cols} == {"id", "name"}
    assert all(c["read_count"] == 1 and c["write_count"] == 0 for c in src_cols)


def test_get_table_columns_multi_source_requires_prefix(isolated_storage):
    """多源 mapping（join）：source_columns 必须显式带表前缀才算入。"""
    from app.services.assets import get_table_columns
    _persist_lineage_run([
        # JOIN：source_tables=[a, b]，源列里 a.x 显式带前缀，y 没前缀
        {"target_table": "dwd.merged", "target_column": "merged_id",
         "source_tables": ["ods.a", "ods.b"],
         "source_columns": ["ods.a.x", "y"]},  # y 不算入 ods.a 也不算入 ods.b
    ])
    a_cols = get_table_columns("ods.a")
    assert {c["name"]: c["read_count"] for c in a_cols} == {"x": 1}
    # y 没前缀 → 既不算 ods.a 也不算 ods.b
    b_cols = get_table_columns("ods.b")
    assert b_cols == []


def test_get_table_columns_sorted_by_total_heat(isolated_storage):
    from app.services.assets import get_table_columns
    _persist_lineage_run([
        {"target_table": "t", "target_column": "hot",
         "source_tables": ["s"], "source_columns": ["hot"]},
        {"target_table": "t", "target_column": "hot",
         "source_tables": ["s"], "source_columns": ["hot"]},
        {"target_table": "t", "target_column": "cold",
         "source_tables": ["s"], "source_columns": ["cold"]},
    ])
    cols = get_table_columns("t")
    # hot 写 2 次，cold 写 1 次 → hot 在前
    assert cols[0]["name"] == "hot"
    assert cols[0]["write_count"] == 2
    assert cols[1]["name"] == "cold"


def test_get_table_columns_empty_when_table_unseen(isolated_storage):
    from app.services.assets import get_table_columns
    # 没 workflow_run → 直接空
    assert get_table_columns("never.seen") == []


def test_get_table_columns_endpoint(client, isolated_storage):
    _persist_lineage_run([
        {"target_table": "dwd.users", "target_column": "id",
         "source_tables": ["ods.users"], "source_columns": ["id"]},
    ])
    r = client.get("/api/assets/columns/dwd.users")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["name"] == "id"
    assert body[0]["write_count"] == 1


def test_get_table_columns_empty_name_returns_400(client, isolated_storage):
    """name=path-converter 不会传空字符串，但 ValueError 兜底应生效（直接调 service）。"""
    from app.services.assets import get_table_columns
    with pytest.raises(ValueError):
        get_table_columns("")


# ─── S1.B 字段血缘深化：column lineage ────────────────────────────────────────


def test_column_lineage_upstream_qualified(isolated_storage):
    """upstream：合格名 ods.t.x → 直接归到 (ods.t, x)。"""
    from app.services.assets import get_column_lineage
    _persist_lineage_run([
        {"target_table": "dwd.users", "target_column": "id",
         "source_tables": ["ods.users", "ref.dim"],   # 多源 → 必须合格名
         "source_columns": ["ods.users.id", "ref.dim.partition_id"]},
    ])
    out = get_column_lineage("dwd.users", "id")
    upstream = {(u["table"], u["column"]) for u in out["upstream"]}
    assert ("ods.users", "id") in upstream
    assert ("ref.dim", "partition_id") in upstream


def test_column_lineage_upstream_unqualified_single_source(isolated_storage):
    """单源 mapping + unqualified col → 归到 source_tables[0]。"""
    from app.services.assets import get_column_lineage
    _persist_lineage_run([
        {"target_table": "dwd.users", "target_column": "name",
         "source_tables": ["ods.users"], "source_columns": ["name"]},
    ])
    out = get_column_lineage("dwd.users", "name")
    assert out["upstream"] == [{"table": "ods.users", "column": "name", "count": 1}]


def test_column_lineage_upstream_skips_unqualified_when_multi_source(isolated_storage):
    """多源 + unqualified → 拒绝（不知道归哪张）。"""
    from app.services.assets import get_column_lineage
    _persist_lineage_run([
        {"target_table": "dwd.x", "target_column": "z",
         "source_tables": ["a", "b"], "source_columns": ["z"]},  # 不知道 z 来自 a 还是 b
    ])
    assert get_column_lineage("dwd.x", "z")["upstream"] == []


def test_column_lineage_downstream_finds_target(isolated_storage):
    """downstream：当前字段被另一 mapping 当 source 用 → 它的 target 是下游。"""
    from app.services.assets import get_column_lineage
    _persist_lineage_run([
        # ods.users.id → dwd.users.id
        {"target_table": "dwd.users", "target_column": "id",
         "source_tables": ["ods.users"], "source_columns": ["id"]},
        # dwd.users.id → dws.user_summary.user_id
        {"target_table": "dws.user_summary", "target_column": "user_id",
         "source_tables": ["dwd.users"], "source_columns": ["id"]},
    ])
    out = get_column_lineage("dwd.users", "id")
    downstream = {(d["table"], d["column"]) for d in out["downstream"]}
    assert ("dws.user_summary", "user_id") in downstream
    # 不能把 dwd.users.id 自己当 downstream（自指排除）
    assert ("dwd.users", "id") not in downstream


def test_column_lineage_counts_aggregate_repeats(isolated_storage):
    """同一 (src,dst) 出现多次 → count 累加。"""
    from app.services.assets import get_column_lineage
    _persist_lineage_run([
        {"target_table": "dwd.x", "target_column": "id",
         "source_tables": ["ods.y"], "source_columns": ["id"]},
        {"target_table": "dwd.x", "target_column": "id",
         "source_tables": ["ods.y"], "source_columns": ["id"]},
    ])
    out = get_column_lineage("dwd.x", "id")
    assert len(out["upstream"]) == 1
    assert out["upstream"][0]["count"] == 2


def test_column_lineage_endpoint(client, isolated_storage):
    _persist_lineage_run([
        {"target_table": "dwd.users", "target_column": "id",
         "source_tables": ["ods.users"], "source_columns": ["id"]},
    ])
    r = client.get("/api/assets/column-lineage/dwd.users?column=id")
    assert r.status_code == 200
    body = r.json()
    assert body["upstream"][0]["table"] == "ods.users"


def test_column_lineage_missing_column_param_400(client, isolated_storage):
    r = client.get("/api/assets/column-lineage/dwd.users")
    assert r.status_code == 422  # FastAPI 422 for missing required Query


def test_column_lineage_empty_inputs_raise(isolated_storage):
    from app.services.assets import get_column_lineage
    with pytest.raises(ValueError):
        get_column_lineage("", "id")
    with pytest.raises(ValueError):
        get_column_lineage("t", "")
