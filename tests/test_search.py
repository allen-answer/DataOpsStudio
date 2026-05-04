"""Phase 10 第 2 项：全局搜索 / 反向索引测试。

覆盖：
- 跨 5 类资产搜索（datasource / task / workflow / history / lineage_script）
- AND 多 token 语义
- 字段权重（name > tables > sql body）
- project_id 过滤（item.project_id 空 = 全局可见）
- limit 截断
- 空 query 返回空
- HTTP /api/search 端点（through TestClient）
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from main import app
from app.models.compare import CompareTaskCreate
from app.models.datasource import DataSourceCreate
from app.models.workflow import WorkflowCreate, WorkflowNode, WorkflowNodeType
from app.services.repositories import datasource_store, task_store, workflow_store
from app.services.search import _extract_tables, search


# ─── 单元：核心 search() ─────────────────────────────────────────────────────


def test_extract_tables_picks_up_from_join_into():
    sql = """
    INSERT INTO dwd.t_orders (id, name)
    SELECT a.id, a.name FROM ods.t_users a
    JOIN ref.code b ON a.status_code = b.code
    """
    assert _extract_tables(sql) == {"dwd.t_orders", "ods.t_users", "ref.code"}


def test_search_empty_query_returns_empty(isolated_storage):
    result = search("")
    assert result["total"] == 0
    assert result["hits"] == []
    assert result["by_kind"] == {}


def test_search_hits_datasource_by_name(isolated_storage):
    datasource_store.create(DataSourceCreate(
        name="prod-mysql-orders", db_type="MySQL", host="db.internal", port=3306, database="orders",
    ))
    datasource_store.create(DataSourceCreate(
        name="dev-postgres", db_type="MySQL", host="dev.local", port=5432, database="dev",
    ))
    result = search("orders")
    # name 命中 100 + database 命中 30 = 同一条只取最大 → score=100
    assert result["total"] == 1
    hit = result["hits"][0]
    assert hit["kind"] == "datasource"
    assert hit["name"] == "prod-mysql-orders"
    assert hit["match_path"] == "name"
    assert hit["score"] == 100


def test_search_hits_task_by_table_name(isolated_storage):
    task_store.create(CompareTaskCreate(
        name="客户表对账",
        sql_mode="double",
        source_sql="SELECT * FROM ods.t_customer WHERE status = 1",
        target_sql="SELECT * FROM dwd.t_customer_clean",
        source_id="x", target_id="y", key_columns=["id"],
    ))
    result = search("t_customer")
    # 表名命中应该上分（50），高于纯 sql body 命中
    assert result["total"] == 1
    hit = result["hits"][0]
    assert hit["kind"] == "task"
    assert hit["match_path"] == "tables"
    assert "t_customer" in hit["snippet"]
    assert "ods.t_customer" in hit["metadata"]["tables"]


def test_search_and_semantics_all_tokens_required(isolated_storage):
    task_store.create(CompareTaskCreate(
        name="客户表对账", sql_mode="double",
        source_sql="SELECT * FROM ods.t_customer", target_sql="SELECT * FROM dwd.t_customer",
        source_id="x", target_id="y", key_columns=["id"],
    ))
    task_store.create(CompareTaskCreate(
        name="订单表对账", sql_mode="double",
        source_sql="SELECT * FROM ods.t_orders", target_sql="SELECT * FROM dwd.t_orders",
        source_id="x", target_id="y", key_columns=["id"],
    ))
    # 两个 token 必须都命中
    result = search("客户 对账")
    assert result["total"] == 1
    assert result["hits"][0]["name"] == "客户表对账"
    # 单 token "对账" 应该都命中
    result = search("对账")
    assert result["total"] == 2


def test_search_workflow_by_tag_and_node_config(isolated_storage):
    workflow_store.create(WorkflowCreate(
        name="月底全量重刷",
        description="跑完整套 ETL 重刷 fact 表",
        tags=["monthly", "full-refresh"],
        nodes=[
            WorkflowNode(id="extract", type=WorkflowNodeType.PARAMS, config={
                "source_query": "SELECT * FROM dwd.t_fact_orders WHERE dt = ${data_dt}",
            }),
        ],
    ))
    # tag 命中
    result = search("monthly")
    assert result["total"] == 1
    assert result["hits"][0]["match_path"] == "tags"
    # node config 命中
    result = search("t_fact_orders")
    assert result["total"] == 1
    assert "extract" in result["hits"][0]["match_path"]


def test_search_kinds_filter(isolated_storage):
    datasource_store.create(DataSourceCreate(
        name="orders-db", db_type="MySQL", host="x", port=1, database="d",
    ))
    task_store.create(CompareTaskCreate(
        name="orders-recon", sql_mode="double",
        source_sql="SELECT * FROM t_orders", target_sql="SELECT * FROM t_orders_v2",
        source_id="x", target_id="y", key_columns=["id"],
    ))
    # 全部
    full = search("orders")
    assert full["by_kind"].get("datasource", 0) >= 1
    assert full["by_kind"].get("task", 0) >= 1
    # 只搜 task
    only_task = search("orders", kinds=["task"])
    assert all(h["kind"] == "task" for h in only_task["hits"])


def test_search_project_id_filter(isolated_storage):
    """item.project_id 等于 query.project_id 或 item.project_id 空 → 可见。
    item.project_id 非空且不等于 query.project_id → 不可见。
    """
    datasource_store.create(DataSourceCreate(
        name="public-db", db_type="MySQL", host="x", port=1, database="d", project_id="",
    ))
    datasource_store.create(DataSourceCreate(
        name="proj-a-db", db_type="MySQL", host="y", port=1, database="d", project_id="proj-a",
    ))
    datasource_store.create(DataSourceCreate(
        name="proj-b-db", db_type="MySQL", host="z", port=1, database="d", project_id="proj-b",
    ))
    # 空 project_id：所有
    result = search("db", project_id="")
    assert result["total"] == 3
    # project_id=proj-a：public（空） + proj-a，不含 proj-b
    result = search("db", project_id="proj-a")
    names = {h["name"] for h in result["hits"]}
    assert names == {"public-db", "proj-a-db"}


def test_search_limit_truncates_but_total_unchanged(isolated_storage):
    for i in range(20):
        datasource_store.create(DataSourceCreate(
            name=f"orders-shard-{i}", db_type="MySQL", host="x", port=1, database="d",
        ))
    result = search("orders", limit=5)
    assert result["total"] == 20
    assert len(result["hits"]) == 5


def test_search_score_orders_name_above_table_above_sql(isolated_storage):
    """同一关键字命中 name 的得分 > 命中 table 的 > 仅命中 SQL body 的。"""
    task_store.create(CompareTaskCreate(
        name="other-task",
        source_sql="SELECT * FROM x WHERE keyword = 1", target_sql="SELECT * FROM y",
        source_id="x", target_id="y", sql_mode="double", key_columns=["id"],
    ))
    task_store.create(CompareTaskCreate(
        name="another-task",
        source_sql="SELECT * FROM dwd.keyword_table", target_sql="SELECT * FROM y",
        source_id="x", target_id="y", sql_mode="double", key_columns=["id"],
    ))
    task_store.create(CompareTaskCreate(
        name="keyword-task",
        source_sql="SELECT * FROM x", target_sql="SELECT * FROM y",
        source_id="x", target_id="y", sql_mode="double", key_columns=["id"],
    ))
    result = search("keyword")
    # 三条都命中，score 排序：name (100) > table (50) > sql (10)
    names_in_order = [h["name"] for h in result["hits"]]
    assert names_in_order == ["keyword-task", "another-task", "other-task"]


# ─── 集成：HTTP 端点 ─────────────────────────────────────────────────────────


@pytest.fixture
def client(isolated_storage):
    return TestClient(app)


def test_http_search_returns_envelope(client, isolated_storage):
    datasource_store.create(DataSourceCreate(
        name="orders-prod", db_type="MySQL", host="db", port=3306, database="orders",
    ))
    response = client.get("/api/search", params={"q": "orders"})
    assert response.status_code == 200
    body = response.json()
    assert body["query"] == "orders"
    assert body["total"] >= 1
    assert "hits" in body
    assert "by_kind" in body
    # 响应体走 SearchResponse —— 每条 hit 必有这些字段
    hit = body["hits"][0]
    for f in ("kind", "id", "name", "snippet", "match_path", "score", "project_id", "metadata"):
        assert f in hit


def test_http_search_kinds_query_param(client, isolated_storage):
    datasource_store.create(DataSourceCreate(
        name="orders-db", db_type="MySQL", host="x", port=1, database="d",
    ))
    task_store.create(CompareTaskCreate(
        name="orders-task", sql_mode="double",
        source_sql="SELECT * FROM t_orders", target_sql="SELECT * FROM t_orders_v2",
        source_id="x", target_id="y", key_columns=["id"],
    ))
    response = client.get("/api/search", params={"q": "orders", "kinds": ["task"]})
    assert response.status_code == 200
    body = response.json()
    assert all(h["kind"] == "task" for h in body["hits"])


def test_http_search_rejects_empty_query(client, isolated_storage):
    response = client.get("/api/search", params={"q": ""})
    # FastAPI Query(min_length=1) → 422
    assert response.status_code == 422
