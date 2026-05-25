"""FK references / referential integrity 测试(Phase 14 #3 Round 6)。

覆盖:
- Pydantic 校验:gen=foreign_key 缺 references / 格式错 / 引用不存在 / 循环 / 自引用
- _resolve_order:topological sort 按 FK 依赖
- _FKPool:add / get / schema 前缀模糊匹配
- generator:fk 列从 pool 抽样 / match_rate / fk_unique / fk_distribution=zipf
- e2e:3 表 customer/account/order 生成,验证 JOIN 拿到匹配行
"""
from __future__ import annotations

import pytest

from app.scenarios.generator import (
    _FKPool, _resolve_order, generate_scenario,
)
from app.scenarios.models import Scenario


# ─── Pydantic validator ────────────────────────────────────────────────────


def _minimal_scenario(tables: list[dict]) -> dict:
    return {
        "id": "test",
        "name": "T",
        "tables": tables,
    }


def test_fk_missing_references_rejected():
    """gen=foreign_key 但 references 为空 → ValueError"""
    with pytest.raises(ValueError, match="references"):
        Scenario.model_validate(_minimal_scenario([
            {"name": "child", "role": "source", "rows": 10, "columns": [
                {"name": "fk", "type": "INT", "gen": "foreign_key"},
            ]},
        ]))


def test_fk_references_wrong_format_rejected():
    """references 没有点号(必须 'table.column')"""
    with pytest.raises(ValueError, match="格式不对"):
        Scenario.model_validate(_minimal_scenario([
            {"name": "child", "role": "source", "rows": 10, "columns": [
                {"name": "fk", "type": "INT", "gen": "foreign_key",
                 "references": "no_dot_here"},
            ]},
        ]))


def test_fk_references_nonexistent_table_rejected():
    with pytest.raises(ValueError, match="不在 scenario.tables"):
        Scenario.model_validate(_minimal_scenario([
            {"name": "child", "role": "source", "rows": 10, "columns": [
                {"name": "fk", "type": "INT", "gen": "foreign_key",
                 "references": "nonexistent.id"},
            ]},
        ]))


def test_fk_references_nonexistent_column_rejected():
    with pytest.raises(ValueError, match="不在表"):
        Scenario.model_validate(_minimal_scenario([
            {"name": "parent", "role": "source", "rows": 10, "columns": [
                {"name": "id", "type": "INT", "gen": "sequence"},
            ]},
            {"name": "child", "role": "source", "rows": 10, "columns": [
                {"name": "fk", "type": "INT", "gen": "foreign_key",
                 "references": "parent.no_such_col"},
            ]},
        ]))


def test_fk_self_reference_rejected():
    with pytest.raises(ValueError, match="自引用"):
        Scenario.model_validate(_minimal_scenario([
            {"name": "t", "role": "source", "rows": 10, "columns": [
                {"name": "id", "type": "INT", "gen": "sequence"},
                {"name": "parent_id", "type": "INT", "gen": "foreign_key",
                 "references": "t.id"},
            ]},
        ]))


def test_fk_cycle_detected():
    """A → B → A 循环 → ValueError"""
    with pytest.raises(ValueError, match="循环"):
        Scenario.model_validate(_minimal_scenario([
            {"name": "a", "role": "source", "rows": 10, "columns": [
                {"name": "id", "type": "INT", "gen": "sequence"},
                {"name": "b_id", "type": "INT", "gen": "foreign_key",
                 "references": "b.id"},
            ]},
            {"name": "b", "role": "source", "rows": 10, "columns": [
                {"name": "id", "type": "INT", "gen": "sequence"},
                {"name": "a_id", "type": "INT", "gen": "foreign_key",
                 "references": "a.id"},
            ]},
        ]))


def test_fk_references_on_non_fk_column_rejected():
    """非 foreign_key 列带 references → ValueError(防误填)"""
    with pytest.raises(ValueError, match="仅 gen=foreign_key"):
        Scenario.model_validate(_minimal_scenario([
            {"name": "t", "role": "source", "rows": 10, "columns": [
                {"name": "x", "type": "INT", "gen": "random_int",
                 "references": "other.id"},
            ]},
        ]))


def test_fk_diamond_shape_accepted():
    """A → B, A → C, B → D, C → D 钻石依赖 OK (DAG)"""
    s = Scenario.model_validate(_minimal_scenario([
        {"name": "a", "role": "source", "rows": 10, "columns": [
            {"name": "id", "type": "INT", "gen": "sequence"},
        ]},
        {"name": "b", "role": "source", "rows": 10, "columns": [
            {"name": "id", "type": "INT", "gen": "sequence"},
            {"name": "a_id", "type": "INT", "gen": "foreign_key", "references": "a.id"},
        ]},
        {"name": "c", "role": "source", "rows": 10, "columns": [
            {"name": "id", "type": "INT", "gen": "sequence"},
            {"name": "a_id", "type": "INT", "gen": "foreign_key", "references": "a.id"},
        ]},
        {"name": "d", "role": "source", "rows": 10, "columns": [
            {"name": "b_id", "type": "INT", "gen": "foreign_key", "references": "b.id"},
            {"name": "c_id", "type": "INT", "gen": "foreign_key", "references": "c.id"},
        ]},
    ]))
    assert len(s.tables) == 4


# ─── _resolve_order topological sort ──────────────────────────────────────


def test_resolve_order_simple_chain():
    """A → B → C,生成顺序必须 A, B, C"""
    s = Scenario.model_validate(_minimal_scenario([
        {"name": "c", "role": "source", "rows": 5, "columns": [
            {"name": "b_id", "type": "INT", "gen": "foreign_key", "references": "b.id"},
        ]},
        {"name": "b", "role": "source", "rows": 5, "columns": [
            {"name": "id", "type": "INT", "gen": "sequence"},
            {"name": "a_id", "type": "INT", "gen": "foreign_key", "references": "a.id"},
        ]},
        {"name": "a", "role": "source", "rows": 5, "columns": [
            {"name": "id", "type": "INT", "gen": "sequence"},
        ]},
    ]))
    order = _resolve_order(s.tables)
    names = [t.name for t in order]
    assert names.index("a") < names.index("b") < names.index("c")


# ─── _FKPool ──────────────────────────────────────────────────────────────


def test_fk_pool_add_get():
    pool = _FKPool()
    pool.add("customer", "id", [1, 2, 3, 4, 5])
    assert pool.get("customer.id") == [1, 2, 3, 4, 5]


def test_fk_pool_schema_qualified_lookup():
    """注册裸表名,引用 schema.table.col 也能找到"""
    pool = _FKPool()
    pool.add("customer", "id", [1, 2, 3])
    # 引用 "dw.customer.id" 应该找到(去 schema 前缀 fallback)
    assert pool.get("dw.customer.id") == [1, 2, 3]


def test_fk_pool_missing_returns_none():
    pool = _FKPool()
    assert pool.get("missing.col") is None


# ─── generator FK 抽样 ─────────────────────────────────────────────────────


def test_generator_fk_values_all_in_parent_pool():
    """match_rate=1.0 默认:child fk 列的值 100% 在 parent 池里"""
    s = Scenario.model_validate({
        "id": "fk-basic", "name": "fk basic", "seed": 42,
        "tables": [
            {"name": "parent", "role": "source", "rows": 50, "columns": [
                {"name": "id", "type": "INT", "gen": "sequence"},
            ]},
            {"name": "child", "role": "source", "rows": 200, "columns": [
                {"name": "id", "type": "INT", "gen": "sequence"},
                {"name": "parent_id", "type": "INT", "gen": "foreign_key",
                 "references": "parent.id"},
            ]},
        ],
    })
    data = generate_scenario(s)
    parent_ids = {row["id"] for row in data["parent"]}
    child_fk = {row["parent_id"] for row in data["child"]}
    # child fk 全部在 parent pool 内
    assert child_fk.issubset(parent_ids)


def test_generator_fk_match_rate_partial():
    """match_rate=0.5:约一半 fk 值不在 parent pool"""
    s = Scenario.model_validate({
        "id": "fk-partial", "name": "partial", "seed": 42,
        "tables": [
            {"name": "p", "role": "source", "rows": 30, "columns": [
                {"name": "id", "type": "INT", "gen": "sequence"},
            ]},
            {"name": "c", "role": "source", "rows": 1000, "columns": [
                {"name": "p_id", "type": "INT", "gen": "foreign_key",
                 "references": "p.id", "match_rate": 0.5},
            ]},
        ],
    })
    data = generate_scenario(s)
    parent_ids = {row["id"] for row in data["p"]}
    in_pool = sum(1 for row in data["c"] if row["p_id"] in parent_ids)
    # 应该 ~50%,允许 ±15% 偏差
    ratio = in_pool / len(data["c"])
    assert 0.35 <= ratio <= 0.65, f"match_rate=0.5 实际命中率 {ratio:.2%}"


def test_generator_fk_unique_no_dup():
    """fk_unique=True 时 child fk 列在表内不重复(1:1 关系)"""
    s = Scenario.model_validate({
        "id": "fk-unique", "name": "unique", "seed": 42,
        "tables": [
            {"name": "p", "role": "source", "rows": 100, "columns": [
                {"name": "id", "type": "INT", "gen": "sequence"},
            ]},
            {"name": "c", "role": "source", "rows": 50, "columns": [
                {"name": "p_id", "type": "INT", "gen": "foreign_key",
                 "references": "p.id", "fk_unique": True},
            ]},
        ],
    })
    data = generate_scenario(s)
    fk_values = [row["p_id"] for row in data["c"]]
    assert len(fk_values) == len(set(fk_values)), "fk_unique=True 但有重复值"


def test_generator_fk_distribution_zipf_skewed():
    """fk_distribution=zipf 头部值占比高于均匀"""
    s = Scenario.model_validate({
        "id": "fk-zipf", "name": "zipf", "seed": 42,
        "tables": [
            {"name": "p", "role": "source", "rows": 100, "columns": [
                {"name": "id", "type": "INT", "gen": "sequence"},
            ]},
            {"name": "c", "role": "source", "rows": 10000, "columns": [
                {"name": "p_id", "type": "INT", "gen": "foreign_key",
                 "references": "p.id",
                 "fk_distribution": "zipf", "fk_zipf_alpha": 1.5},
            ]},
        ],
    })
    data = generate_scenario(s)
    parent_ids = sorted({row["id"] for row in data["p"]})
    # 取 parent_ids 头部 20% (前 20 个 — 注意 sequence 默认是 1..100 的 int)
    head_set = set(parent_ids[:20])
    head_hits = sum(1 for row in data["c"] if row["p_id"] in head_set)
    head_ratio = head_hits / len(data["c"])
    # zipf 头部 20% 应占至少 40%(均匀只 20%)
    assert head_ratio > 0.4, f"zipf alpha=1.5 头部 20% 只占 {head_ratio:.2%},不够偏斜"


# ─── e2e:3 表 customer/account/order JOIN 拿匹配行 ──────────────────────


def test_e2e_three_table_join_returns_matched_rows():
    """模拟真实业务:customer 100 → account 200(1:N) → order 1000(N:1)
    生成后 JOIN 应该 100% 拿到匹配行(match_rate=1.0)"""
    s = Scenario.model_validate({
        "id": "three-table", "name": "客户-账户-订单", "seed": 42,
        "tables": [
            {"name": "customer", "role": "source", "rows": 100, "columns": [
                {"name": "cust_id", "type": "INT", "gen": "sequence", "pk": True},
                {"name": "name", "type": "VARCHAR(40)", "gen": "realistic"},
            ]},
            {"name": "account", "role": "source", "rows": 200, "columns": [
                {"name": "acct_id", "type": "INT", "gen": "sequence", "pk": True},
                {"name": "cust_id", "type": "INT", "gen": "foreign_key",
                 "references": "customer.cust_id"},
            ]},
            {"name": "order_tbl", "role": "source", "rows": 1000, "columns": [
                {"name": "order_id", "type": "INT", "gen": "sequence", "pk": True},
                {"name": "acct_id", "type": "INT", "gen": "foreign_key",
                 "references": "account.acct_id"},
                {"name": "amount", "type": "DECIMAL(10,2)", "gen": "realistic"},
            ]},
        ],
    })
    data = generate_scenario(s)
    # 验证拓扑序生成正确
    assert len(data["customer"]) == 100
    assert len(data["account"]) == 200
    assert len(data["order_tbl"]) == 1000
    # account.cust_id 全部 ∈ customer.cust_id
    cust_ids = {row["cust_id"] for row in data["customer"]}
    acct_cust_ids = {row["cust_id"] for row in data["account"]}
    assert acct_cust_ids.issubset(cust_ids), "account → customer JOIN 不全匹配"
    # order.acct_id 全部 ∈ account.acct_id
    acct_ids = {row["acct_id"] for row in data["account"]}
    order_acct_ids = {row["acct_id"] for row in data["order_tbl"]}
    assert order_acct_ids.issubset(acct_ids), "order → account JOIN 不全匹配"
