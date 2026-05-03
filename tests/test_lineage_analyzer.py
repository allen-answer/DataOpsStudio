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
    assert "from all_tab_columns" in sql
    assert "owner = 'APP'" in sql


def test_schema_normalize_names_deduplicates_and_strips_schema():
    assert _normalize_names(["dw.orders", '"orders"', "users"]) == ["orders", "users"]


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
    # `EXEC sp_executesql N'<literal>'` 走精确 keyword 路径，应该被识别为动态 SQL。
    sql = "EXEC sp_executesql N'INSERT INTO t SELECT id FROM src'"
    result = analyze_sql_lineage(sql)
    assert result["dynamic_sql_count"] >= 1


def test_select_with_long_string_literal_not_treated_as_dynamic_sql():
    # 普通 SELECT 里出现的 SQL-shaped 字符串字面量不应该被报为动态 SQL —— 这是
    # 之前 string_literal 兜底匹配产生海量假阳的根源（参见 a_cispnew_f3045.sql 的
    # 324 段误报）。删掉兜底后预期 dynamic_sql_count == 0。
    sql = "SELECT 'INSERT INTO dw.f SELECT * FROM ods.a' AS dummy_sql FROM dual"
    result = analyze_sql_lineage(sql, dialect="oracle")
    assert result["dynamic_sql_count"] == 0


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


# ─── 方言路由 ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "alias,expected_dialect_works",
    [
        ("mysql", True),
        ("oracle", True),
        ("dm", True),
        ("dameng", True),
        ("ob_mysql", True),
        ("ob_oracle", True),
        ("oceanbase", True),
        ("oceanbase_mysql", True),
        ("oceanbase_oracle", True),
    ],
)
def test_dialect_aliases_resolve(alias, expected_dialect_works):
    """Each alias should be accepted without raising."""
    sql = "INSERT INTO dw.t SELECT id FROM stg.s"
    result = analyze_sql_lineage(sql, dialect=alias)
    assert result["statement_count"] >= 1


def test_dialect_unknown_passthrough_does_not_raise():
    # Unknown dialect should fall back to default parsing.
    sql = "INSERT INTO dw.t SELECT id FROM stg.s"
    result = analyze_sql_lineage(sql, dialect="postgres")
    assert result["statement_count"] >= 1


# ─── 存储过程深度解析 ──────────────────────────────────────────────────────────

def test_oracle_procedure_extracts_inner_inserts():
    sql = """
    CREATE OR REPLACE PROCEDURE etl_orders AS
    BEGIN
      INSERT INTO dw.orders_t SELECT id, amount FROM stg.orders WHERE dt = SYSDATE;
      MERGE INTO dw.users t USING stg.users s ON (t.id = s.id)
        WHEN MATCHED THEN UPDATE SET t.name = s.name;
    END;
    """
    result = analyze_sql_lineage(sql, dialect="oracle")
    targets = {m["target_table"] for m in result["insert_mappings"]}
    assert "dw.orders_t" in targets
    assert "dw.users" in targets
    assert any(seg["procedure_name"] == "etl_orders" for seg in result["procedure_segments"])


def test_mysql_procedure_extracts_inner_inserts():
    sql = """
    CREATE PROCEDURE p_etl()
    BEGIN
      INSERT INTO dw.orders_t SELECT id, amount FROM stg.orders;
      DELETE FROM stg.staging WHERE processed = 1;
    END;
    """
    result = analyze_sql_lineage(sql, dialect="mysql")
    targets = {m["target_table"] for m in result["insert_mappings"]}
    assert "dw.orders_t" in targets
    assert any("p_etl" in seg["procedure_name"] for seg in result["procedure_segments"])


def test_procedure_with_control_flow_skipped():
    sql = """
    CREATE OR REPLACE PROCEDURE p_cond AS
    BEGIN
      IF :p_mode = 'FULL' THEN
        INSERT INTO dw.t SELECT * FROM stg.s;
      END IF;
    END;
    """
    result = analyze_sql_lineage(sql, dialect="oracle")
    assert any(m["target_table"] == "dw.t" for m in result["insert_mappings"])


def test_procedure_warning_emitted():
    sql = """
    CREATE PROCEDURE p() BEGIN INSERT INTO t SELECT * FROM s; END;
    """
    result = analyze_sql_lineage(sql, dialect="mysql")
    assert any(w["type"] == "存储过程" for w in result["warnings"])


# ─── 临时表中间节点 ────────────────────────────────────────────────────────────

def test_temp_table_marked_is_temp():
    sql = "CREATE TEMPORARY TABLE tmp_orders AS SELECT id FROM stg.orders"
    result = analyze_sql_lineage(sql, dialect="mysql")
    assert any(m.get("is_temp") for m in result["insert_mappings"])


def test_oracle_global_temporary_table_marked_is_temp():
    sql = "CREATE GLOBAL TEMPORARY TABLE tmp_users AS SELECT id FROM stg.users"
    result = analyze_sql_lineage(sql, dialect="oracle")
    assert any(m.get("is_temp") for m in result["insert_mappings"])


def test_temp_table_excluded_from_external_source_warnings():
    scripts = [
        ScriptInput("s1.sql", "CREATE TEMPORARY TABLE tmp AS SELECT id FROM stg.s"),
        ScriptInput("s2.sql", "INSERT INTO dw.final SELECT id FROM tmp"),
    ]
    result = analyze_lineage_batch(scripts, dialect="mysql")
    external_warnings = [w for w in result["warnings"] if w.get("type") == "外部源表"]
    external_messages = {w["message"] for w in external_warnings}
    assert "tmp" not in external_messages


# ─── 动态 SQL ─────────────────────────────────────────────────────────────────

def test_mysql_prepare_execute_extracts_dml():
    sql = """
    SET @sql := 'INSERT INTO dw.orders_t SELECT id FROM stg.orders WHERE dt = CURDATE()';
    PREPARE stmt FROM @sql;
    EXECUTE stmt;
    """
    result = analyze_sql_lineage(sql, dialect="mysql")
    sources = {tbl for m in result["insert_mappings"] for tbl in m.get("source_tables", [])}
    assert "stg.orders" in sources
    assert any(d["source"] == "prepare_var" for d in result["dynamic_sql_segments"])


def test_oracle_execute_immediate_var_concat():
    sql = """
    DECLARE
      v_sql VARCHAR2(2000);
    BEGIN
      v_sql := 'INSERT INTO dw.orders_t SELECT id FROM stg.orders WHERE dt = ' || :p_dt;
      EXECUTE IMMEDIATE v_sql;
    END;
    """
    result = analyze_sql_lineage(sql, dialect="oracle")
    sources = {tbl for m in result["insert_mappings"] for tbl in m.get("source_tables", [])}
    assert "stg.orders" in sources
    assert any(d["source"] == "var_concat" for d in result["dynamic_sql_segments"])


def test_oracle_execute_immediate_literal():
    sql = """
    BEGIN
      EXECUTE IMMEDIATE 'INSERT INTO dw.orders_t SELECT id FROM stg.orders';
    END;
    """
    result = analyze_sql_lineage(sql, dialect="oracle")
    sources = {tbl for m in result["insert_mappings"] for tbl in m.get("source_tables", [])}
    assert "stg.orders" in sources


# ─── 各方言 DML 形态覆盖 ───────────────────────────────────────────────────────

@pytest.mark.parametrize("dialect", ["mysql", "oracle", "dm", "ob_mysql", "ob_oracle"])
def test_ctas_all_dialects(dialect):
    sql = "CREATE TABLE dw.snapshot AS SELECT id, dt FROM stg.events"
    result = analyze_sql_lineage(sql, dialect=dialect)
    targets = {m["target_table"] for m in result["insert_mappings"]}
    assert "dw.snapshot" in targets


@pytest.mark.parametrize("dialect", ["mysql", "oracle", "dm", "ob_mysql", "ob_oracle"])
def test_insert_select_all_dialects(dialect):
    sql = "INSERT INTO dw.t SELECT id FROM stg.s"
    result = analyze_sql_lineage(sql, dialect=dialect)
    sources = {tbl for m in result["insert_mappings"] for tbl in m.get("source_tables", [])}
    assert "stg.s" in sources


@pytest.mark.parametrize("dialect", ["oracle", "dm", "ob_oracle"])
def test_merge_all_dialects(dialect):
    sql = """
    MERGE INTO dw.t tgt
    USING stg.s src ON (tgt.id = src.id)
    WHEN MATCHED THEN UPDATE SET tgt.v = src.v
    """
    result = analyze_sql_lineage(sql, dialect=dialect)
    sources = {tbl for m in result["insert_mappings"] for tbl in m.get("source_tables", [])}
    assert "stg.s" in sources


@pytest.mark.parametrize("dialect", ["mysql", "oracle", "dm", "ob_mysql"])
def test_update_with_subquery_all_dialects(dialect):
    sql = "UPDATE dw.t SET v = (SELECT v FROM stg.s WHERE id = dw.t.id) WHERE EXISTS (SELECT 1 FROM stg.s)"
    result = analyze_sql_lineage(sql, dialect=dialect)
    sources = {tbl for m in result["insert_mappings"] for tbl in m.get("source_tables", [])}
    assert "stg.s" in sources


# ─── target_summary 聚合（Phase 7 Track B 第 2 项）─────────────────────────────

def _summary_by(target_summary, table):
    matches = [s for s in target_summary if s["target_table"].lower() == table.lower()]
    assert matches, f"未在 target_summary 找到 {table}: {[s['target_table'] for s in target_summary]}"
    return matches[0]


def test_target_summary_aggregates_multiple_inserts():
    sql = """
    INSERT INTO dw.fact SELECT a, b FROM stg.a;
    INSERT INTO dw.fact SELECT c, d FROM stg.b;
    INSERT INTO dw.fact SELECT e, f FROM stg.c;
    """
    result = analyze_sql_lineage(sql, dialect="mysql")
    summary = _summary_by(result["target_summary"], "dw.fact")
    assert summary["insert_count"] == 3
    assert summary["update_count"] == 0
    assert summary["refresh_mode"] == "append"


def test_target_summary_truncate_insert_full_refresh():
    sql = """
    TRUNCATE TABLE dim.cust;
    INSERT INTO dim.cust SELECT id, name FROM stg.cust;
    """
    result = analyze_sql_lineage(sql, dialect="mysql")
    summary = _summary_by(result["target_summary"], "dim.cust")
    assert summary["truncate_before_insert"] is True
    assert summary["truncate_count"] == 1
    assert summary["refresh_mode"] == "truncate_insert"


def test_target_summary_delete_insert_full_refresh():
    sql = """
    DELETE FROM dwd.fact_order;
    INSERT INTO dwd.fact_order SELECT * FROM ods.order_log;
    """
    result = analyze_sql_lineage(sql, dialect="mysql")
    summary = _summary_by(result["target_summary"], "dwd.fact_order")
    assert summary["delete_before_insert"] is True
    assert summary["delete_count"] == 1
    assert summary["refresh_mode"] == "delete_insert"


def test_target_summary_delete_with_where_is_partial():
    sql = """
    DELETE FROM dwd.fact_order WHERE biz_date = '2025-01-01';
    INSERT INTO dwd.fact_order SELECT * FROM ods.order_log WHERE biz_date = '2025-01-01';
    """
    result = analyze_sql_lineage(sql, dialect="mysql")
    summary = _summary_by(result["target_summary"], "dwd.fact_order")
    assert summary["delete_before_insert"] is True
    assert summary["refresh_mode"] == "delete_insert_partial"


def test_target_summary_merge_only():
    sql = """
    MERGE INTO dwd.dim_user d
    USING stg.user_delta s ON d.id = s.id
    WHEN MATCHED THEN UPDATE SET d.name = s.name
    WHEN NOT MATCHED THEN INSERT (id, name) VALUES (s.id, s.name);
    """
    result = analyze_sql_lineage(sql, dialect="oracle")
    summary = _summary_by(result["target_summary"], "dwd.dim_user")
    assert summary["merge_count"] == 1
    assert summary["insert_count"] == 0
    assert summary["refresh_mode"] == "merge"


def test_target_summary_update_only():
    sql = "UPDATE dwd.fact SET status = '1' WHERE biz_date = '2025-01-01';"
    result = analyze_sql_lineage(sql, dialect="mysql")
    summary = _summary_by(result["target_summary"], "dwd.fact")
    assert summary["update_count"] == 1
    assert summary["refresh_mode"] == "update"


def test_target_summary_mixed_insert_and_update():
    sql = """
    INSERT INTO dwd.fact SELECT * FROM stg.fact;
    UPDATE dwd.fact SET flag = 1 WHERE flag IS NULL;
    """
    result = analyze_sql_lineage(sql, dialect="mysql")
    summary = _summary_by(result["target_summary"], "dwd.fact")
    assert summary["insert_count"] == 1
    assert summary["update_count"] == 1
    assert summary["refresh_mode"] == "mixed"


def test_target_summary_multiple_targets():
    sql = """
    INSERT INTO dw.a SELECT * FROM stg.a;
    INSERT INTO dw.b SELECT * FROM stg.b;
    INSERT INTO dw.a SELECT * FROM stg.a2;
    """
    result = analyze_sql_lineage(sql, dialect="mysql")
    a = _summary_by(result["target_summary"], "dw.a")
    b = _summary_by(result["target_summary"], "dw.b")
    assert a["insert_count"] == 2
    assert b["insert_count"] == 1


def test_target_summary_truncate_multiple_tables():
    sql = "TRUNCATE TABLE dim.a, dim.b;"
    result = analyze_sql_lineage(sql, dialect="mysql")
    a = _summary_by(result["target_summary"], "dim.a")
    b = _summary_by(result["target_summary"], "dim.b")
    assert a["truncate_count"] == 1
    assert b["truncate_count"] == 1
    # truncate 单飞，没 insert 跟着 → refresh_mode 不应升级到 truncate_insert
    assert a["refresh_mode"] is None
    assert b["refresh_mode"] is None


def test_target_summary_skips_temp_table():
    sql = """
    CREATE TEMPORARY TABLE tmp_stage AS SELECT * FROM stg.s;
    INSERT INTO dw.fact SELECT * FROM tmp_stage;
    """
    result = analyze_sql_lineage(sql, dialect="mysql")
    targets = {s["target_table"].lower() for s in result["target_summary"]}
    assert "dw.fact" in targets
    assert "tmp_stage" not in targets


def test_target_summary_empty_when_select_only():
    sql = "SELECT a, b FROM dw.fact WHERE biz_date = '2025-01-01';"
    result = analyze_sql_lineage(sql, dialect="mysql")
    assert result["target_summary"] == []


def test_target_summary_insert_overwrite():
    # INSERT OVERWRITE 视为 INSERT 家族
    sql = "INSERT OVERWRITE TABLE dw.fact SELECT * FROM stg.s;"
    result = analyze_sql_lineage(sql, dialect="hive")
    summary = _summary_by(result["target_summary"], "dw.fact")
    assert summary["insert_count"] == 1


def test_target_summary_ctas_counts_as_insert():
    sql = "CREATE TABLE dw.snapshot AS SELECT * FROM stg.cust;"
    result = analyze_sql_lineage(sql, dialect="mysql")
    summary = _summary_by(result["target_summary"], "dw.snapshot")
    assert summary["insert_count"] == 1
    assert summary["refresh_mode"] == "append"


# ─── table_roles 角色识别（Phase 7 Track B 第 4 项）───────────────────────────

def _roles_by(table_roles, name):
    matches = [r for r in table_roles if r["table"].lower() == name.lower()]
    assert matches, f"未在 table_roles 找到 {name}: {[r['table'] for r in table_roles]}"
    return matches[0]


def test_table_roles_target_only():
    sql = "INSERT INTO dwd.fact_order SELECT id, amount FROM ods.order_log;"
    result = analyze_sql_lineage(sql, dialect="mysql")
    fact = _roles_by(result["table_roles"], "dwd.fact_order")
    log = _roles_by(result["table_roles"], "ods.order_log")
    assert fact["primary_role"] == "target"
    assert "target" in fact["roles"]
    assert log["primary_role"] == "source_fact"


def test_table_roles_intermediate():
    sql = """
    INSERT INTO tmp_stage SELECT * FROM ods.raw;
    INSERT INTO dw.final SELECT * FROM tmp_stage;
    """
    result = analyze_sql_lineage(sql, dialect="mysql")
    stage = _roles_by(result["table_roles"], "tmp_stage")
    final = _roles_by(result["table_roles"], "dw.final")
    assert stage["primary_role"] == "intermediate"
    assert "intermediate" in stage["roles"]
    assert final["primary_role"] == "target"


def test_table_roles_dimension_by_schema():
    sql = "INSERT INTO dw.fact SELECT id, c.name FROM ods.x JOIN dim.cust c ON c.id = ods.x.cid;"
    result = analyze_sql_lineage(sql, dialect="mysql")
    cust = _roles_by(result["table_roles"], "dim.cust")
    assert "dimension" in cust["roles"]
    assert cust["primary_role"] == "dimension"


def test_table_roles_dimension_by_basename():
    sql = "INSERT INTO dw.fact SELECT id, d.name FROM ods.x JOIN ods.dim_user d ON d.id = ods.x.uid;"
    result = analyze_sql_lineage(sql, dialect="mysql")
    user = _roles_by(result["table_roles"], "ods.dim_user")
    assert "dimension" in user["roles"]


def test_table_roles_reference():
    sql = "INSERT INTO dw.fact SELECT s.label FROM ods.x JOIN ref.code_status s ON s.code = ods.x.status;"
    result = analyze_sql_lineage(sql, dialect="mysql")
    code_status = _roles_by(result["table_roles"], "ref.code_status")
    assert "reference" in code_status["roles"]


def test_table_roles_config():
    sql = "INSERT INTO dw.fact SELECT cfg.batch FROM ods.x JOIN config.t_config cfg ON cfg.k = 'a';"
    result = analyze_sql_lineage(sql, dialect="mysql")
    cfg = _roles_by(result["table_roles"], "config.t_config")
    assert "config" in cfg["roles"]


def test_table_roles_filter():
    sql = """
    INSERT INTO dw.fact SELECT id FROM ods.x
    WHERE id NOT IN (SELECT cid FROM filter.exclude_cust);
    """
    result = analyze_sql_lineage(sql, dialect="mysql")
    excl = _roles_by(result["table_roles"], "filter.exclude_cust")
    assert "filter" in excl["roles"]


def test_table_roles_remote_dblink_oracle():
    # DB Link 是 Oracle 专属语法
    sql = """
    INSERT INTO dw.f
    SELECT o.id, r.name FROM ods.o o JOIN remote_t@dblink_a r ON r.id = o.rid;
    """
    result = analyze_sql_lineage(sql, dialect="oracle")
    remote = _roles_by(result["table_roles"], "remote_t@dblink_a")
    assert "remote_dblink" in remote["roles"]
    assert remote["primary_role"] == "remote_dblink"


def test_table_roles_target_plus_dimension_combo():
    # 目标表又是个维度表 → 两个 role 都挂上，primary 取 target
    sql = """
    TRUNCATE TABLE dim.user_snapshot;
    INSERT INTO dim.user_snapshot SELECT id, name FROM ods.user;
    """
    result = analyze_sql_lineage(sql, dialect="mysql")
    snap = _roles_by(result["table_roles"], "dim.user_snapshot")
    assert "target" in snap["roles"]
    assert "dimension" in snap["roles"]
    assert snap["primary_role"] == "target"


def test_table_roles_pure_source_no_naming_falls_back_to_source_fact():
    sql = "INSERT INTO dw.f SELECT * FROM ods.order_log;"
    result = analyze_sql_lineage(sql, dialect="mysql")
    log = _roles_by(result["table_roles"], "ods.order_log")
    assert log["roles"] == ["source_fact"]


def test_table_roles_empty_for_select_only():
    sql = "SELECT * FROM ods.x WHERE biz_date = '2025-01-01';"
    result = analyze_sql_lineage(sql, dialect="mysql")
    # 不写表 = 没 target，但读侧 ods.x 应该出现且默认 source_fact
    x = _roles_by(result["table_roles"], "ods.x")
    assert x["roles"] == ["source_fact"]


def test_table_roles_does_not_match_partial_word():
    # `decode_lookup` 应被识别为 reference（lookup），不应把 `decode` 误判
    # `dimsum` 不应被识别为 dimension（dim 没有边界）
    sql = """
    INSERT INTO dw.f SELECT * FROM ods.dimsum;
    INSERT INTO dw.g SELECT * FROM ods.decode_lookup;
    """
    result = analyze_sql_lineage(sql, dialect="mysql")
    dimsum = _roles_by(result["table_roles"], "ods.dimsum")
    lookup = _roles_by(result["table_roles"], "ods.decode_lookup")
    assert "dimension" not in dimsum["roles"]
    assert "reference" in lookup["roles"]


# ─── 注释 → statement_title（Phase 7 Track B 第 6 项）─────────────────────────

def test_statement_title_from_line_comment():
    sql = """
    -- 同步集中交易订单
    INSERT INTO dwd.fact_order SELECT * FROM ods.order_log;
    """
    result = analyze_sql_lineage(sql, dialect="mysql")
    insert = next(s for s in result["statements"] if s["type"] == "INSERT")
    assert insert["title"] == "同步集中交易订单"


def test_statement_title_from_block_comment():
    sql = """
    /* 维表更新 —— 客户基础信息 */
    INSERT INTO dim.cust SELECT * FROM stg.cust;
    """
    result = analyze_sql_lineage(sql, dialect="mysql")
    insert = next(s for s in result["statements"] if s["type"] == "INSERT")
    assert insert["title"] == "维表更新 —— 客户基础信息"


def test_statement_title_multiline_takes_first_nonempty_line():
    sql = """
    /* 全量刷新
       说明：每周三跑
       详见 wiki: foo */
    INSERT INTO dim.x SELECT * FROM stg.x;
    """
    result = analyze_sql_lineage(sql, dialect="mysql")
    insert = next(s for s in result["statements"] if s["type"] == "INSERT")
    assert insert["title"] == "全量刷新"


def test_statement_title_empty_when_no_comment():
    sql = "INSERT INTO dim.x SELECT * FROM stg.x;"
    result = analyze_sql_lineage(sql, dialect="mysql")
    insert = next(s for s in result["statements"] if s["type"] == "INSERT")
    assert insert["title"] == ""


def test_target_summary_titles_collect_all_writes_in_order():
    sql = """
    -- 集中交易表全量刷新
    TRUNCATE TABLE dwd.fact_order;

    -- 同步订单数据
    INSERT INTO dwd.fact_order SELECT * FROM ods.order_log;
    """
    result = analyze_sql_lineage(sql, dialect="mysql")
    summary = _summary_by(result["target_summary"], "dwd.fact_order")
    assert summary["titles"] == ["集中交易表全量刷新", "同步订单数据"]


def test_target_summary_titles_dedupes_same_comment():
    sql = """
    -- 同步订单数据
    INSERT INTO dwd.f SELECT * FROM ods.a;
    -- 同步订单数据
    INSERT INTO dwd.f SELECT * FROM ods.b;
    """
    result = analyze_sql_lineage(sql, dialect="mysql")
    summary = _summary_by(result["target_summary"], "dwd.f")
    assert summary["titles"] == ["同步订单数据"]


def test_target_summary_titles_empty_when_no_comments():
    sql = "INSERT INTO dwd.f SELECT * FROM ods.a;"
    result = analyze_sql_lineage(sql, dialect="mysql")
    summary = _summary_by(result["target_summary"], "dwd.f")
    assert summary["titles"] == []


# ─── semantic_lineage 收口（Phase 7 Track B 第 7 项）──────────────────────────

def _semantic_target(semantic, table):
    matches = [t for t in semantic["targets"] if t["table"].lower() == table.lower()]
    assert matches, f"未在 semantic.targets 找到 {table}"
    return matches[0]


def test_semantic_lineage_top_level_keys():
    sql = "INSERT INTO dw.f SELECT * FROM ods.x;"
    result = analyze_sql_lineage(sql, dialect="mysql")
    sem = result["semantic_lineage"]
    assert set(sem.keys()) == {
        "procedures", "targets", "business_groups",
        "grouped_edges", "observations", "risks",
    }


def test_semantic_lineage_targets_merge_role_and_refresh():
    sql = """
    TRUNCATE TABLE dim.user_snapshot;
    INSERT INTO dim.user_snapshot SELECT * FROM ods.user;
    """
    result = analyze_sql_lineage(sql, dialect="mysql")
    target = _semantic_target(result["semantic_lineage"], "dim.user_snapshot")
    assert target["refresh_mode"] == "truncate_insert"
    assert target["counts"]["insert"] == 1
    assert target["counts"]["truncate"] == 1
    # role: target + dimension（dim. schema），primary 取 target
    assert "dimension" in target["roles"]
    assert target["primary_role"] == "target"


def test_semantic_lineage_observations_count_targets_and_full_refresh():
    sql = """
    TRUNCATE TABLE a;
    INSERT INTO a SELECT * FROM ods.x;
    DELETE FROM b;
    INSERT INTO b SELECT * FROM ods.y;
    INSERT INTO c SELECT * FROM ods.z;
    """
    result = analyze_sql_lineage(sql, dialect="mysql")
    obs = result["semantic_lineage"]["observations"]
    text = " | ".join(obs)
    assert "3 张目标表" in text
    assert "全量重刷" in text  # a (truncate_insert) + b (delete_insert)


def test_semantic_lineage_observations_intermediate():
    sql = """
    INSERT INTO tmp_stage SELECT * FROM ods.raw;
    INSERT INTO dw.final SELECT * FROM tmp_stage;
    """
    result = analyze_sql_lineage(sql, dialect="mysql")
    obs = " | ".join(result["semantic_lineage"]["observations"])
    assert "中转表" in obs


def test_semantic_lineage_observations_dynamic_sql():
    # SET / PREPARE 走动态 SQL 路径 → observations 提到段数
    sql = """
    SET @s = 'INSERT INTO dw.f SELECT * FROM ods.a';
    PREPARE stmt FROM @s;
    EXECUTE stmt;
    """
    result = analyze_sql_lineage(sql, dialect="mysql")
    obs = " | ".join(result["semantic_lineage"]["observations"])
    assert "动态 SQL" in obs


def test_semantic_lineage_risks_low_confidence_dynamic_sql():
    # PL/SQL 变量拼接（`v_sql := 'INSERT ' || p_t || ' SELECT...'; EXECUTE IMMEDIATE v_sql;`）
    # 走 `var_concat` 路径，confidence=low → risks level=medium。
    sql = """
    DECLARE
      v_sql VARCHAR2(2000);
      p_table VARCHAR2(50) := 'targets';
    BEGIN
      v_sql := 'INSERT INTO ' || p_table || ' SELECT * FROM stg.src';
      EXECUTE IMMEDIATE v_sql;
    END;
    """
    result = analyze_sql_lineage(sql, dialect="oracle")
    risks = result["semantic_lineage"]["risks"]
    types = {r["type"] for r in risks}
    assert "dynamic_sql_low_confidence" in types
    levels = {r["level"] for r in risks if r["type"] == "dynamic_sql_low_confidence"}
    assert "medium" in levels  # var_concat low confidence → medium risk level


def test_semantic_lineage_procedures_grouped_by_name():
    sql = """
    CREATE OR REPLACE PROCEDURE sync_orders AS
    BEGIN
        INSERT INTO dw.a SELECT * FROM ods.x;
        INSERT INTO dw.b SELECT * FROM ods.y;
    END;
    /
    """
    result = analyze_sql_lineage(sql, dialect="oracle")
    procs = result["semantic_lineage"]["procedures"]
    assert len(procs) == 1
    assert procs[0]["name"] == "sync_orders"
    assert procs[0]["segment_count"] >= 2


def test_semantic_lineage_empty_for_select_only():
    sql = "SELECT * FROM ods.x;"
    result = analyze_sql_lineage(sql, dialect="mysql")
    sem = result["semantic_lineage"]
    assert sem["targets"] == []
    assert sem["procedures"] == []
    assert sem["risks"] == []
    # observations 也应该没东西（没目标表也没存储过程也没动态 SQL）
    assert sem["observations"] == []


# ─── Oracle 大型存储过程回归测试（合成 fixture，复刻 a_cispnew_f3045.sql 的所有 bug）─

_ORACLE_PROC_FIXTURE = """\
create or replace procedure cispnew.sync_full_refresh(
  p_out_flag  out varchar2,
  p_out_msg   out varchar2
)
is
  v_us number(14,5);
begin
  p_out_flag := '1';
  p_out_msg  := 'OK';

  /*集中交易*/
  delete from cispnew.t_etl_jy;
  commit;

  --A股主板股票
  insert into /*+ parallel(cispnew.t_etl_jy,4) */ cispnew.t_etl_jy
        (yyb,--营业部
         khlx,--0-个人 1-机构
         zqlx,--1-A股主板 2-创业板 3-科创板 4-深B 5-沪B 6-新三板
         je
        )
  select /*+ parallel(t1,4)(a,4) */
         trim(t1.branch_code),
         substr(a.attribute, 1, 1),
         '1',
         sum(t1.amt * v_us)
  from kods.a_ks_his_done t1
  inner join kods.a_ks_cust_base_info a on t1.cust_no = a.cust_no
  where t1.market_code = '1'
    and t1.sec_type = '01'
    and not exists (select c.cust_no from cisp.cust_base_info c where c.cust_no = trim(a.cust_no))
  group by substr(a.attribute, 1, 1), trim(t1.branch_code);
  commit;

  --优先股
  insert into /*+ parallel(cispnew.t_etl_jy,4) */ cispnew.t_etl_jy
        (yyb, khlx, zqlx, je)
  select trim(t1.branch_code),
         substr(a.attribute, 1, 1),
         '2',
         sum(case when t1.market_code = '1' then t1.amt else 0 end)
  from kods.a_ks_his_done t1
  inner join kods.a_ks_cust_base_info a on t1.cust_no = a.cust_no
  where t1.sec_type = '02';
  commit;

  --A股创业板
  insert into /*+ parallel(cispnew.t_etl_jy,4) */ cispnew.t_etl_jy
        (yyb, khlx, zqlx, je)
  select trim(t1.branch_code),
         substr(a.attribute, 1, 1),
         '3',
         sum(t1.amt)
  from kods.a_ks_his_done t1
  inner join kods.a_ks_cust_base_info a on t1.cust_no = a.cust_no
  where t1.sec_type = '03';
  commit;

  /*集中交易托管市值*/
  delete from cispnew.t_etl_zqsz;
  commit;

  --A股主板
  insert into cispnew.t_etl_zqsz
        (yyb,--营业部
         khlx,
         zqlx,-- 1-A股主板 2-创业板
         zqsz
        )
  select trim(a.branch_code),
         substr(a.attribute, 1, 1),
         '1',
         sum(case when t.market_code = '1' then t.total_asset else 0 end)
  from kods.a_ks_stock t
  inner join kods.a_ks_cust_base_info a on t.cust_no = a.cust_no
  where t.market_code = '1'
  group by substr(a.attribute, 1, 1), trim(a.branch_code);
  commit;

  --优先股
  insert into cispnew.t_etl_zqsz (yyb, khlx, zqlx, zqsz)
  select trim(a.branch_code),
         substr(a.attribute, 1, 1),
         '2',
         sum(t.total_asset)
  from kods.a_ks_stock t
  inner join kods.a_ks_cust_base_info a on t.cust_no = a.cust_no
  where t.sec_type = '02';
  commit;

end sync_full_refresh;
/
"""


def _summary_by_lower(target_summary, table):
    matches = [s for s in target_summary if s["target_table"].lower() == table.lower()]
    assert matches, f"未在 target_summary 找到 {table}"
    return matches[0]


def test_oracle_proc_fixture_target_counts():
    """大型 Oracle 存储过程：DELETE+多 INSERT 全量重刷（来自 a_cispnew_f3045.sql 的回归）。

    这个 fixture 复刻真实文件里把 88 INSERT 折成 1 的全部 bug：CASE...END 把外层
    BEGIN/END token 计数搞乱、`/*+ parallel(...) */` hint 误为业务标题、`-- 行注释`
    在多列 INSERT 列表里、324 段假动态 SQL（任何 20+ 字符字符串字面量都中招）。
    """
    result = analyze_sql_lineage(_ORACLE_PROC_FIXTURE, dialect="oracle")

    # 三段 INSERT INTO t_etl_jy + 一条 DELETE
    jy = _summary_by_lower(result["target_summary"], "cispnew.t_etl_jy")
    assert jy["insert_count"] == 3, f"jy.insert_count={jy['insert_count']} 期望 3"
    assert jy["delete_count"] == 1
    assert jy["refresh_mode"] == "delete_insert"

    # 两段 INSERT INTO t_etl_zqsz + 一条 DELETE
    zqsz = _summary_by_lower(result["target_summary"], "cispnew.t_etl_zqsz")
    assert zqsz["insert_count"] == 2, f"zqsz.insert_count={zqsz['insert_count']} 期望 2"
    assert zqsz["delete_count"] == 1
    assert zqsz["refresh_mode"] == "delete_insert"


def test_oracle_proc_fixture_titles_skip_hints():
    """业务标题应该是 INSERT 前的中文注释，不是 Oracle hint。"""
    result = analyze_sql_lineage(_ORACLE_PROC_FIXTURE, dialect="oracle")

    jy = _summary_by_lower(result["target_summary"], "cispnew.t_etl_jy")
    titles = jy.get("titles", [])
    # 真业务标题
    assert "A股主板股票" in titles
    assert "优先股" in titles
    assert "A股创业板" in titles
    # hint 不应在
    assert not any("parallel(" in t for t in titles), f"hint 漏到 titles: {titles}"

    zqsz = _summary_by_lower(result["target_summary"], "cispnew.t_etl_zqsz")
    z_titles = zqsz.get("titles", [])
    assert "集中交易托管市值" in z_titles or "A股主板" in z_titles


def test_oracle_proc_fixture_no_false_positive_dynamic_sql():
    """没真正的 EXECUTE IMMEDIATE / sp_executesql / PREPARE → dynamic_sql_count 应为 0。

    a_cispnew_f3045.sql 报 324 段，全部是字符串字面量误报。"""
    result = analyze_sql_lineage(_ORACLE_PROC_FIXTURE, dialect="oracle")
    assert result["dynamic_sql_count"] == 0


def test_oracle_proc_fixture_procedure_segments():
    """过程体段切分：CASE...END 不能让外层 BEGIN/END 计数错乱。"""
    result = analyze_sql_lineage(_ORACLE_PROC_FIXTURE, dialect="oracle")
    procs = result["semantic_lineage"]["procedures"]
    assert len(procs) == 1
    assert procs[0]["name"] == "cispnew.sync_full_refresh"
    # 至少 2 DELETE + 5 INSERT = 7 段（具体数因为 dedupe 可能略低）
    assert procs[0]["segment_count"] >= 5


# ─── procedure_segments line_start / preceding_comment / parse_status ─────────


def test_procedure_segments_carry_line_numbers():
    """每段记录 line_start / line_end，按出现顺序单调递增。"""
    sql = """\
CREATE OR REPLACE PROCEDURE etl_demo AS
BEGIN
  -- 业务标题 A
  INSERT INTO dw.t_a SELECT * FROM stg.s_a;

  -- 业务标题 B
  INSERT INTO dw.t_b SELECT * FROM stg.s_b;
END;
"""
    result = analyze_sql_lineage(sql, dialect="oracle")
    segs = result["procedure_segments"]
    assert len(segs) == 2
    assert all("line_start" in s and "line_end" in s for s in segs)
    assert segs[0]["line_start"] < segs[1]["line_start"]
    # 第一段 INSERT 在第 4 行（1-based），允许 ±1 容差应对前置注释计入策略
    assert 3 <= segs[0]["line_start"] <= 4
    assert segs[0]["line_end"] >= segs[0]["line_start"]


def test_procedure_segments_extract_preceding_comment():
    """业务标题 `-- 集中交易` 这种前置行注释被抽到 preceding_comment。"""
    sql = """\
CREATE OR REPLACE PROCEDURE etl_demo AS
BEGIN
  -- 集中交易
  INSERT INTO dw.t_a SELECT * FROM stg.s_a;
  INSERT INTO dw.t_b SELECT * FROM stg.s_b;
END;
"""
    result = analyze_sql_lineage(sql, dialect="oracle")
    segs = result["procedure_segments"]
    assert segs[0]["preceding_comment"] == "集中交易"
    # 第二段没有前置注释 → 空串
    assert segs[1]["preceding_comment"] == ""


def test_procedure_segments_block_comment_extracted():
    """`/* 业务标题 */` 块注释也能抽出。"""
    sql = """\
CREATE OR REPLACE PROCEDURE etl_demo AS
BEGIN
  /* 持仓市值 -- 全量重刷 */
  INSERT INTO dw.t_a SELECT * FROM stg.s_a;
END;
"""
    result = analyze_sql_lineage(sql, dialect="oracle")
    seg = result["procedure_segments"][0]
    assert "持仓市值" in seg["preceding_comment"]


def test_procedure_segments_parse_status_marked():
    """sqlglot 能解析的段标 parsed；不能的标 unsupported。"""
    sql = """\
CREATE OR REPLACE PROCEDURE etl_demo AS
BEGIN
  INSERT INTO dw.t_a SELECT * FROM stg.s_a;
END;
"""
    result = analyze_sql_lineage(sql, dialect="oracle")
    segs = result["procedure_segments"]
    assert all(s["parse_status"] in ("parsed", "unsupported") for s in segs)
    assert segs[0]["parse_status"] == "parsed"


def test_semantic_procedures_expose_steps():
    """semantic_lineage.procedures[i].steps 暴露每段的 line / 标题 / 状态。"""
    sql = """\
CREATE OR REPLACE PROCEDURE etl_demo AS
BEGIN
  -- 标题 A
  INSERT INTO dw.t_a SELECT * FROM stg.s_a;
  -- 标题 B
  DELETE FROM dw.t_b WHERE dt = SYSDATE;
END;
"""
    result = analyze_sql_lineage(sql, dialect="oracle")
    proc = result["semantic_lineage"]["procedures"][0]
    assert proc["segment_count"] == 2
    assert len(proc["steps"]) == 2
    assert proc["steps"][0]["dml_keyword"] == "INSERT"
    assert proc["steps"][1]["dml_keyword"] == "DELETE"
    assert proc["steps"][0]["preceding_comment"] == "标题 A"
    assert all(s.get("line_start") is not None for s in proc["steps"])


def test_oracle_proc_fixture_steps_monotonic():
    """真实 Oracle fixture：所有 step 的 line_start 单调递增。"""
    result = analyze_sql_lineage(_ORACLE_PROC_FIXTURE, dialect="oracle")
    proc = result["semantic_lineage"]["procedures"][0]
    lines = [s["line_start"] for s in proc["steps"]]
    assert lines == sorted(lines)
    # 至少有一段抽到了业务标题
    assert any(s["preceding_comment"] for s in proc["steps"])
