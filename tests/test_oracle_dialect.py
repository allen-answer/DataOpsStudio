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
