from __future__ import annotations

import pytest

from app.lineage._common import is_alias_reference, normalize_table_name, raw_sql_aliases, unique_strings
from app.lineage.analyzer import analyze_sql_lineage
from app.lineage.batch_analyzer import ScriptInput, analyze_lineage_batch
from app.services.schema_introspection import _normalize_names, _rows_to_schema, _schema_query


# ─── _common utilities ────────────────────────────────────────────────────────

def test_normalize_table_name_lowercase():
    assert normalize_table_name("MyTable") == "mytable"


def test_normalize_table_name_strips_double_quotes():
    assert normalize_table_name('"MyTable"') == "mytable"


def test_normalize_table_name_strips_backticks():
    assert normalize_table_name("`MyTable`") == "mytable"


def test_normalize_table_name_strips_brackets():
    assert normalize_table_name("[MyTable]") == "mytable"


def test_normalize_table_name_preserves_dot():
    assert normalize_table_name("schema.table") == "schema.table"


def test_unique_strings_deduplicates():
    assert unique_strings(["a", "b", "a", "c"]) == ["a", "b", "c"]


def test_unique_strings_drops_empty():
    assert unique_strings(["a", "", "b"]) == ["a", "b"]


def test_unique_strings_empty_input():
    assert unique_strings([]) == []


def test_raw_sql_aliases_from_join():
    aliases = raw_sql_aliases("SELECT * FROM employees e JOIN departments d ON e.dept_id = d.id")
    assert "e" in aliases
    assert "d" in aliases


def test_raw_sql_aliases_with_as():
    aliases = raw_sql_aliases("SELECT * FROM orders AS o")
    assert "o" in aliases


def test_raw_sql_aliases_cte():
    aliases = raw_sql_aliases("WITH base AS (SELECT 1) SELECT * FROM base")
    assert "base" in aliases


def test_is_alias_reference_matches():
    assert is_alias_reference("e", {"e", "d"})


def test_is_alias_reference_no_match():
    assert not is_alias_reference("employees", {"e", "d"})


def test_is_alias_reference_dot_not_alias():
    assert not is_alias_reference("schema.table", {"schema.table"})


def test_schema_rows_to_schema_includes_qualified_and_unqualified_names():
    schema = _rows_to_schema(
        [
            {"TABLE_SCHEMA": "dw", "TABLE_NAME": "orders", "COLUMN_NAME": "id"},
            {"TABLE_SCHEMA": "dw", "TABLE_NAME": "orders", "COLUMN_NAME": "amount"},
        ]
    )
    assert schema["orders"] == ["id", "amount"]
    assert schema["dw.orders"] == ["id", "amount"]


def test_mysql_schema_query_uses_configured_database():
    source = type("Source", (), {"db_type": "MySQL", "database": "dw", "username": "u", "extra": {}})()
    sql = _schema_query(source)
    assert "information_schema.columns" in sql
    assert "table_schema = 'dw'" in sql


def test_schema_query_supports_table_scope_filter():
    source = type("Source", (), {"db_type": "MySQL", "database": "dw", "username": "u", "extra": {}})()
    sql = _schema_query(source, table_filter="ods_%", include_tables=["orders", "dw.users"])
    assert "table_name like 'ods_%'" in sql
    assert "table_name in ('orders', 'users')" in sql


def test_schema_query_supports_oceanbase_oracle_mode():
    source = type("Source", (), {"db_type": "MySQL", "database": "dw", "username": "obuser", "extra": {}})()
    sql = _schema_query(source, schema_dialect="ob_oracle", schema_name="app")
    assert "all_tab_columns" in sql
    assert "owner = 'APP'" in sql


def test_normalize_schema_table_names_for_scope():
    assert _normalize_names(["dw.orders", "`users`", "orders"]) == ["orders", "users"]


# ─── analyze_sql_lineage: basic ───────────────────────────────────────────────

def test_simple_select_tables():
    result = analyze_sql_lineage("SELECT id, name FROM users")
    assert any(t["table"] == "users" for t in result["tables"])


def test_result_has_parse_errors_key():
    result = analyze_sql_lineage("SELECT 1")
    assert isinstance(result["parse_errors"], list)


def test_result_has_dynamic_sql_segments_key():
    result = analyze_sql_lineage("SELECT 1")
    assert isinstance(result["dynamic_sql_segments"], list)


def test_insert_select_graph_edge():
    sql = "INSERT INTO target_table SELECT id, name FROM source_table"
    result = analyze_sql_lineage(sql)
    edges = result["graph_edges"]
    assert any(e["source_table"] == "source_table" and e["target_table"] == "target_table" for e in edges)


def test_insert_select_mappings_columns():
    sql = "INSERT INTO t (a, b) SELECT x, y FROM s"
    result = analyze_sql_lineage(sql)
    mappings = result["insert_mappings"]
    assert len(mappings) == 2
    assert mappings[0]["target_column"] == "a"
    assert mappings[1]["target_column"] == "b"
    assert all(m["source_tables"] == ["s"] for m in mappings)


def test_insert_mappings_have_dml_type():
    sql = "INSERT INTO t SELECT x FROM s"
    result = analyze_sql_lineage(sql)
    assert all(m.get("dml_type") == "INSERT" for m in result["insert_mappings"])


def test_statement_count():
    sql = "INSERT INTO t SELECT * FROM s1; INSERT INTO t SELECT * FROM s2"
    result = analyze_sql_lineage(sql)
    assert result["statement_count"] == 2


def test_multi_source_edges():
    sql = "INSERT INTO c SELECT a.x, b.y FROM a JOIN b ON a.id = b.id"
    result = analyze_sql_lineage(sql)
    sources = {e["source_table"] for e in result["graph_edges"] if e["target_table"] == "c"}
    assert "a" in sources
    assert "b" in sources


def test_graph_edges_no_duplicates():
    sql = "INSERT INTO t SELECT a.x, a.y FROM a"
    result = analyze_sql_lineage(sql)
    edges = result["graph_edges"]
    keys = [(e["source_table"], e["target_table"]) for e in edges]
    assert len(keys) == len(set(keys))


def test_star_expansion_with_schema():
    sql = "INSERT INTO t SELECT * FROM s"
    result = analyze_sql_lineage(sql, schema={"s": ["id", "name", "age"]})
    output_cols = [c["output_column"] for c in result["columns"]]
    assert "id" in output_cols
    assert "name" in output_cols
    assert "age" in output_cols


def test_unqualified_column_resolves_unique_schema_table():
    sql = "INSERT INTO rpt SELECT amount FROM orders o JOIN users u ON o.user_id = u.id"
    result = analyze_sql_lineage(sql, schema={"orders": ["id", "amount", "user_id"], "users": ["id", "name"]})
    mapping = result["insert_mappings"][0]
    assert mapping["source_tables"] == ["orders"]
    assert mapping["source_columns"] == ["orders.amount"]
    assert mapping["confidence"] == "medium"


def test_unqualified_column_warns_when_schema_ambiguous():
    sql = "INSERT INTO rpt SELECT id FROM orders o JOIN users u ON o.user_id = u.id"
    result = analyze_sql_lineage(sql, schema={"orders": ["id", "amount", "user_id"], "users": ["id", "name"]})
    mapping = result["insert_mappings"][0]
    assert set(mapping["source_tables"]) == {"orders", "users"}
    assert mapping["confidence"] == "low"
    assert any(w["type"] == "字段歧义" for w in result["warnings"])


def test_graph_edges_include_context():
    sql = "INSERT INTO rpt (amount) SELECT amount FROM orders"
    result = analyze_sql_lineage(sql, schema={"orders": ["amount"]})
    edge = result["graph_edges"][0]
    assert edge["statement_index"] == 1
    assert edge["edge_type"] == "INSERT"
    assert edge["target_columns"] == ["amount"]
    assert edge["confidence"] == "high"


def test_create_table_as_select_lineage():
    sql = "CREATE TABLE rpt AS SELECT id, name FROM source_table"
    result = analyze_sql_lineage(sql)
    assert any(e["source_table"] == "source_table" and e["target_table"] == "rpt" for e in result["graph_edges"])
    assert all(m["dml_type"] == "CREATE_TABLE_AS" for m in result["insert_mappings"])
    assert [m["target_column"] for m in result["insert_mappings"]] == ["id", "name"]


def test_create_or_replace_table_as_select_lineage():
    sql = "CREATE OR REPLACE TABLE rpt AS SELECT id FROM source_table"
    result = analyze_sql_lineage(sql)
    assert result["insert_mappings"][0]["dml_type"] == "CREATE_OR_REPLACE_TABLE_AS"
    assert result["graph_edges"][0]["edge_type"] == "CREATE_OR_REPLACE_TABLE_AS"


def test_insert_overwrite_keeps_dml_type():
    sql = "INSERT OVERWRITE TABLE rpt SELECT id FROM source_table"
    result = analyze_sql_lineage(sql)
    assert result["insert_mappings"][0]["dml_type"] == "INSERT_OVERWRITE"
    assert result["graph_edges"][0]["edge_type"] == "INSERT_OVERWRITE"


def test_replace_into_select_lineage():
    sql = "REPLACE INTO rpt SELECT id FROM source_table"
    result = analyze_sql_lineage(sql)
    assert result["insert_mappings"][0]["dml_type"] == "REPLACE"
    assert result["statements"][0]["sql"] == sql
    assert any(e["source_table"] == "source_table" and e["target_table"] == "rpt" for e in result["graph_edges"])


# ─── CTE and subquery ────────────────────────────────────────────────────────

def test_cte_not_in_physical_tables():
    sql = """
    WITH base AS (SELECT id FROM raw_users)
    INSERT INTO users SELECT id FROM base
    """
    result = analyze_sql_lineage(sql)
    table_names = {t["table"] for t in result["tables"]}
    assert "raw_users" in table_names
    assert "base" not in table_names


def test_cte_lineage_traces_to_source():
    sql = """
    WITH base AS (SELECT id FROM raw_users)
    INSERT INTO users SELECT id FROM base
    """
    result = analyze_sql_lineage(sql)
    edges = result["graph_edges"]
    assert any(e["source_table"] == "raw_users" and e["target_table"] == "users" for e in edges)


def test_subquery_lineage():
    sql = """
    INSERT INTO summary
    SELECT dept, total
    FROM (SELECT dept_id AS dept, SUM(salary) AS total FROM employees GROUP BY dept_id) sub
    """
    result = analyze_sql_lineage(sql)
    edges = result["graph_edges"]
    assert any(e["source_table"] == "employees" and e["target_table"] == "summary" for e in edges)


# ─── Dynamic SQL detection ───────────────────────────────────────────────────

def test_dynamic_sql_execute_immediate():
    sql = "EXECUTE IMMEDIATE 'INSERT INTO t SELECT id FROM src'"
    result = analyze_sql_lineage(sql)
    assert result["dynamic_sql_count"] >= 1
    assert any(s["confidence"] == "high" for s in result["dynamic_sql_segments"])


def test_dynamic_sql_string_literal():
    sql = "DECLARE @q NVARCHAR(MAX) = 'INSERT INTO t SELECT id FROM src'; EXEC sp_executesql @q"
    result = analyze_sql_lineage(sql)
    assert result["dynamic_sql_count"] >= 1


def test_dynamic_sql_segment_has_sql_field():
    sql = "EXECUTE IMMEDIATE 'INSERT INTO t SELECT id FROM src'"
    result = analyze_sql_lineage(sql)
    segs = result["dynamic_sql_segments"]
    assert all("sql" in s and "source" in s and "confidence" in s for s in segs)


# ─── UPDATE lineage ──────────────────────────────────────────────────────────

def test_update_from_creates_edge():
    sql = """
    UPDATE employees e
    SET salary = d.budget
    FROM departments d
    WHERE e.dept_id = d.id
    """
    result = analyze_sql_lineage(sql)
    edges = result["graph_edges"]
    assert any(e["target_table"] == "employees" for e in edges)
    assert any(e["source_table"] == "departments" for e in edges)


def test_update_mappings_have_dml_type():
    sql = "UPDATE employees e SET salary = d.budget FROM departments d WHERE e.dept_id = d.id"
    result = analyze_sql_lineage(sql)
    update_mappings = [m for m in result["insert_mappings"] if m.get("dml_type") == "UPDATE"]
    assert len(update_mappings) >= 1


def test_update_self_no_cross_table_edge():
    sql = "UPDATE t SET x = x + 1"
    result = analyze_sql_lineage(sql)
    edges = result["graph_edges"]
    assert not any(e["source_table"] == e["target_table"] for e in edges)


# ─── MERGE lineage ───────────────────────────────────────────────────────────

def test_merge_creates_edge():
    sql = """
    MERGE INTO target t
    USING source s ON t.id = s.id
    WHEN MATCHED THEN UPDATE SET t.name = s.name
    WHEN NOT MATCHED THEN INSERT (id, name) VALUES (s.id, s.name)
    """
    result = analyze_sql_lineage(sql)
    edges = result["graph_edges"]
    assert any(e["source_table"] == "source" and e["target_table"] == "target" for e in edges)


def test_merge_mappings_have_dml_type():
    sql = """
    MERGE INTO target t
    USING source s ON t.id = s.id
    WHEN MATCHED THEN UPDATE SET t.name = s.name
    """
    result = analyze_sql_lineage(sql)
    merge_mappings = [m for m in result["insert_mappings"] if m.get("dml_type") == "MERGE"]
    assert len(merge_mappings) >= 1


# ─── analyze_lineage_batch ────────────────────────────────────────────────────

def test_batch_basic_success():
    scripts = [ScriptInput("a.sql", "INSERT INTO target SELECT id FROM source")]
    result = analyze_lineage_batch(scripts)
    assert result["file_count"] == 1
    assert result["files"][0]["status"] == "成功"
    assert "source" in result["files"][0]["read_tables"]
    assert "target" in result["files"][0]["write_tables"]


def test_batch_summary_counts():
    scripts = [
        ScriptInput("s1.sql", "INSERT INTO b SELECT * FROM a"),
        ScriptInput("s2.sql", "INSERT INTO c SELECT * FROM b"),
    ]
    result = analyze_lineage_batch(scripts)
    assert result["summary"]["files"] == 2
    assert result["summary"]["success_files"] == 2
    assert result["summary"]["failed_files"] == 0


def test_batch_table_edges():
    scripts = [ScriptInput("s.sql", "INSERT INTO b SELECT * FROM a")]
    result = analyze_lineage_batch(scripts)
    edges = result["table_edges"]
    assert any(e["source_table"] == "a" and e["target_table"] == "b" for e in edges)


def test_batch_script_edges():
    scripts = [
        ScriptInput("producer.sql", "INSERT INTO mid SELECT * FROM raw"),
        ScriptInput("consumer.sql", "INSERT INTO final SELECT * FROM mid"),
    ]
    result = analyze_lineage_batch(scripts)
    script_edges = result["script_edges"]
    assert any(
        e["producer_file"] == "producer.sql" and e["consumer_file"] == "consumer.sql"
        for e in script_edges
    )


def test_batch_dag_topological_order():
    scripts = [
        ScriptInput("producer.sql", "INSERT INTO mid SELECT * FROM raw"),
        ScriptInput("consumer.sql", "INSERT INTO final SELECT * FROM mid"),
    ]
    result = analyze_lineage_batch(scripts)
    assert result["dag"]["topological_order"] == ["producer.sql", "consumer.sql"]
    assert result["dag"]["has_cycle"] is False
    assert result["dag"]["downstream"]["producer.sql"] == ["consumer.sql"]
    assert result["dag"]["upstream"]["consumer.sql"] == ["producer.sql"]


def test_batch_dag_detects_cycle():
    scripts = [
        ScriptInput("a.sql", "INSERT INTO t1 SELECT * FROM t2"),
        ScriptInput("b.sql", "INSERT INTO t2 SELECT * FROM t1"),
    ]
    result = analyze_lineage_batch(scripts)
    assert result["dag"]["has_cycle"] is True
    assert any(cycle[0] == cycle[-1] for cycle in result["dag"]["cycles"])
    assert any(w["type"] == "脚本依赖环" for w in result["warnings"])


def test_batch_dag_write_conflict_severity_high_when_read_downstream():
    scripts = [
        ScriptInput("w1.sql", "INSERT INTO shared SELECT * FROM a"),
        ScriptInput("w2.sql", "INSERT INTO shared SELECT * FROM b"),
        ScriptInput("reader.sql", "INSERT INTO final SELECT * FROM shared"),
    ]
    result = analyze_lineage_batch(scripts)
    conflict = result["dag"]["write_conflicts"][0]
    assert conflict["table"] == "shared"
    assert set(conflict["writers"]) == {"w1.sql", "w2.sql"}
    assert conflict["severity"] == "high"
    assert result["summary"]["write_conflicts"] == 1


def test_batch_impact_analysis_direct():
    scripts = [ScriptInput("s.sql", "INSERT INTO b SELECT * FROM a")]
    result = analyze_lineage_batch(scripts)
    impact = result["impact_analysis"]
    downstream = impact.get("a", [])
    assert any(t.lower() == "b" for t in downstream)


def test_batch_impact_analysis_transitive():
    scripts = [
        ScriptInput("s1.sql", "INSERT INTO b SELECT * FROM a"),
        ScriptInput("s2.sql", "INSERT INTO c SELECT * FROM b"),
    ]
    result = analyze_lineage_batch(scripts)
    impact = result["impact_analysis"]
    downstream = impact.get("a", [])
    assert any(t.lower() == "b" for t in downstream)
    assert any(t.lower() == "c" for t in downstream)


def test_batch_global_warnings_multi_writer():
    scripts = [
        ScriptInput("s1.sql", "INSERT INTO shared SELECT * FROM a"),
        ScriptInput("s2.sql", "INSERT INTO shared SELECT * FROM b"),
    ]
    result = analyze_lineage_batch(scripts)
    global_warnings = [w for w in result["warnings"] if w.get("file_name") == "全局"]
    assert any(w["type"] == "多脚本写同一目标表" for w in global_warnings)


def test_batch_select_star_warning():
    scripts = [ScriptInput("s.sql", "INSERT INTO t SELECT * FROM s")]
    result = analyze_lineage_batch(scripts)
    file_warnings = result["files"][0]["warnings"]
    assert any(w["type"] == "SELECT *" for w in file_warnings)


def test_batch_impact_analysis_present():
    scripts = [ScriptInput("s.sql", "INSERT INTO b SELECT * FROM a")]
    result = analyze_lineage_batch(scripts)
    assert "impact_analysis" in result
    assert isinstance(result["impact_analysis"], dict)
