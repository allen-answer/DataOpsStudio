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
