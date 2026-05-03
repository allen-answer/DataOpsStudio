"""Phase 7 Track B 第 5 项：业务分组规则的单元测试。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.lineage.grouping import (
    GroupRule, _split_schema_basename, apply_group_rules, load_group_rules,
)


# ─── 文件加载 ──────────────────────────────────────────────────────────────────

def test_load_returns_empty_when_no_files(tmp_path: Path):
    rules = load_group_rules(yaml_path=tmp_path / "x.yml", json_path=tmp_path / "x.json")
    assert rules == []


def test_load_yaml_basic(tmp_path: Path):
    pytest.importorskip("yaml")
    yml = tmp_path / "rules.yml"
    yml.write_text("""\
groups:
  - name: 集中交易
    description: cisp 系统
    match:
      - schema_prefix: cisp
""", encoding="utf-8")
    rules = load_group_rules(yaml_path=yml, json_path=tmp_path / "x.json")
    assert len(rules) == 1
    assert rules[0].name == "集中交易"
    assert rules[0].description == "cisp 系统"
    assert rules[0].matchers[0].kind == "schema_prefix"
    assert rules[0].matchers[0].value == "cisp"  # 已 lowercase


def test_load_json_fallback(tmp_path: Path):
    payload = {
        "groups": [
            {
                "name": "config",
                "match": [{"basename_exact": "t_config"}],
            }
        ]
    }
    js = tmp_path / "rules.json"
    js.write_text(json.dumps(payload), encoding="utf-8")
    rules = load_group_rules(yaml_path=tmp_path / "missing.yml", json_path=js)
    assert len(rules) == 1
    assert rules[0].name == "config"


def test_load_yaml_takes_precedence_over_json(tmp_path: Path):
    pytest.importorskip("yaml")
    (tmp_path / "rules.yml").write_text(
        "groups:\n  - name: from_yaml\n    match:\n      - schema_prefix: y\n",
        encoding="utf-8",
    )
    (tmp_path / "rules.json").write_text(
        json.dumps({"groups": [{"name": "from_json", "match": [{"schema_prefix": "j"}]}]}),
        encoding="utf-8",
    )
    rules = load_group_rules(
        yaml_path=tmp_path / "rules.yml", json_path=tmp_path / "rules.json"
    )
    assert [r.name for r in rules] == ["from_yaml"]


def test_load_rejects_missing_match(tmp_path: Path):
    pytest.importorskip("yaml")
    yml = tmp_path / "rules.yml"
    yml.write_text("groups:\n  - name: x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="match 至少需要一条"):
        load_group_rules(yaml_path=yml, json_path=tmp_path / "x.json")


def test_load_rejects_unknown_matcher(tmp_path: Path):
    pytest.importorskip("yaml")
    yml = tmp_path / "rules.yml"
    yml.write_text(
        "groups:\n  - name: x\n    match:\n      - bogus_field: foo\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="未知 matcher"):
        load_group_rules(yaml_path=yml, json_path=tmp_path / "x.json")


def test_load_rejects_invalid_regex(tmp_path: Path):
    pytest.importorskip("yaml")
    yml = tmp_path / "rules.yml"
    yml.write_text(
        "groups:\n  - name: x\n    match:\n      - basename_regex: '[unclosed'\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="basename_regex 编译失败"):
        load_group_rules(yaml_path=yml, json_path=tmp_path / "x.json")


def test_load_empty_groups_returns_empty(tmp_path: Path):
    pytest.importorskip("yaml")
    yml = tmp_path / "rules.yml"
    yml.write_text("groups: []\n", encoding="utf-8")
    assert load_group_rules(yaml_path=yml, json_path=tmp_path / "x.json") == []


# ─── _split_schema_basename ───────────────────────────────────────────────────

def test_split_no_schema():
    assert _split_schema_basename("orders") == ("", "orders")


def test_split_simple_schema():
    assert _split_schema_basename("dw.orders") == ("dw", "orders")


def test_split_three_part_uses_rightmost_dot():
    # `catalog.schema.table` → schema 段是 'catalog.schema'
    assert _split_schema_basename("cat.dw.orders") == ("cat.dw", "orders")


def test_split_strips_dblink():
    assert _split_schema_basename("scott.emp@remote_db") == ("scott", "emp")


def test_split_strips_quotes():
    # 大小写在匹配时再统一，这里只剥引号
    assert _split_schema_basename('"Schema"."Table"') == ("Schema", "Table")


# ─── apply_group_rules：各 matcher 类型 ───────────────────────────────────────

def _rule(name: str, **matcher) -> GroupRule:
    """test helper：用 dict 风格构造单条规则，避免直接拿 _Matcher 内部类。"""
    from app.lineage.grouping import _Matcher
    import re
    matchers = []
    for k, v in matcher.items():
        regex = re.compile(v, re.IGNORECASE) if k == "basename_regex" else None
        matchers.append(_Matcher(kind=k, value=v.lower(), regex=regex))
    return GroupRule(name=name, matchers=matchers)


def _tables(*names: str) -> list[dict[str, str]]:
    return [{"table": n} for n in names]


def test_schema_prefix_matches():
    rules = [_rule("trade", schema_prefix="cisp")]
    bg, _ = apply_group_rules(rules, _tables("cispnew.t_jy", "ods.x"), [], [])
    assert bg[0]["name"] == "trade"
    assert bg[0]["tables"] == ["cispnew.t_jy"]


def test_schema_prefix_case_insensitive():
    rules = [_rule("trade", schema_prefix="CISP")]
    bg, _ = apply_group_rules(rules, _tables("CISPNEW.t_jy"), [], [])
    assert bg[0]["tables"] == ["CISPNEW.t_jy"]


def test_schema_prefix_does_not_match_unqualified():
    rules = [_rule("trade", schema_prefix="cisp")]
    bg, _ = apply_group_rules(rules, _tables("t_jy"), [], [])
    assert bg == []


def test_schema_exact_matches():
    rules = [_rule("config", schema_exact="config")]
    bg, _ = apply_group_rules(rules, _tables("config.t_param", "configx.y"), [], [])
    assert bg[0]["tables"] == ["config.t_param"]


def test_schema_contains_matches():
    rules = [_rule("trade", schema_contains="trade")]
    bg, _ = apply_group_rules(rules, _tables("dw_trade_v2.x", "ods.y"), [], [])
    assert bg[0]["tables"] == ["dw_trade_v2.x"]


def test_basename_prefix_matches():
    rules = [_rule("etl", basename_prefix="t_etl_")]
    bg, _ = apply_group_rules(rules, _tables("dw.t_etl_jy", "dw.t_etl_zqsz", "dw.other"), [], [])
    assert sorted(bg[0]["tables"]) == ["dw.t_etl_jy", "dw.t_etl_zqsz"]


def test_basename_suffix_matches():
    rules = [_rule("market", basename_suffix="_stock")]
    bg, _ = apply_group_rules(rules, _tables("dw.cust_stock", "dw.no_match"), [], [])
    assert bg[0]["tables"] == ["dw.cust_stock"]


def test_basename_exact_matches():
    rules = [_rule("config", basename_exact="t_config")]
    bg, _ = apply_group_rules(rules, _tables("any.t_config", "any.t_config_v2"), [], [])
    assert bg[0]["tables"] == ["any.t_config"]


def test_basename_contains_matches():
    rules = [_rule("market", basename_contains="position")]
    bg, _ = apply_group_rules(rules, _tables("dw.stock_position_snap", "dw.x"), [], [])
    assert bg[0]["tables"] == ["dw.stock_position_snap"]


def test_basename_regex_matches():
    rules = [_rule("filter", basename_regex=r"^cust_base_info$")]
    bg, _ = apply_group_rules(
        rules, _tables("dw.cust_base_info", "dw.cust_base_info_old"), [], []
    )
    assert bg[0]["tables"] == ["dw.cust_base_info"]


def test_title_keyword_matches_via_target_summary():
    rules = [_rule("trade", title_keyword="集中交易")]
    target_summary = [
        {"target_table": "dw.t_etl_jy", "titles": ["集中交易", "A股主板"]},
        {"target_table": "dw.other", "titles": ["其他业务"]},
    ]
    bg, _ = apply_group_rules(rules, _tables("dw.t_etl_jy", "dw.other"), target_summary, [])
    assert bg[0]["tables"] == ["dw.t_etl_jy"]


# ─── apply_group_rules：复合行为 ──────────────────────────────────────────────

def test_or_logic_within_one_group():
    rules = [
        GroupRule(
            name="trade",
            matchers=_rule("_", schema_prefix="cisp", basename_prefix="t_jy_").matchers,
        )
    ]
    bg, _ = apply_group_rules(
        rules, _tables("cispnew.x", "ods.t_jy_a", "ods.unrelated"), [], []
    )
    assert sorted(bg[0]["tables"]) == ["cispnew.x", "ods.t_jy_a"]


def test_table_can_belong_to_multiple_groups():
    rules = [
        _rule("market", basename_suffix="_stock"),
        _rule("dimension", schema_prefix="dim"),
    ]
    bg, _ = apply_group_rules(rules, _tables("dim.cust_stock"), [], [])
    names = {g["name"] for g in bg}
    assert names == {"market", "dimension"}


def test_unmatched_tables_omitted_no_other_bucket():
    rules = [_rule("trade", schema_prefix="cisp")]
    bg, _ = apply_group_rules(rules, _tables("ods.x", "ods.y"), [], [])
    assert bg == []


def test_target_count_only_counts_writes():
    rules = [_rule("etl", basename_prefix="t_")]
    target_summary = [
        {"target_table": "dw.t_a", "titles": []},
    ]
    tables = _tables("dw.t_a", "dw.t_b")  # t_a 是写入目标，t_b 只读
    bg, _ = apply_group_rules(rules, tables, target_summary, [])
    [group] = bg
    assert group["table_count"] == 2
    assert group["target_count"] == 1


def test_groups_preserve_rule_order():
    rules = [
        _rule("first", schema_prefix="a"),
        _rule("second", schema_prefix="b"),
        _rule("third", schema_prefix="c"),
    ]
    bg, _ = apply_group_rules(rules, _tables("c.x", "a.y", "b.z"), [], [])
    assert [g["name"] for g in bg] == ["first", "second", "third"]


def test_no_rules_returns_empty():
    bg, ge = apply_group_rules([], _tables("dw.x"), [], [])
    assert bg == [] and ge == []


# ─── grouped_edges ────────────────────────────────────────────────────────────

def _edge(src: str, tgt: str) -> dict[str, str]:
    return {"source_table": src, "target_table": tgt}


def test_grouped_edges_aggregate_count():
    rules = [
        _rule("ods", schema_prefix="ods"),
        _rule("dw", schema_prefix="dw"),
    ]
    edges = [
        _edge("ods.a", "dw.x"),
        _edge("ods.b", "dw.x"),
        _edge("ods.c", "dw.y"),
    ]
    tables = _tables("ods.a", "ods.b", "ods.c", "dw.x", "dw.y")
    _, ge = apply_group_rules(rules, tables, [], edges)
    [pair] = ge
    assert pair["source_group"] == "ods"
    assert pair["target_group"] == "dw"
    assert pair["edge_count"] == 3
    assert len(pair["table_pairs"]) == 3  # 三对独立 (src, tgt) 组合


def test_grouped_edges_skip_same_group():
    rules = [_rule("ods", schema_prefix="ods")]
    edges = [_edge("ods.a", "ods.b")]
    _, ge = apply_group_rules(rules, _tables("ods.a", "ods.b"), [], edges)
    assert ge == []


def test_grouped_edges_skip_when_either_end_ungrouped():
    rules = [_rule("dw", schema_prefix="dw")]
    edges = [_edge("ods.a", "dw.x"), _edge("dw.x", "external.y")]
    _, ge = apply_group_rules(rules, _tables("ods.a", "dw.x", "external.y"), [], edges)
    assert ge == []


def test_grouped_edges_when_table_in_multiple_groups():
    """表同时归属 A 和 B → 一条边到该表会同时贡献 X→A 和 X→B 两条 grouped edge。"""
    rules = [
        _rule("ods", schema_prefix="ods"),
        _rule("market", basename_suffix="_stock"),
        _rule("dim", schema_prefix="dim"),
    ]
    # dim.cust_stock 同时属于 market 和 dim
    edges = [_edge("ods.x", "dim.cust_stock")]
    tables = _tables("ods.x", "dim.cust_stock")
    _, ge = apply_group_rules(rules, tables, [], edges)
    pairs = {(g["source_group"], g["target_group"]) for g in ge}
    assert pairs == {("ods", "market"), ("ods", "dim")}


# ─── 端到端：YAML 文件到 analyze_sql_lineage 顶层 ─────────────────────────────

def test_e2e_yaml_rules_reach_semantic_lineage(tmp_path: Path, monkeypatch):
    """规则文件存在时，semantic_lineage.business_groups 应该自动落上。"""
    pytest.importorskip("yaml")
    rules_yml = tmp_path / "lineage_group_rules.yml"
    rules_yml.write_text("""\
groups:
  - name: 集中交易
    description: cisp 系统
    match:
      - schema_prefix: cispnew
  - name: ODS
    match:
      - schema_prefix: ods
""", encoding="utf-8")
    from app.lineage import semantic
    monkeypatch.setattr(semantic, "LINEAGE_GROUP_RULES_YAML", rules_yml)
    monkeypatch.setattr(semantic, "LINEAGE_GROUP_RULES_JSON", tmp_path / "missing.json")
    monkeypatch.setattr(semantic, "_group_rules_cache", None)

    from app.lineage.analyzer import analyze_sql_lineage
    sql = "INSERT INTO cispnew.t_etl_jy SELECT a, b FROM ods.src_jy;"
    result = analyze_sql_lineage(sql, dialect="oracle")
    sem = result["semantic_lineage"]

    bg = {g["name"]: g for g in sem["business_groups"]}
    assert "集中交易" in bg and "ODS" in bg
    assert bg["集中交易"]["tables"] == ["cispnew.t_etl_jy"]
    assert bg["集中交易"]["target_count"] == 1
    assert bg["ODS"]["tables"] == ["ods.src_jy"]
    assert bg["ODS"]["target_count"] == 0

    [edge] = sem["grouped_edges"]
    assert edge["source_group"] == "ODS"
    assert edge["target_group"] == "集中交易"
    assert edge["edge_count"] == 1


def test_e2e_no_rules_file_no_business_groups(tmp_path: Path, monkeypatch):
    from app.lineage import semantic
    monkeypatch.setattr(semantic, "LINEAGE_GROUP_RULES_YAML", tmp_path / "no.yml")
    monkeypatch.setattr(semantic, "LINEAGE_GROUP_RULES_JSON", tmp_path / "no.json")
    monkeypatch.setattr(semantic, "_group_rules_cache", None)

    from app.lineage.analyzer import analyze_sql_lineage
    result = analyze_sql_lineage("INSERT INTO a SELECT * FROM b;", dialect="mysql")
    assert result["semantic_lineage"]["business_groups"] == []
    assert result["semantic_lineage"]["grouped_edges"] == []


def test_e2e_corrupt_rules_file_falls_back_to_empty(tmp_path: Path, monkeypatch):
    """规则文件坏掉时，主流程不能崩，business_groups 降级为空表。"""
    pytest.importorskip("yaml")
    bad = tmp_path / "bad.yml"
    bad.write_text("groups:\n  - name: x\n", encoding="utf-8")  # 缺 match → 解析报错

    from app.lineage import semantic
    monkeypatch.setattr(semantic, "LINEAGE_GROUP_RULES_YAML", bad)
    monkeypatch.setattr(semantic, "LINEAGE_GROUP_RULES_JSON", tmp_path / "missing.json")
    monkeypatch.setattr(semantic, "_group_rules_cache", None)

    from app.lineage.analyzer import analyze_sql_lineage
    result = analyze_sql_lineage("INSERT INTO a SELECT * FROM b;", dialect="mysql")
    # 不抛异常 + business_groups 为空
    assert result["semantic_lineage"]["business_groups"] == []
