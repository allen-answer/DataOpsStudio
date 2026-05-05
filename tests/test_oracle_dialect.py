"""Oracle 方言深化回归。覆盖 hint / DB Link / SELECT INTO / 游标 /
EXECUTE IMMEDIATE 风险提示等真实 PL/SQL 项目里常见的语法。

合成 SQL，不上传业务文件。"""
from __future__ import annotations

from app.lineage.analyzer import analyze_sql_lineage


def _summary_by(target_summary, table):
    matches = [s for s in target_summary if s["target_table"].lower() == table.lower()]
    assert matches, f"target_summary missing {table}: {[s['target_table'] for s in target_summary]}"
    return matches[0]


def _has_warning(result, type_substr):
    for w in result.get("warnings", []) or []:
        if type_substr in (w.get("type", "") or ""):
            return True
    return False


# ─── Oracle hint：业务标题不能被 hint 抢 ──────────────────────────────────────


def test_oracle_parallel_hint_not_treated_as_business_title():
    sql = """
    /*+ parallel(t, 8) */
    -- 集中交易当日全量
    INSERT INTO dwd.t_jy SELECT * FROM ods.jy t;
    """
    result = analyze_sql_lineage(sql, dialect="oracle")
    summary = _summary_by(result["target_summary"], "dwd.t_jy")
    titles = summary.get("titles") or []
    assert any("集中交易" in t for t in titles), \
        f"业务标题应被识别为'集中交易当日全量'，而不是被 hint 吃掉: {titles}"
    # hint 不应该出现在 titles 里
    assert not any("parallel" in t.lower() for t in titles)


def test_oracle_use_hash_hint_skipped():
    sql = """
    /*+ use_hash(a b) leading(a) */
    -- 关联订单和退款
    INSERT INTO dwd.fact (id, amt) SELECT a.id, a.amt FROM ods.orders a JOIN ods.refunds b ON a.id=b.id;
    """
    result = analyze_sql_lineage(sql, dialect="oracle")
    summary = _summary_by(result["target_summary"], "dwd.fact")
    titles = summary.get("titles") or []
    assert any("关联" in t for t in titles), f"标题应被识别: {titles}"


# ─── Oracle DB Link：tab@dblink 保留为外部源表，role=remote_dblink ─────────────


def test_oracle_db_link_table_preserved_as_remote():
    sql = """
    INSERT INTO dwd.local_copy (id, name)
    SELECT id, name FROM ods.remote_orders@DBLINK_PROD;
    """
    result = analyze_sql_lineage(sql, dialect="oracle")
    # 表级 edge 应保留 @dblink 后缀
    sources = {edge["source_table"] for edge in result["graph_edges"] if edge["target_table"] == "dwd.local_copy"}
    assert any("remote_orders" in s and "DBLINK_PROD" in s for s in sources), \
        f"DB Link 源表应保留 @ 后缀: {sources}"

    # role 识别为 remote_dblink
    roles_by_table = {r["table"]: r["roles"] for r in result["table_roles"]}
    dblink_tables = [t for t in roles_by_table if "@" in t]
    assert dblink_tables, f"应识别出 @dblink 表: {list(roles_by_table.keys())}"
    for t in dblink_tables:
        assert "remote_dblink" in roles_by_table[t], \
            f"{t} 应有 remote_dblink role，实际：{roles_by_table[t]}"


# ─── SELECT INTO :variable —— PL/SQL 单值赋值不应让外层解析失败 ───────────────


def test_oracle_select_into_variable_keeps_table_lineage():
    sql = """
    CREATE OR REPLACE PROCEDURE etl_demo AS
      v_cnt NUMBER;
    BEGIN
      SELECT count(*) INTO v_cnt FROM ods.orders WHERE biz_date = :p_date;
      INSERT INTO dwd.t_summary (cnt, biz_date) VALUES (v_cnt, :p_date);
    END;
    """
    result = analyze_sql_lineage(sql, dialect="oracle")
    # 至少 ods.orders 出现在 read tables（被 SELECT INTO 读）
    read_tables = {t["table"] for t in result["tables"]}
    assert "ods.orders" in read_tables, \
        f"SELECT INTO 不应让 ods.orders 漏掉: {read_tables}"


# ─── EXECUTE IMMEDIATE 静态无法推断 —— 应该出 warning ─────────────────────────


def test_oracle_execute_immediate_unknown_var_emits_warning():
    """变量在过程外部传入或来自 cursor，无法静态推断 → 应该出 dynamic_sql warning。
    不能默默吞掉，否则用户看不到这部分血缘缺失。"""
    sql = """
    CREATE OR REPLACE PROCEDURE p_runtime(p_dynamic_sql VARCHAR2) AS
    BEGIN
      EXECUTE IMMEDIATE p_dynamic_sql;
    END;
    """
    result = analyze_sql_lineage(sql, dialect="oracle")
    assert _has_warning(result, "动态") or _has_warning(result, "dynamic"), \
        f"EXECUTE IMMEDIATE 接外部变量应出 warning: {result.get('warnings', [])}"


def test_oracle_execute_immediate_literal_keeps_lineage():
    """EXECUTE IMMEDIATE 接字面量 SQL 仍能抽出血缘"""
    sql = """
    CREATE OR REPLACE PROCEDURE p_literal AS
    BEGIN
      EXECUTE IMMEDIATE 'INSERT INTO dwd.target (id) SELECT id FROM ods.source';
    END;
    """
    result = analyze_sql_lineage(sql, dialect="oracle")
    target_tables = {s["target_table"] for s in result["target_summary"]}
    assert any("dwd.target" in t.lower() for t in target_tables), \
        f"字面量 EXECUTE IMMEDIATE 应抽出 dwd.target: {target_tables}"


# ─── 包变量 / 包级常量赋值不应破坏血缘 ────────────────────────────────────────


def test_oracle_package_body_with_constants_parses():
    sql = """
    CREATE OR REPLACE PACKAGE BODY pkg_etl AS
      g_run_date CONSTANT DATE := SYSDATE;
      g_app_id   CONSTANT VARCHAR2(32) := 'JY';

      PROCEDURE run_daily IS
      BEGIN
        INSERT INTO dwd.t_jy (run_date, app_id, cnt)
        SELECT g_run_date, g_app_id, count(*) FROM ods.orders;
      END;
    END;
    """
    result = analyze_sql_lineage(sql, dialect="oracle")
    target_tables = {s["target_table"] for s in result["target_summary"]}
    assert any("dwd.t_jy" in t.lower() for t in target_tables), \
        f"PACKAGE BODY 内的 INSERT 应被抽出: {target_tables}"


# ─── Oracle 游标 cursor —— FOR loop 内 INSERT 应该被识别 ─────────────────────


def test_oracle_cursor_for_loop_inner_insert():
    sql = """
    CREATE OR REPLACE PROCEDURE p_cursor AS
    BEGIN
      FOR rec IN (SELECT id, amt FROM ods.batches WHERE status = 'pending') LOOP
        INSERT INTO dwd.fact_amount (id, amt) VALUES (rec.id, rec.amt);
      END LOOP;
    END;
    """
    result = analyze_sql_lineage(sql, dialect="oracle")
    target_tables = {s["target_table"] for s in result["target_summary"]}
    assert any("dwd.fact_amount" in t.lower() for t in target_tables), \
        f"FOR cursor LOOP 内的 INSERT 应被抽出: {target_tables}"


# ─── S5：cursor source tracking —— body INSERT VALUES (rec.col) 必须补 source → target 边 ─────


def _cursor_edge(edges, source, target):
    src_l, tgt_l = source.lower(), target.lower()
    return [
        e for e in edges
        if e["source_table"].lower() == src_l
        and e["target_table"].lower() == tgt_l
        and e.get("edge_type") == "CURSOR_LOOP_INSERT"
    ]


def test_cursor_source_tracking_single_table():
    """`FOR rec IN (SELECT FROM tabA) LOOP INSERT INTO tabB VALUES (rec.col)` —
    INSERT 没 source_tables，应该靠 cursor_sources 补出 ods.batches → dwd.fact 边。"""
    sql = """
    CREATE OR REPLACE PROCEDURE pkg.demo IS
    BEGIN
      FOR rec IN (SELECT id, name FROM ods.batches WHERE flag = 1) LOOP
        INSERT INTO dwd.fact (id, name) VALUES (rec.id, rec.name);
      END LOOP;
    END;
    """
    result = analyze_sql_lineage(sql, dialect="oracle")
    cursor_edges = _cursor_edge(result["graph_edges"], "ods.batches", "dwd.fact")
    assert cursor_edges, \
        f"应补 ods.batches → dwd.fact 的 CURSOR_LOOP_INSERT 边: {result['graph_edges']}"
    edge = cursor_edges[0]
    assert edge["confidence"] == "medium", "cursor 推断的边 confidence 应为 medium"
    assert "cursor FOR loop" in edge["reason"]
    assert "pkg.demo" in edge["reason"]


def test_cursor_source_tracking_multi_table_join():
    """cursor SELECT 含 JOIN 时，每个源表都应该补一条边到 INSERT target。"""
    sql = """
    CREATE OR REPLACE PROCEDURE pkg.multi IS
    BEGIN
      FOR rec IN (
        SELECT b.id, b.name, c.code
        FROM ods.batches b
        INNER JOIN ods.codes c ON b.id = c.batch_id
      ) LOOP
        INSERT INTO dwd.fact (id, name, code) VALUES (rec.id, rec.name, rec.code);
      END LOOP;
    END;
    """
    result = analyze_sql_lineage(sql, dialect="oracle")
    assert _cursor_edge(result["graph_edges"], "ods.batches", "dwd.fact"), \
        "JOIN 左表应补边"
    assert _cursor_edge(result["graph_edges"], "ods.codes", "dwd.fact"), \
        "JOIN 右表也应补边"


def test_cursor_source_tracking_no_dml_body():
    """cursor LOOP 体里没 DML（仅 dbms_output 等）—— 不应产生任何 supplemental 边。"""
    sql = """
    CREATE OR REPLACE PROCEDURE pkg.empty_loop IS
    BEGIN
      FOR rec IN (SELECT id FROM ods.batches) LOOP
        dbms_output.put_line(rec.id);
      END LOOP;
    END;
    """
    result = analyze_sql_lineage(sql, dialect="oracle")
    cursor_loop_edges = [e for e in result["graph_edges"] if e.get("edge_type") == "CURSOR_LOOP_INSERT"]
    assert cursor_loop_edges == [], \
        f"无 DML 的 cursor body 不应产生 CURSOR_LOOP_INSERT 边: {cursor_loop_edges}"


def test_cursor_source_tracking_dedup_against_existing_edges():
    """普通 INSERT-SELECT（_graph_edges 已经能抽出 high confidence 边）+ cursor body
    INSERT VALUES 同时存在时，cursor supplemental 不应重复添加同 (source, target) 边。"""
    sql = """
    INSERT INTO dwd.fact (id) SELECT id FROM ods.batches;
    """
    # 第一条静态 INSERT-SELECT 已经覆盖 ods.batches → dwd.fact 的边
    result = analyze_sql_lineage(sql, dialect="oracle")
    same_pair = [
        e for e in result["graph_edges"]
        if e["source_table"].lower() == "ods.batches"
        and e["target_table"].lower() == "dwd.fact"
    ]
    # 至少 1 条边（静态 INSERT-SELECT 的），不应被 cursor 重复添加
    assert len(same_pair) == 1, f"同 source-target 应只 1 条边: {same_pair}"
    assert same_pair[0]["confidence"] == "high", "原始静态边应保留 high confidence"


def test_cursor_source_tracking_multi_dml_in_loop_body():
    """S5 PR2：cursor LOOP 体内多个 DML 段都应继承 cursor_sources。
    PR1 只覆盖到第一段，UPDATE / DELETE 也是有效的 cursor body 操作。"""
    sql = """
    CREATE OR REPLACE PROCEDURE pkg.multi_dml IS
    BEGIN
      FOR rec IN (SELECT id FROM ods.batches) LOOP
        INSERT INTO dwd.fact (id) VALUES (rec.id);
        UPDATE dwd.audit SET last_run = sysdate WHERE id = rec.id;
        DELETE FROM dwd.stale WHERE id = rec.id;
      END LOOP;
    END;
    """
    result = analyze_sql_lineage(sql, dialect="oracle")
    assert _cursor_edge(result["graph_edges"], "ods.batches", "dwd.fact"), \
        "INSERT 段应有边（PR1 已覆盖）"
    assert _cursor_edge(result["graph_edges"], "ods.batches", "dwd.audit"), \
        "PR2：UPDATE 段也应继承 cursor_sources 补边"
    assert _cursor_edge(result["graph_edges"], "ods.batches", "dwd.stale"), \
        "PR2：DELETE 段也应继承 cursor_sources 补边"


def test_cursor_source_tracking_nested_loops_inner_takes_precedence():
    """S5 PR2：嵌套 cursor LOOP 时段应继承"最内层"scope 的 cursor_sources。
    一旦内层 END LOOP，外层 scope 重新生效。"""
    sql = """
    CREATE OR REPLACE PROCEDURE pkg.nested IS
    BEGIN
      FOR a IN (SELECT id FROM ods.outer_t) LOOP
        INSERT INTO dwd.outer_dst (id) VALUES (a.id);
        FOR b IN (SELECT code FROM ods.inner_t) LOOP
          INSERT INTO dwd.inner_dst (id, code) VALUES (a.id, b.code);
        END LOOP;
        UPDATE dwd.outer_audit SET cnt = cnt + 1 WHERE id = a.id;
      END LOOP;
    END;
    """
    result = analyze_sql_lineage(sql, dialect="oracle")
    # 外层 scope 段：用 ods.outer_t
    assert _cursor_edge(result["graph_edges"], "ods.outer_t", "dwd.outer_dst")
    assert _cursor_edge(result["graph_edges"], "ods.outer_t", "dwd.outer_audit"), \
        "内层 END LOOP 后外层 scope 应恢复"
    # 内层 scope 段：用 ods.inner_t（最内层取胜）
    assert _cursor_edge(result["graph_edges"], "ods.inner_t", "dwd.inner_dst")
    # 外层 source 不应误挂到内层目标
    assert not _cursor_edge(result["graph_edges"], "ods.outer_t", "dwd.inner_dst"), \
        "嵌套时取最内层 scope，外层 source 不应挂到内层 target"


# ─── S5 PR4：UDF 调用应补 UDF 读的源表 → DML target 边 ─────────────────────────


def _udf_edge(edges, source, target):
    src_l, tgt_l = source.lower(), target.lower()
    return [
        e for e in edges
        if e["source_table"].lower() == src_l
        and e["target_table"].lower() == tgt_l
        and e.get("edge_type") == "UDF_CALL"
    ]


def test_udf_call_in_insert_values_adds_source_edge():
    """`INSERT INTO X VALUES (pkg.fn)` —— pkg.fn 函数体内 SELECT 的源表应作为
    supplemental 边补到 X 上。"""
    sql = """
    CREATE OR REPLACE FUNCTION pkg.get_max_amt RETURN NUMBER IS
      v_max NUMBER;
    BEGIN
      SELECT max(amt) INTO v_max FROM ods.txn WHERE flag = 1;
      RETURN v_max;
    END;

    INSERT INTO dwd.summary (max_amt) VALUES (pkg.get_max_amt);
    """
    result = analyze_sql_lineage(sql, dialect="oracle")
    udf_edges = _udf_edge(result["graph_edges"], "ods.txn", "dwd.summary")
    assert udf_edges, \
        f"应补 ods.txn → dwd.summary 的 UDF_CALL 边: {result['graph_edges']}"
    edge = udf_edges[0]
    assert edge["confidence"] == "medium"
    assert "pkg.get_max_amt" in edge["reason"]


def test_udf_call_with_arguments_picked_up():
    """带参数 `pkg.fn(arg)` 调用形式同样应识别。"""
    sql = """
    CREATE OR REPLACE FUNCTION pkg.lookup(p_id IN NUMBER) RETURN VARCHAR2 IS
      v VARCHAR2(50);
    BEGIN
      SELECT name INTO v FROM ods.dim_user WHERE id = p_id;
      RETURN v;
    END;

    INSERT INTO dwd.report (id, name) SELECT id, pkg.lookup(id) FROM ods.events;
    """
    result = analyze_sql_lineage(sql, dialect="oracle")
    # ods.events 应是静态边（INSERT-SELECT），ods.dim_user 应是 UDF_CALL 补的
    assert _udf_edge(result["graph_edges"], "ods.dim_user", "dwd.report"), \
        "UDF 内读的 ods.dim_user 应补到 dwd.report"


def test_udf_call_function_definition_statement_skipped():
    """`CREATE FUNCTION ...` 自身的 statement SQL 引用了函数名，但那是定义，
    不该补边到自己。"""
    sql = """
    CREATE OR REPLACE FUNCTION pkg.fn RETURN NUMBER IS
      v NUMBER;
    BEGIN
      SELECT max(amt) INTO v FROM ods.txn;
      RETURN v;
    END;
    """
    result = analyze_sql_lineage(sql, dialect="oracle")
    udf_edges = [e for e in result["graph_edges"] if e.get("edge_type") == "UDF_CALL"]
    assert udf_edges == [], \
        f"只定义 UDF 没有调用方，不应有 UDF_CALL 边: {udf_edges}"


def test_udf_call_no_function_no_edges():
    """普通 INSERT-SELECT 无 UDF —— UDF_CALL 边应空。"""
    sql = """
    INSERT INTO dwd.fact (id) SELECT id FROM ods.batches;
    """
    result = analyze_sql_lineage(sql, dialect="oracle")
    udf_edges = [e for e in result["graph_edges"] if e.get("edge_type") == "UDF_CALL"]
    assert udf_edges == []


# ─── S5 PR3：PACKAGE BODY / DECLARE 的常量与变量声明应进入 result.variables ─────


def _var_by(variables, name):
    matches = [v for v in variables if v.get("name", "").lower() == name.lower()]
    return matches[0] if matches else None


def test_package_body_constants_extracted():
    """`CONSTANT TYPE := value` 应被识别为 package_constant，带 assigned_value。"""
    sql = """
    CREATE OR REPLACE PACKAGE BODY pkg_etl AS
      g_app_id CONSTANT VARCHAR2(32) := 'JY';
      g_threshold CONSTANT NUMBER := 100;
      PROCEDURE run IS BEGIN INSERT INTO dwd.t (a) VALUES (g_app_id); END;
    END;
    """
    result = analyze_sql_lineage(sql, dialect="oracle")
    variables = result.get("variables", [])
    g_app_id = _var_by(variables, "g_app_id")
    assert g_app_id is not None, f"应识别 g_app_id package constant: {variables}"
    assert g_app_id["kind"] == "package_constant"
    assert "JY" in g_app_id["assigned_value"]

    g_threshold = _var_by(variables, "g_threshold")
    assert g_threshold is not None
    assert g_threshold["assigned_value"] == "100"


def test_package_body_non_constant_variables_extracted():
    """非 CONSTANT 的 package 顶层变量也应被识别（kind=package_variable）。"""
    sql = """
    CREATE OR REPLACE PACKAGE BODY pkg_etl AS
      g_counter NUMBER := 0;
      PROCEDURE run IS BEGIN INSERT INTO dwd.t (cnt) VALUES (g_counter); END;
    END;
    """
    result = analyze_sql_lineage(sql, dialect="oracle")
    g_counter = _var_by(result.get("variables", []), "g_counter")
    assert g_counter is not None, f"应识别 g_counter: {result.get('variables')}"
    assert g_counter["kind"] == "package_variable"
    assert g_counter["assigned_value"] == "0"


def test_declare_block_variables_extracted():
    """匿名块 `DECLARE ... BEGIN ... END;` 里的声明也应被抽出。"""
    sql = """
    DECLARE
      v_cnt NUMBER := 100;
      v_label CONSTANT VARCHAR2(50) := 'demo';
    BEGIN
      INSERT INTO dwd.t (cnt, label) VALUES (v_cnt, v_label);
    END;
    """
    result = analyze_sql_lineage(sql, dialect="oracle")
    variables = result.get("variables", [])
    v_cnt = _var_by(variables, "v_cnt")
    assert v_cnt is not None
    assert v_cnt["kind"] == "declare_variable"
    assert v_cnt["assigned_value"] == "100"

    v_label = _var_by(variables, "v_label")
    assert v_label is not None
    assert v_label["kind"] == "declare_constant"
    assert "demo" in v_label["assigned_value"]


# ─── S5 PR9：BULK COLLECT + FORALL pattern 应能补 source 边 ────────────────────


def _bulk_edge(edges, source, target):
    src_l, tgt_l = source.lower(), target.lower()
    return [
        e for e in edges
        if e["source_table"].lower() == src_l
        and e["target_table"].lower() == tgt_l
        and e.get("edge_type") == "BULK_COLLECT"
    ]


def test_bulk_collect_forall_pattern():
    """`SELECT BULK COLLECT INTO v FROM tabA;` + `FORALL i ... INSERT INTO tabB
    VALUES (v(i).col)` —— 应该补 tabA → tabB 的 BULK_COLLECT 边。"""
    sql = """
    CREATE OR REPLACE PROCEDURE pkg.bulk_load IS
      v_data x;
    BEGIN
      SELECT * BULK COLLECT INTO v_data FROM ods.orders;
      FORALL i IN 1..v_data.COUNT
        INSERT INTO dwd.fact (id, amt) VALUES (v_data(i).id, v_data(i).amt);
    END;
    """
    result = analyze_sql_lineage(sql, dialect="oracle")
    bulk_edges = _bulk_edge(result["graph_edges"], "ods.orders", "dwd.fact")
    assert bulk_edges, f"应补 ods.orders → dwd.fact BULK_COLLECT 边: {result['graph_edges']}"
    assert bulk_edges[0]["confidence"] == "medium"
    assert "v_data" in bulk_edges[0]["reason"]


def test_bulk_collect_with_join_multi_source():
    """BULK COLLECT 的 SELECT 含 JOIN 时多个源表都该补边。"""
    sql = """
    CREATE OR REPLACE PROCEDURE pkg.proc IS
      v_data x;
    BEGIN
      SELECT a.id BULK COLLECT INTO v_data FROM ods.orders a JOIN ods.codes b ON a.id = b.id;
      FORALL i IN 1..v_data.COUNT
        INSERT INTO dwd.fact (id) VALUES (v_data(i).id);
    END;
    """
    result = analyze_sql_lineage(sql, dialect="oracle")
    assert _bulk_edge(result["graph_edges"], "ods.orders", "dwd.fact")
    assert _bulk_edge(result["graph_edges"], "ods.codes", "dwd.fact")


def test_bulk_collect_no_dml_consumer_no_edge():
    """只有 BULK COLLECT 没有 INSERT 消费 array —— 不该补 BULK_COLLECT 边。"""
    sql = """
    CREATE OR REPLACE PROCEDURE pkg.proc IS
      v_data x;
    BEGIN
      SELECT * BULK COLLECT INTO v_data FROM ods.orders;
      dbms_output.put_line(v_data.COUNT);
    END;
    """
    result = analyze_sql_lineage(sql, dialect="oracle")
    bulk_edges = [e for e in result["graph_edges"] if e.get("edge_type") == "BULK_COLLECT"]
    assert bulk_edges == [], f"无 INSERT 消费 array 不应有边: {bulk_edges}"


# ─── S5 PR8：cursor with parameters / Oracle INSERT ALL fan-out ────────────────


def test_declared_cursor_with_parameters():
    """`CURSOR cur(p NUMBER) IS SELECT ...; FOR rec IN cur(1) LOOP` 应该解析
    cursor 名 + 跳过参数列表，正常补 source 边。"""
    sql = """
    CREATE OR REPLACE PROCEDURE pkg.proc IS
      CURSOR cur_orders(p_flag NUMBER) IS
        SELECT id FROM ods.orders WHERE flag = p_flag;
    BEGIN
      FOR rec IN cur_orders(1) LOOP
        INSERT INTO dwd.fact (id) VALUES (rec.id);
      END LOOP;
    END;
    """
    result = analyze_sql_lineage(sql, dialect="oracle")
    assert _cursor_edge(result["graph_edges"], "ods.orders", "dwd.fact"), \
        f"参数化 cursor 应被解析: {result['graph_edges']}"


def test_oracle_insert_all_fans_out():
    """Oracle `INSERT ALL ... INTO t1 ... INTO t2 ... SELECT ...` 应该被识别
    成多个独立 INSERT，每个 target 都该有独立的 graph_edges + target_summary。"""
    sql = """
    INSERT ALL
      INTO dwd.t1 (id, amt) VALUES (id, amt)
      INTO dwd.t2 (id, amt) VALUES (id, amt * 2)
    SELECT id, amt FROM ods.orders;
    """
    result = analyze_sql_lineage(sql, dialect="oracle")
    targets = {s.get("target_table") for s in result.get("target_summary", [])}
    assert "dwd.t1" in targets and "dwd.t2" in targets, \
        f"INSERT ALL 应 fan-out 到 t1+t2: {targets}"
    edges = [(e["source_table"], e["target_table"]) for e in result.get("graph_edges", [])]
    assert ("ods.orders", "dwd.t1") in edges
    assert ("ods.orders", "dwd.t2") in edges


def test_oracle_insert_all_field_mappings_per_target():
    """INSERT ALL 每个 target 都要有自己的 insert_mappings 列。"""
    sql = """
    INSERT ALL
      INTO dwd.t1 (id, amt) VALUES (id, amt)
      INTO dwd.t2 (id, amt) VALUES (id, amt)
    SELECT id, amt FROM ods.orders;
    """
    result = analyze_sql_lineage(sql, dialect="oracle")
    mappings = result.get("insert_mappings", [])
    t1_mappings = [m for m in mappings if m.get("target_table") == "dwd.t1"]
    t2_mappings = [m for m in mappings if m.get("target_table") == "dwd.t2"]
    assert len(t1_mappings) >= 2, f"t1 应有 id+amt 两条 mapping: {t1_mappings}"
    assert len(t2_mappings) >= 2, f"t2 应有 id+amt 两条 mapping: {t2_mappings}"


# ─── S5 PR7：显式 CURSOR 声明 + `FOR rec IN cur_x LOOP` 应能解析 ────────────────


def test_declared_cursor_for_loop_resolves_source():
    """`CURSOR cur_orders IS SELECT FROM ods.orders;` + `FOR rec IN cur_orders LOOP`
    应该把 ods.orders 作为 cursor_sources 补到 body INSERT 上。"""
    sql = """
    CREATE OR REPLACE PROCEDURE pkg.proc IS
      CURSOR cur_orders IS SELECT id, amt FROM ods.orders WHERE flag = 1;
    BEGIN
      FOR rec IN cur_orders LOOP
        INSERT INTO dwd.fact (id, amt) VALUES (rec.id, rec.amt);
      END LOOP;
    END;
    """
    result = analyze_sql_lineage(sql, dialect="oracle")
    assert _cursor_edge(result["graph_edges"], "ods.orders", "dwd.fact"), \
        f"显式 cursor 应解析 ods.orders → dwd.fact: {result['graph_edges']}"


def test_declared_cursor_with_join_multi_source():
    """声明的 cursor SELECT 含 JOIN 时，每个源表都应作为 cursor_sources。"""
    sql = """
    CREATE OR REPLACE PROCEDURE pkg.proc IS
      CURSOR cur_x IS
        SELECT b.id, c.code FROM ods.batches b JOIN ods.codes c ON b.id = c.bid;
    BEGIN
      FOR rec IN cur_x LOOP
        INSERT INTO dwd.fact (id, code) VALUES (rec.id, rec.code);
      END LOOP;
    END;
    """
    result = analyze_sql_lineage(sql, dialect="oracle")
    assert _cursor_edge(result["graph_edges"], "ods.batches", "dwd.fact")
    assert _cursor_edge(result["graph_edges"], "ods.codes", "dwd.fact")


def test_declared_cursor_multi_dml_in_loop_body():
    """显式 cursor + LOOP 体内多 DML —— PR2 的 scope 传播 + PR7 的 cursor 解析
    要协同：UPDATE/DELETE 段也要继承 cursor_sources。"""
    sql = """
    CREATE OR REPLACE PROCEDURE pkg.proc IS
      CURSOR cur_orders IS SELECT id FROM ods.orders;
    BEGIN
      FOR rec IN cur_orders LOOP
        INSERT INTO dwd.fact (id) VALUES (rec.id);
        UPDATE dwd.audit SET ts = sysdate WHERE id = rec.id;
      END LOOP;
    END;
    """
    result = analyze_sql_lineage(sql, dialect="oracle")
    assert _cursor_edge(result["graph_edges"], "ods.orders", "dwd.fact"), "INSERT 段"
    assert _cursor_edge(result["graph_edges"], "ods.orders", "dwd.audit"), "UPDATE 段"


def test_multiple_declared_cursors_independent_loops():
    """同一过程声明多个 cursor + 多个 LOOP，每个 LOOP 用自己的 cursor。"""
    sql = """
    CREATE OR REPLACE PROCEDURE pkg.proc IS
      CURSOR cur_a IS SELECT id FROM ods.orders;
      CURSOR cur_b IS SELECT code FROM ods.codes;
    BEGIN
      FOR r IN cur_a LOOP
        INSERT INTO dwd.fact (id) VALUES (r.id);
      END LOOP;
      FOR c IN cur_b LOOP
        DELETE FROM dwd.stale WHERE code = c.code;
      END LOOP;
    END;
    """
    result = analyze_sql_lineage(sql, dialect="oracle")
    assert _cursor_edge(result["graph_edges"], "ods.orders", "dwd.fact")
    assert _cursor_edge(result["graph_edges"], "ods.codes", "dwd.stale")
    # 不应交叉污染
    assert not _cursor_edge(result["graph_edges"], "ods.orders", "dwd.stale")
    assert not _cursor_edge(result["graph_edges"], "ods.codes", "dwd.fact")


def test_for_numeric_range_loop_unaffected():
    """`FOR i IN 1..10 LOOP` 这种数值范围循环不是 cursor，应不补任何 source。
    不应误识别为 cursor 引用。"""
    sql = """
    CREATE OR REPLACE PROCEDURE pkg.proc IS
    BEGIN
      FOR i IN 1..10 LOOP
        INSERT INTO dwd.t (n) VALUES (i);
      END LOOP;
    END;
    """
    result = analyze_sql_lineage(sql, dialect="oracle")
    cursor_edges = [e for e in result["graph_edges"] if e.get("edge_type") == "CURSOR_LOOP_INSERT"]
    assert cursor_edges == [], f"数值范围 LOOP 不应产生 CURSOR_LOOP_INSERT 边: {cursor_edges}"


# ─── S5 PR6：CREATE PROCEDURE / FUNCTION 不应进 target_summary ─────────────────


def test_create_procedure_not_in_target_summary():
    """`CREATE OR REPLACE PROCEDURE pkg.foo IS BEGIN ... END` —— pkg.foo 是
    过程名不是表名，过去会作为 fake target 出现在 target_summary 里。"""
    sql = """
    CREATE OR REPLACE PROCEDURE pkg.refresh_daily IS
    BEGIN
      TRUNCATE TABLE dwd.daily_summary;
      INSERT INTO dwd.daily_summary (id, amt) SELECT id, amt FROM ods.txn;
    END;
    """
    result = analyze_sql_lineage(sql, dialect="oracle")
    targets = [s.get("target_table") for s in result.get("target_summary", [])]
    assert "pkg.refresh_daily" not in targets, \
        f"pkg.refresh_daily 是过程名不是表，不该进 target_summary: {targets}"
    # 真实 target 仍应在
    assert "dwd.daily_summary" in targets


def test_create_function_not_in_target_summary():
    """CREATE FUNCTION 同样不该当 target_table。"""
    sql = """
    CREATE OR REPLACE FUNCTION pkg.fn RETURN NUMBER IS BEGIN
      RETURN 1;
    END;

    INSERT INTO dwd.t (a) VALUES (pkg.fn);
    """
    result = analyze_sql_lineage(sql, dialect="oracle")
    targets = [s.get("target_table") for s in result.get("target_summary", [])]
    assert "pkg.fn" not in targets, f"FUNCTION 名不该当 target: {targets}"
    assert "dwd.t" in targets


def test_create_table_still_in_target_summary():
    """PR6 不应破坏 CREATE TABLE 的 target_summary —— TABLE 是真实 DDL on table。"""
    sql = "CREATE TABLE dwd.t AS SELECT id FROM ods.s;"
    result = analyze_sql_lineage(sql, dialect="oracle")
    targets = [s.get("target_table") for s in result.get("target_summary", [])]
    assert "dwd.t" in targets


def test_create_view_still_in_target_summary():
    """CREATE VIEW 也是有效的"目标"，仍应入 summary。"""
    sql = "CREATE OR REPLACE VIEW dwd.v AS SELECT id FROM ods.s;"
    result = analyze_sql_lineage(sql, dialect="oracle")
    targets = [s.get("target_table") for s in result.get("target_summary", [])]
    assert "dwd.v" in targets


def test_package_var_not_misattributed_to_from_table():
    """S5 PR5：`SELECT g_app_id, count(*) FROM ods.orders` 不应把 g_app_id
    误归到 ods.orders 的 source_columns。它是 PL/SQL 变量，不是物理列。
    column-level mapping 应标 source_type=variable，且 graph_groups 把
    ods.orders 列在 dependency_tables 而非 source_tables。"""
    sql = """
    CREATE OR REPLACE PACKAGE BODY pkg_etl AS
      g_app_id CONSTANT VARCHAR2(32) := 'JY';
      PROCEDURE run IS BEGIN
        INSERT INTO dwd.t_jy (app_id, cnt)
        SELECT g_app_id, count(*) FROM ods.orders;
      END;
    END;
    """
    result = analyze_sql_lineage(sql, dialect="oracle")
    mappings = result.get("insert_mappings", [])
    app_id_map = next((m for m in mappings if m.get("target_column") == "app_id"), None)
    assert app_id_map is not None, f"应有 app_id 的 mapping: {mappings}"
    # PR5 关键：source_columns / source_tables 不再包含 g_app_id / ods.orders
    assert app_id_map["source_columns"] == [], \
        f"g_app_id 是变量，不该挂到 source_columns: {app_id_map}"
    assert app_id_map["source_tables"] == [], \
        f"g_app_id 是变量，FROM ods.orders 不该挂到这条 mapping 的 source_tables: {app_id_map}"
    # source_type 应标 variable，区分纯常量
    assert app_id_map["source_type"] == "variable"
    # variables 字段仍指明引用了哪个变量
    assert "g_app_id" in app_id_map.get("variables", [])

    # graph_groups: ods.orders 应作为 dependency_tables 出现，不作为 source_tables
    groups = result.get("graph_groups", [])
    target_group = next((g for g in groups if g.get("target_table") == "dwd.t_jy"), None)
    assert target_group is not None
    assert "ods.orders" in (target_group.get("dependency_tables") or []), \
        f"ods.orders 应作为 dependency: {target_group}"


def test_package_var_extraction_skips_proc_body_locals():
    """PROCEDURE 体内的局部变量不属于 package 顶层 —— 不应被抽进 result.variables
    （会跟模板变量串味）。BEGIN 后的变量靠 `assigned_value()` 兜底机制处理。"""
    sql = """
    CREATE OR REPLACE PACKAGE BODY pkg_etl AS
      g_top CONSTANT NUMBER := 1;
      PROCEDURE run IS
        v_local_only NUMBER := 99;
      BEGIN
        INSERT INTO dwd.t (a, b) VALUES (g_top, v_local_only);
      END;
    END;
    """
    result = analyze_sql_lineage(sql, dialect="oracle")
    variables = result.get("variables", [])
    assert _var_by(variables, "g_top") is not None, "package 顶层常量应被识别"
    # PROCEDURE 内的局部变量不应该进入 package_variables 列表
    assert _var_by(variables, "v_local_only") is None, \
        f"PROCEDURE 体内局部变量不应被当 package var: {variables}"


def test_cursor_source_tracking_segment_carries_cursor_sources():
    """procedure_segments 输出的每段应该有 cursor_sources 字段，cursor FOR 段非空，
    其他段为 []。"""
    sql = """
    CREATE OR REPLACE PROCEDURE pkg.mixed IS
    BEGIN
      INSERT INTO dwd.audit (msg) VALUES ('start');
      FOR rec IN (SELECT id FROM ods.batches) LOOP
        INSERT INTO dwd.fact (id) VALUES (rec.id);
      END LOOP;
    END;
    """
    result = analyze_sql_lineage(sql, dialect="oracle")
    segs = result.get("procedure_segments", [])
    assert segs, "应抽到 procedure_segments"
    # 每段都应有 cursor_sources 字段（即便是空 list）
    for seg in segs:
        assert "cursor_sources" in seg, \
            f"procedure_segments 输出每段都应该有 cursor_sources 字段: {seg}"
    # 至少一段（cursor 内的 INSERT）应携带 ods.batches
    cursor_segs = [s for s in segs if s.get("cursor_sources")]
    assert cursor_segs, f"应有段携带 cursor_sources: {[s.get('cursor_sources') for s in segs]}"
    assert any("ods.batches" in (s.get("cursor_sources") or []) for s in cursor_segs)
