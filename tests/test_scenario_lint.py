"""scenario_lint 静态校验脚本 tests（Phase 12 切片 18）。

scope:
- lint_scenarios：load 失败 / generator 冒烟 / 交叉引用 / 模板变量四类检查
- main() CLI：退出码（clean=0 / error=1 / --strict+warning=1）
- 仓库自带的 orders-recon.example.yml 必须 lint 干净
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.scenario_lint import lint_scenarios, main  # noqa: E402


def _write(dir_: Path, name: str, doc: dict) -> Path:
    p = dir_ / name
    p.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")
    return p


def _valid_doc(**over) -> dict:
    doc = {
        "id": "lint-ok",
        "name": "lint ok",
        "seed": 1,
        "tables": [{
            "name": "ods.t",
            "role": "source",
            "rows": 5,
            "columns": [
                {"name": "id", "type": "INT", "pk": True, "gen": "sequence"},
                {"name": "amt", "type": "DECIMAL(10,2)", "gen": "realistic"},
            ],
        }],
    }
    doc.update(over)
    return doc


# ─── 干净路径 ───────────────────────────────────────────────────────────────


def test_lint_valid_scenario_is_ok(tmp_path):
    _write(tmp_path, "ok.yml", _valid_doc())
    report = lint_scenarios(tmp_path)
    assert report.ok is True
    assert report.passed == 1
    assert report.failed == 0
    assert report.results[0].scenario_id == "lint-ok"
    assert report.results[0].errors == []
    assert report.results[0].warnings == []


def test_lint_empty_dir_returns_ok_empty(tmp_path):
    report = lint_scenarios(tmp_path)
    assert report.results == []
    assert report.ok is True


def test_lint_missing_dir_returns_empty(tmp_path):
    report = lint_scenarios(tmp_path / "nope")
    assert report.results == []


# ─── 1. load / DSL 校验 ─────────────────────────────────────────────────────


def test_lint_unknown_field_fails_load(tmp_path):
    doc = _valid_doc(bogus_field=123)  # extra='forbid' 顶层
    _write(tmp_path, "bad.yml", doc)
    report = lint_scenarios(tmp_path)
    assert report.ok is False
    assert any("load failed" in e for e in report.results[0].errors)


def test_lint_malformed_yaml_fails_load(tmp_path):
    (tmp_path / "broken.yml").write_text("id: x\n  name: : :\n", encoding="utf-8")
    report = lint_scenarios(tmp_path)
    assert report.ok is False
    assert report.results[0].errors


# ─── 2. generator 冒烟 ──────────────────────────────────────────────────────


def test_lint_unknown_distribution_kind_caught_by_generate(tmp_path):
    doc = _valid_doc(tables=[{
        "name": "t", "role": "source", "rows": 3,
        "columns": [{
            "name": "x", "type": "INT", "gen": "realistic",
            "dist_params": {"kind": "weibull"},
        }],
    }])
    _write(tmp_path, "baddist.yml", doc)
    report = lint_scenarios(tmp_path)
    assert report.ok is False
    assert any("generate_scenario raised" in e and "unknown distribution"
               in e for e in report.results[0].errors)


# ─── 3. 交叉引用 ────────────────────────────────────────────────────────────


def test_lint_anomaly_on_undefined_table_is_error(tmp_path):
    doc = _valid_doc(anomalies=[
        {"kind": "missing_rows", "table": "ods.nonexistent", "count": 1},
    ])
    _write(tmp_path, "a.yml", doc)
    report = lint_scenarios(tmp_path)
    assert report.ok is False
    assert any("未定义的表" in e for e in report.results[0].errors)


def test_lint_anomaly_on_unknown_column_is_warning(tmp_path):
    doc = _valid_doc(anomalies=[
        {"kind": "null_drift", "table": "ods.t", "column": "no_such_col",
         "count": 1},
    ])
    _write(tmp_path, "a.yml", doc)
    report = lint_scenarios(tmp_path)
    # column 不存在 → warning（不致命），scenario 本身仍 ok
    assert report.ok is True
    assert any("no_such_col" in w for w in report.results[0].warnings)


def test_lint_derives_from_missing_is_warning(tmp_path):
    doc = _valid_doc(tables=[
        {"name": "ods.t", "role": "source", "rows": 3,
         "columns": [{"name": "id", "type": "INT", "pk": True, "gen": "sequence"}]},
        {"name": "dwd.t", "role": "target", "rows": 3,
         "derives_from": "ods.ghost"},
    ])
    _write(tmp_path, "d.yml", doc)
    report = lint_scenarios(tmp_path)
    assert report.ok is True
    assert any("derives_from" in w and "ods.ghost" in w
               for w in report.results[0].warnings)


def test_lint_column_override_unknown_from_is_warning(tmp_path):
    doc = _valid_doc(tables=[
        {"name": "ods.t", "role": "source", "rows": 3,
         "columns": [{"name": "id", "type": "INT", "pk": True, "gen": "sequence"}]},
        {"name": "dwd.t", "role": "target", "rows": 3,
         "derives_from": "ods.t",
         "column_overrides": [{"from": "ghost_col", "rename": "x"}]},
    ])
    _write(tmp_path, "co.yml", doc)
    report = lint_scenarios(tmp_path)
    assert report.ok is True
    assert any("column_override" in w and "ghost_col" in w
               for w in report.results[0].warnings)


def test_lint_compare_task_unknown_table_is_warning(tmp_path):
    doc = _valid_doc(workloads=[
        {"kind": "compare_task", "name": "cmp", "source": "ods.t",
         "target": "dwd.ghost", "keys": ["id"]},
    ])
    _write(tmp_path, "w.yml", doc)
    report = lint_scenarios(tmp_path)
    assert report.ok is True
    assert any("dwd.ghost" in w for w in report.results[0].warnings)


# ─── 4. 模板变量 ────────────────────────────────────────────────────────────


def test_lint_unresolved_template_var_is_warning(tmp_path):
    doc = _valid_doc(
        variables={"known": "2026-01-01"},
        workloads=[{
            "kind": "lineage_script", "name": "etl",
            "sql": "SELECT * FROM ods.t WHERE dt > '{{known}}' AND x = {{unknown}}",
        }],
    )
    _write(tmp_path, "tpl.yml", doc)
    report = lint_scenarios(tmp_path)
    assert report.ok is True
    warns = report.results[0].warnings
    assert any("unknown" in w for w in warns)
    # known 变量已定义，不该出现在警告里
    assert not any("'known'" in w for w in warns)


def test_lint_fully_resolved_template_no_warning(tmp_path):
    doc = _valid_doc(
        variables={"cutoff": "2026-01-01"},
        workloads=[{
            "kind": "lineage_script", "name": "etl",
            "sql": "SELECT * FROM ods.t WHERE dt > '{{cutoff}}'",
        }],
    )
    _write(tmp_path, "tpl.yml", doc)
    report = lint_scenarios(tmp_path)
    assert report.results[0].warnings == []


# ─── CLI main() ─────────────────────────────────────────────────────────────


def test_main_returns_0_on_clean(tmp_path, capsys):
    _write(tmp_path, "ok.yml", _valid_doc())
    code = main(["--dir", str(tmp_path)])
    assert code == 0
    assert "1 ok" in capsys.readouterr().out


def test_main_returns_1_on_error(tmp_path):
    _write(tmp_path, "bad.yml", _valid_doc(
        anomalies=[{"kind": "extra_rows", "table": "ghost", "count": 1}]))
    assert main(["--dir", str(tmp_path)]) == 1


def test_main_strict_treats_warning_as_failure(tmp_path):
    _write(tmp_path, "warn.yml", _valid_doc(
        anomalies=[{"kind": "null_drift", "table": "ods.t",
                    "column": "ghost", "count": 1}]))
    # 默认 warning 不致命
    assert main(["--dir", str(tmp_path)]) == 0
    # --strict 下 warning 算失败
    assert main(["--dir", str(tmp_path), "--strict"]) == 1


def test_main_missing_dir_returns_1(tmp_path):
    assert main(["--dir", str(tmp_path / "nope")]) == 1


def test_main_empty_dir_returns_0(tmp_path, capsys):
    code = main(["--dir", str(tmp_path)])
    assert code == 0
    assert "nothing to lint" in capsys.readouterr().out


# ─── 仓库自带 example 必须干净 ──────────────────────────────────────────────


def test_repo_example_scenario_lints_clean():
    example_dir = _ROOT / "config" / "scenarios"
    report = lint_scenarios(example_dir)
    assert report.results, "config/scenarios/ 下应至少有一个 example yml"
    for r in report.results:
        assert r.ok, f"{r.file} lint 失败: {r.errors}"
        assert not r.warnings, f"{r.file} 有警告: {r.warnings}"
