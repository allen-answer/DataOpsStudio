"""Scenario SQL templating tests（Phase 12 切片 15）。

scope:
- `render_template`：基本替换 / 多变量 / 类型转换 / 缺失保留 / 空 SQL
- recorder lineage_script 调用时使用 scenario.variables 渲染
- 前端 JS 镜像逻辑用 Python 等价（用 regex 反推一遍 reasoning）
"""
from __future__ import annotations

from typing import Any

from app.scenarios.models import Scenario
from app.scenarios.recorder import record_scenario
from app.scenarios.templating import render_template


# ─── 单元：render_template ─────────────────────────────────────────────────


def test_basic_substitution():
    r = render_template("WHERE dt > '{{cutoff_date}}'", {"cutoff_date": "2026-05-01"})
    assert r.text == "WHERE dt > '2026-05-01'"
    assert r.substituted == ["cutoff_date"]
    assert r.missing == []


def test_multi_variable_substitution():
    r = render_template(
        "SELECT * FROM t WHERE id > {{min_id}} AND id < {{max_id}}",
        {"min_id": 100, "max_id": 200},
    )
    assert "id > 100" in r.text and "id < 200" in r.text
    assert set(r.substituted) == {"min_id", "max_id"}


def test_missing_variable_kept_as_is():
    """SQL 引用了 var 但 variables 没提供 → 保留原样 + missing 收集起来。"""
    r = render_template("WHERE dt > '{{cutoff_date}}'", {})
    assert r.text == "WHERE dt > '{{cutoff_date}}'"
    assert r.missing == ["cutoff_date"]
    assert r.substituted == []


def test_unused_variable_no_error():
    """variables 多余的 key 不报错（caller 可能预定义全套）。"""
    r = render_template(
        "SELECT 1",
        {"cutoff_date": "2026-05-01", "extra": "unused"},
    )
    assert r.text == "SELECT 1"
    assert r.substituted == []  # 没真正用到


def test_whitespace_inside_placeholder():
    """`{{ name }}` 和 `{{name}}` 都该工作。"""
    r = render_template("v={{  x  }}", {"x": "ok"})
    assert r.text == "v=ok"


def test_invalid_identifier_not_matched():
    """Jinja-like 表达式 `{{ x + 1 }}` / `{{1+2}}` 不该被替换 —— 我们只支持 identifier。"""
    r = render_template("{{1+2}}", {"1": "no"})
    assert r.text == "{{1+2}}"  # 整段保留


def test_typed_values_coerced_to_str():
    r = render_template("v={{i}} b={{b}} f={{f}}", {"i": 42, "b": True, "f": 3.14})
    assert r.text == "v=42 b=true f=3.14"


def test_none_value_renders_empty_string():
    r = render_template("v='{{x}}'", {"x": None})
    assert r.text == "v=''"


def test_empty_sql_returns_empty():
    r = render_template("", {"x": "y"})
    assert r.text == ""
    assert r.substituted == []


def test_no_variables_dict_collects_missing():
    """variables=None / {} 时仍扫一遍 SQL 看是否有 `{{var}}` 形态。"""
    r = render_template("WHERE dt > '{{cutoff_date}}'", None)
    assert r.text == "WHERE dt > '{{cutoff_date}}'"
    assert r.missing == ["cutoff_date"]


# ─── 集成：recorder 用 scenario.variables 渲染 lineage_script ─────────────


def _scenario(**kwargs: Any) -> Scenario:
    payload = {
        "id": "test", "name": "T", "seed": 42,
        "tables": [{"name": "t", "role": "source", "rows": 0,
                    "columns": [{"name": "id", "type": "INT", "pk": True, "gen": "sequence"}]}],
    }
    payload.update(kwargs)
    return Scenario.model_validate(payload)


def test_recorder_renders_lineage_script_sql(isolated_storage):
    """yml variables 块进 scenario → recorder 写出的 history JSON 含渲染后 SQL。"""
    s = _scenario(
        variables={"cutoff_date": "2026-05-01"},
        workloads=[{
            "kind": "lineage_script", "name": "etl",
            "sql": "INSERT INTO dwd.t SELECT id FROM ods.t WHERE dt > '{{cutoff_date}}';",
        }],
    )
    res = record_scenario(s, datasource_id="ds-1")
    run = res["lineage_runs"][0]
    assert run["ok"] is True
    assert run.get("variables_substituted") == ["cutoff_date"]
    assert run.get("variables_missing", []) == []
    # history JSON 落进 results，sql 字段已渲染
    import json as _json
    data = _json.loads(
        (isolated_storage["results"] / f"{run['run_id']}.json").read_text(encoding="utf-8")
    )
    assert "{{cutoff_date}}" not in data["sql"]
    assert "2026-05-01" in data["sql"]


def test_recorder_lineage_script_missing_var_keeps_placeholder(isolated_storage):
    """variables 不提供 → SQL 占位符保留，recorder 标 missing 字段。"""
    s = _scenario(
        # 故意不声明 variables: {}
        workloads=[{
            "kind": "lineage_script", "name": "etl",
            "sql": "INSERT INTO dwd.t SELECT id FROM ods.t WHERE dt > '{{cutoff_date}}';",
        }],
    )
    res = record_scenario(s, datasource_id="ds-1")
    run = res["lineage_runs"][0]
    # analyzer 仍能解析（sqlglot 不在意字符串字面值是什么）
    assert run["ok"] is True
    assert run.get("variables_missing") == ["cutoff_date"]
    # variables_substituted 字段在没替换时不应出现
    assert "variables_substituted" not in run


def test_recorder_lineage_script_no_template_no_metadata(isolated_storage):
    """SQL 不含 {{var}} 时也不该塞 variables_substituted/missing 字段。"""
    s = _scenario(
        variables={"cutoff_date": "2026-05-01"},  # 声明了但 SQL 没引用
        workloads=[{
            "kind": "lineage_script", "name": "etl",
            "sql": "INSERT INTO dwd.t SELECT id FROM ods.t;",
        }],
    )
    res = record_scenario(s, datasource_id="ds-1")
    run = res["lineage_runs"][0]
    assert run["ok"] is True
    assert "variables_substituted" not in run
    assert "variables_missing" not in run
