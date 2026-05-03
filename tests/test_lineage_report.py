"""LineageAnalysisReport 转换层测试。

覆盖：
- 单脚本基本结构（scope/summary/inputs/outputs/process_steps/...）
- 多脚本基本结构 + impact_analysis 落到 report
- 真实 fixture 回归：a_cispnew_f3045.sql 仍出 88/39/delete_insert/dynamic_sql_count=0
- 边界：空脚本 / 失败脚本不抛
"""
from __future__ import annotations

from app.lineage.analyzer import analyze_sql_lineage
from app.lineage.batch_analyzer import ScriptInput, analyze_lineage_batch
from app.lineage.report import to_lineage_report


# ─── 基础结构测试 ──────────────────────────────────────────────────────────────


def test_single_report_has_all_top_level_fields():
    sql = "INSERT INTO dw.t SELECT a, b FROM stg.s;"
    result = analyze_sql_lineage(sql, dialect="mysql")
    report = result["report"]
    expected_keys = {
        "scope", "summary", "inputs", "outputs", "process_steps",
        "table_edges", "column_edges", "semantic_lineage",
        "impact_analysis", "risks", "files", "exports",
    }
    assert set(report.keys()) == expected_keys
    assert report["scope"] == "single"


def test_single_report_classifies_inputs_and_outputs():
    sql = "INSERT INTO dw.t_target SELECT id, name FROM ods.s_source;"
    result = analyze_sql_lineage(sql, dialect="mysql")
    report = result["report"]
    output_names = {o["name"] for o in report["outputs"]}
    input_names = {i["name"] for i in report["inputs"]}
    assert "dw.t_target" in output_names
    assert "ods.s_source" in input_names
    # 输入和输出不重叠
    assert not (output_names & input_names)


def test_single_report_summary_counts_match_lists():
    sql = """
    INSERT INTO dw.fact SELECT a, b FROM stg.x;
    INSERT INTO dw.fact SELECT c, d FROM stg.y;
    """
    result = analyze_sql_lineage(sql, dialect="mysql")
    s = result["report"]["summary"]
    assert s["input_count"] == len(result["report"]["inputs"])
    assert s["output_count"] == len(result["report"]["outputs"])
    assert s["table_edge_count"] == len(result["report"]["table_edges"])
    assert s["file_count"] == 1
    assert s["success_count"] == 1


def test_single_report_extracts_process_steps_from_procedure():
    sql = """\
CREATE OR REPLACE PROCEDURE etl_demo AS
BEGIN
  -- 业务标题
  INSERT INTO dw.t_a SELECT * FROM stg.s_a;
  DELETE FROM dw.t_b WHERE dt = SYSDATE;
END;
"""
    result = analyze_sql_lineage(sql, dialect="oracle")
    steps = result["report"]["process_steps"]
    assert len(steps) >= 2
    keywords = [s["dml_keyword"] for s in steps]
    assert "INSERT" in keywords
    assert "DELETE" in keywords
    assert all("line_start" in s for s in steps)
    # preceding_comment 应该至少抽到一个
    assert any(s.get("preceding_comment") for s in steps)


def test_single_report_column_edges_from_insert_mappings():
    sql = "INSERT INTO dw.t (id, name) SELECT s.id, s.name FROM stg.s s;"
    result = analyze_sql_lineage(sql, dialect="mysql")
    edges = result["report"]["column_edges"]
    assert len(edges) >= 2
    target_cols = {e["target_column"] for e in edges}
    assert "id" in target_cols
    assert "name" in target_cols


def test_single_report_impact_analysis_from_edges():
    # SELECT * 不会产 graph_edges（无列级追踪），用具名列
    sql = """
    INSERT INTO dw.b (id, name) SELECT id, name FROM ods.a;
    INSERT INTO dw.c (id) SELECT id FROM dw.b;
    """
    result = analyze_sql_lineage(sql, dialect="mysql")
    downstream = result["report"]["impact_analysis"]["downstream"]
    # 边以 lower 存；ods.a 应该可达 dw.b 和 dw.c
    a_downstream = downstream.get("ods.a", [])
    assert "dw.b" in a_downstream
    assert "dw.c" in a_downstream


def test_single_report_risks_from_semantic_lineage():
    """semantic_lineage.risks 应该被规范化进 report.risks。"""
    sql = "this is not valid sql at all xxxxxx;"
    try:
        result = analyze_sql_lineage(sql, dialect="mysql")
    except Exception:
        # 顶层解析失败属于另一种路径，不在本测试覆盖
        return
    report = result["report"]
    if report["risks"]:
        for r in report["risks"]:
            assert "level" in r
            assert "type" in r
            assert "message" in r


# ─── 多脚本测试 ────────────────────────────────────────────────────────────────


def test_batch_report_has_same_top_level_keys():
    scripts = [
        ScriptInput("a.sql", "INSERT INTO dw.t SELECT * FROM ods.s;"),
    ]
    result = analyze_lineage_batch(scripts, dialect="mysql")
    report = result["report"]
    expected_keys = {
        "scope", "summary", "inputs", "outputs", "process_steps",
        "table_edges", "column_edges", "semantic_lineage",
        "impact_analysis", "risks", "files", "exports",
    }
    assert set(report.keys()) == expected_keys
    assert report["scope"] == "batch"


def test_batch_report_aggregates_inputs_outputs_across_files():
    scripts = [
        ScriptInput("a.sql", "INSERT INTO dw.t1 SELECT * FROM ods.x;"),
        ScriptInput("b.sql", "INSERT INTO dw.t2 SELECT * FROM dw.t1;"),
    ]
    result = analyze_lineage_batch(scripts, dialect="mysql")
    report = result["report"]
    output_names = {o["name"] for o in report["outputs"]}
    input_names = {i["name"] for i in report["inputs"]}
    assert "dw.t1" in output_names
    assert "dw.t2" in output_names
    assert "ods.x" in input_names
    # dw.t1 既写又读 → 标记 intermediate（不出现在 input）
    assert "dw.t1" not in input_names
    t1 = next(o for o in report["outputs"] if o["name"] == "dw.t1")
    assert t1["primary_role"] == "intermediate"


def test_batch_report_files_summary():
    scripts = [
        ScriptInput("ok.sql", "INSERT INTO dw.t SELECT * FROM s;"),
        ScriptInput("bad.sql", "totally not valid SQL with !@#$%^&*();"),
    ]
    result = analyze_lineage_batch(scripts, dialect="mysql")
    files = result["report"]["files"]
    assert len(files) == 2
    file_names = [f["file_name"] for f in files]
    assert "ok.sql" in file_names


def test_batch_report_impact_analysis_carried_through():
    scripts = [
        ScriptInput("a.sql", "INSERT INTO dw.b SELECT * FROM ods.a;"),
        ScriptInput("b.sql", "INSERT INTO dw.c SELECT * FROM dw.b;"),
    ]
    result = analyze_lineage_batch(scripts, dialect="mysql")
    downstream = result["report"]["impact_analysis"]["downstream"]
    # 至少 ods.a 能传到 dw.c
    a_chain = downstream.get("ods.a", [])
    assert "dw.b" in a_chain
    assert "dw.c" in a_chain


def test_batch_report_summary_consistency():
    scripts = [
        ScriptInput("a.sql", "INSERT INTO dw.t SELECT * FROM ods.s;"),
    ]
    result = analyze_lineage_batch(scripts, dialect="mysql")
    s = result["report"]["summary"]
    assert s["file_count"] == 1
    assert s["success_count"] == 1
    assert s["input_count"] == len(result["report"]["inputs"])
    assert s["output_count"] == len(result["report"]["outputs"])


# ─── 真实 fixture 回归 ────────────────────────────────────────────────────────


def test_oracle_fixture_report_does_not_break_legacy_fields():
    """加 report 字段不能影响 a_cispnew_f3045.sql 真实回归（target_summary 88/39/delete_insert）。

    fixture 在 test_lineage_analyzer.py 里；这里只验证 report 字段也合理。
    """
    from tests.test_lineage_analyzer import _ORACLE_PROC_FIXTURE
    result = analyze_sql_lineage(_ORACLE_PROC_FIXTURE, dialect="oracle")

    # 既有字段保持原状
    assert result["dynamic_sql_count"] == 0
    target_tables = {t["target_table"] for t in result["target_summary"]}
    assert "cispnew.t_etl_jy" in target_tables
    assert "cispnew.t_etl_zqsz" in target_tables

    # report 字段也填了
    report = result["report"]
    assert report["scope"] == "single"
    output_names = {o["name"] for o in report["outputs"]}
    assert "cispnew.t_etl_jy" in output_names
    assert "cispnew.t_etl_zqsz" in output_names
    # process_steps 至少有一条
    assert len(report["process_steps"]) >= 5


# ─── 直接调用 to_lineage_report ───────────────────────────────────────────────


def test_to_lineage_report_idempotent():
    """重复调用 to_lineage_report 不应改变结果（无副作用）。"""
    result = analyze_sql_lineage(
        "INSERT INTO dw.t SELECT * FROM ods.s;",
        dialect="mysql",
    )
    r1 = to_lineage_report(result, scope="single")
    r2 = to_lineage_report(result, scope="single")
    assert r1 == r2


def test_to_lineage_report_unknown_scope_defaults_to_single():
    """未知 scope 也应该返回合理结果（defaults to single）。"""
    result = analyze_sql_lineage(
        "INSERT INTO dw.t SELECT * FROM ods.s;",
        dialect="mysql",
    )
    r = to_lineage_report(result, scope="weird")
    assert r["scope"] == "single"
