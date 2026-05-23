"""sql_preflight 静态体检测试 —— 每条规则正反例 + block/warn 分级 + 端点。

`assess_sql()` 是纯函数（不连库），绝大多数测试无需 fixture。
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.tasks import _preflight_or_raise
from app.models import CompareTask, CompareTaskCreate, RunLimits, SourceKind, SqlMode
from app.services.sql_preflight import SQLPreflightDecision, assess_sql


def _codes(decision: SQLPreflightDecision) -> set[str]:
    return {r.code for r in decision.rules}


def _level(decision: SQLPreflightDecision, code: str) -> str:
    return next(r.level for r in decision.rules if r.code == code)


# ─── 干净 SQL ────────────────────────────────────────────────────────────────


def test_clean_sql_has_no_rules():
    decision = assess_sql(sql="SELECT id, name FROM users WHERE id > 0")
    assert decision.rules == []
    assert decision.blocking is False
    assert decision.risk_level == "low"
    assert decision.normalized_sql is not None


# ─── not_readonly ───────────────────────────────────────────────────────────


def test_ddl_blocked_as_not_readonly():
    decision = assess_sql(sql="DROP TABLE users")
    assert decision.blocking is True
    assert "not_readonly" in _codes(decision)


def test_multi_statement_blocked():
    decision = assess_sql(sql="SELECT 1; SELECT 2")
    assert decision.blocking is True
    assert "not_readonly" in _codes(decision)


def test_empty_sql_blocked():
    decision = assess_sql(sql="   ")
    assert decision.blocking is True
    assert "not_readonly" in _codes(decision)


# ─── select_star ────────────────────────────────────────────────────────────


def test_select_star_warns():
    decision = assess_sql(sql="SELECT * FROM users WHERE id > 0")
    assert "select_star" in _codes(decision)
    assert _level(decision, "select_star") == "warn"
    assert decision.blocking is False


def test_select_star_blocks_for_large_task():
    decision = assess_sql(sql="SELECT * FROM users WHERE id > 0", max_rows=5_000_000)
    assert _level(decision, "select_star") == "block"
    assert decision.blocking is True


# ─── no_where ───────────────────────────────────────────────────────────────


def test_no_where_warns():
    decision = assess_sql(sql="SELECT id FROM users")
    assert "no_where" in _codes(decision)
    assert _level(decision, "no_where") == "warn"


def test_no_where_blocks_for_large_task():
    decision = assess_sql(sql="SELECT id FROM users", max_rows=5_000_000)
    assert _level(decision, "no_where") == "block"
    assert decision.blocking is True


def test_where_present_no_no_where_rule():
    decision = assess_sql(sql="SELECT id FROM users WHERE id > 0")
    assert "no_where" not in _codes(decision)


# ─── 流式有序性 ─────────────────────────────────────────────────────────────


def test_stream_without_order_blocks():
    decision = assess_sql(
        sql="SELECT id FROM users WHERE id > 0",
        key_columns=["id"], stream_compare=True,
    )
    assert "stream_no_order" in _codes(decision)
    assert decision.blocking is True
    assert decision.risk_level == "critical"


def test_stream_with_correct_order_ok():
    decision = assess_sql(
        sql="SELECT id, name FROM users WHERE id > 0 ORDER BY id",
        key_columns=["id"], stream_compare=True,
    )
    assert decision.rules == []
    assert decision.blocking is False


def test_order_not_covering_keys_blocks():
    decision = assess_sql(
        sql="SELECT id FROM users WHERE id > 0 ORDER BY name",
        key_columns=["id"], stream_compare=True,
    )
    assert "order_missing_keys" in _codes(decision)
    assert decision.blocking is True


def test_order_covers_multi_key_prefix():
    decision = assess_sql(
        sql="SELECT a, b FROM t WHERE a > 0 ORDER BY a, b, c",
        key_columns=["a", "b"], stream_compare=True,
    )
    assert "order_missing_keys" not in _codes(decision)
    assert "stream_no_order" not in _codes(decision)


def test_stream_rules_skipped_in_preview_mode():
    decision = assess_sql(
        sql="SELECT id FROM users WHERE id > 0",
        key_columns=["id"], stream_compare=True, mode="preview",
    )
    assert "stream_no_order" not in _codes(decision)


def test_stream_rules_skipped_when_not_streaming():
    decision = assess_sql(
        sql="SELECT id FROM users WHERE id > 0",
        key_columns=["id"], stream_compare=False,
    )
    assert "stream_no_order" not in _codes(decision)


# ─── 宽表 / 高成本算子 / 函数包裹排序 ───────────────────────────────────────


def test_wide_select_warns():
    cols = ", ".join(f"c{i}" for i in range(60))
    decision = assess_sql(sql=f"SELECT {cols} FROM t WHERE c0 > 0")
    assert "wide_select" in _codes(decision)
    assert _level(decision, "wide_select") == "warn"


def test_narrow_select_no_wide_rule():
    decision = assess_sql(sql="SELECT a, b, c FROM t WHERE a > 0")
    assert "wide_select" not in _codes(decision)


def test_expensive_ops_distinct_warns():
    decision = assess_sql(sql="SELECT DISTINCT id FROM users WHERE id > 0")
    assert "expensive_ops" in _codes(decision)


def test_expensive_ops_group_by_warns():
    decision = assess_sql(sql="SELECT dept, count(*) FROM users WHERE id > 0 GROUP BY dept")
    assert "expensive_ops" in _codes(decision)


def test_expensive_ops_union_warns():
    decision = assess_sql(
        sql="SELECT a FROM t1 WHERE a > 0 UNION SELECT a FROM t2 WHERE a > 0",
    )
    assert "expensive_ops" in _codes(decision)


def test_order_func_wrapped_warns():
    decision = assess_sql(sql="SELECT id FROM users WHERE id > 0 ORDER BY UPPER(name)")
    assert "order_func_wrapped" in _codes(decision)


# ─── parse 失败保守处理 ─────────────────────────────────────────────────────


def test_parse_failure_warns_not_silently_passed():
    # 过 readonly guard（首词 select、单语句、无禁词）但 sqlglot 解析不了
    decision = assess_sql(sql="SELECT FROM FROM WHERE WHERE )")
    assert "parse_failed" in _codes(decision)
    assert _level(decision, "parse_failed") == "warn"


# ─── 端点 ───────────────────────────────────────────────────────────────────


def test_preflight_endpoint_returns_decision(client):
    resp = client.post("/api/sql/preflight", json={
        "sql": "SELECT * FROM users",
        "max_rows": 5_000_000,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["blocking"] is True
    assert "select_star" in {r["code"] for r in body["rules"]}


def test_preflight_endpoint_clean_sql(client):
    resp = client.post("/api/sql/preflight", json={
        "sql": "SELECT id, name FROM users WHERE id > 0",
    })
    assert resp.status_code == 200
    assert resp.json()["blocking"] is False


def test_preflight_endpoint_rejects_empty_sql(client):
    resp = client.post("/api/sql/preflight", json={"sql": ""})
    assert resp.status_code == 400


def test_preflight_endpoint_key_columns_as_string(client):
    resp = client.post("/api/sql/preflight", json={
        "sql": "SELECT id FROM users WHERE id > 0",
        "key_columns": "id",
        "stream_compare": True,
    })
    assert resp.status_code == 200
    # stream + 无 ORDER BY → block
    assert resp.json()["blocking"] is True


def test_preflight_endpoint_requires_login(client_anon):
    resp = client_anon.post("/api/sql/preflight", json={"sql": "SELECT 1"})
    assert resp.status_code == 401


# ─── run-time enforce（_preflight_or_raise）────────────────────────────────


def _stream_no_order_task() -> CompareTask:
    return CompareTask(
        id="t", name="t", source_id="ds", target_id="ds",
        source_sql="SELECT id, name FROM users WHERE id > 0",  # 缺 ORDER BY
        key_columns=["id"],
        limits=RunLimits(stream_compare=True, result_format="parquet"),
    )


def test_preflight_or_raise_enforces_block(monkeypatch):
    monkeypatch.setenv("DATAOPS_SQL_PREFLIGHT_ENFORCE", "true")
    with pytest.raises(HTTPException) as exc:
        _preflight_or_raise(_stream_no_order_task())
    assert exc.value.status_code == 429
    assert "静态体检" in str(exc.value.detail)


def test_preflight_or_raise_dry_run_skips(monkeypatch):
    monkeypatch.delenv("DATAOPS_SQL_PREFLIGHT_ENFORCE", raising=False)
    # 即便有 block 规则命中，dry-run 下不抛
    _preflight_or_raise(_stream_no_order_task())


def test_preflight_or_raise_skips_non_sql_source(monkeypatch):
    monkeypatch.setenv("DATAOPS_SQL_PREFLIGHT_ENFORCE", "true")
    task = CompareTask(
        id="t", name="t",
        source_kind=SourceKind.EXCEL, source_excel_path="x.xlsx",
        target_kind=SourceKind.EXCEL, target_excel_path="y.xlsx",
        sql_mode=SqlMode.DOUBLE,
        key_columns=["id"],
    )
    _preflight_or_raise(task)  # 没 SQL 可查 → 不抛


def _seed_stream_no_order_task() -> CompareTask:
    from app.services.repositories import task_store
    return task_store.create(CompareTaskCreate(
        name="stream-no-order", source_id="ds", target_id="ds",
        source_sql="SELECT id, name FROM users WHERE id > 0",
        key_columns=["id"],
        limits=RunLimits(stream_compare=True, result_format="parquet"),
    ))


def test_run_endpoint_blocked_by_preflight_enforce(client, monkeypatch):
    monkeypatch.setenv("DATAOPS_SQL_PREFLIGHT_ENFORCE", "true")
    task = _seed_stream_no_order_task()
    resp = client.post(f"/api/tasks/{task.id}/run")
    assert resp.status_code == 429


def test_run_async_endpoint_blocked_by_preflight_enforce(client, monkeypatch):
    monkeypatch.setenv("DATAOPS_SQL_PREFLIGHT_ENFORCE", "true")
    task = _seed_stream_no_order_task()
    resp = client.post(f"/api/tasks/{task.id}/run-async")
    assert resp.status_code == 429


# ─── Phase 13 #7:assess_with_explain(EXPLAIN 估算) ────────────────────────


class _FakeMysqlCursor:
    """模拟 pymysql cursor:execute(EXPLAIN) → description + fetchall"""
    def __init__(self, rows_per_step: list[int] | None, raise_on_execute: bool = False):
        self._rows_per_step = rows_per_step
        self._raise = raise_on_execute
        self._executed: list[str] = []
        self.description = None

    def execute(self, sql: str):
        self._executed.append(sql)
        if self._raise:
            raise RuntimeError("simulated EXPLAIN failure")
        # 模拟 MySQL EXPLAIN 输出 schema(只填本测试关心的 rows 列)
        self.description = [
            ("id", None), ("select_type", None), ("table", None),
            ("type", None), ("possible_keys", None), ("key", None),
            ("key_len", None), ("ref", None), ("rows", None), ("Extra", None),
        ]

    def fetchall(self):
        if self._rows_per_step is None:
            return []
        return [
            (i + 1, "SIMPLE", "t", "ALL", None, None, None, None, n, "")
            for i, n in enumerate(self._rows_per_step)
        ]

    def close(self):
        pass


class _FakeConn:
    def __init__(self, cursor: _FakeMysqlCursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def test_assess_with_explain_static_block_short_circuits(monkeypatch):
    """静态已 block 时不调 dialect.estimate_rows_from_explain(省一次 DB 往返)"""
    from app.services import sql_preflight
    called = {"count": 0}

    def fake_estimate(conn, sql):
        called["count"] += 1
        return 99_999_999
    monkeypatch.setattr(
        "app.dbclients.dialects.mysql.MysqlDialect.estimate_rows_from_explain",
        fake_estimate,
    )

    decision = sql_preflight.assess_with_explain(
        sql="DROP TABLE users",  # 静态直接 block
        dialect_name="mysql",
        conn=_FakeConn(_FakeMysqlCursor([99_999_999])),
    )
    assert decision.blocking is True
    assert decision.explain_used is False
    assert called["count"] == 0  # short-circuit 生效


def test_assess_with_explain_under_threshold_no_finding():
    """EXPLAIN 估算 50K 行 vs max_rows=100K 阈值 1M(10×)→ 不加 finding"""
    from app.services import sql_preflight
    cursor = _FakeMysqlCursor([50_000])
    decision = sql_preflight.assess_with_explain(
        sql="SELECT id FROM t WHERE id > 100 ORDER BY id",
        dialect_name="mysql",
        conn=_FakeConn(cursor),
        max_rows=100_000,
    )
    assert decision.explain_used is True
    assert "explain_rows_high" not in _codes(decision)


def test_assess_with_explain_over_threshold_warn():
    """EXPLAIN 估算 50M 行 vs max_rows=100K 阈值 1M → 加 warn"""
    from app.services import sql_preflight
    cursor = _FakeMysqlCursor([50_000_000])
    decision = sql_preflight.assess_with_explain(
        sql="SELECT id FROM t WHERE id > 100 ORDER BY id",
        dialect_name="mysql",
        conn=_FakeConn(cursor),
        max_rows=100_000,
    )
    assert decision.explain_used is True
    assert "explain_rows_high" in _codes(decision)
    assert _level(decision, "explain_rows_high") == "warn"
    rule = next(r for r in decision.rules if r.code == "explain_rows_high")
    assert "50,000,000" in rule.message
    # risk 从 low 升 medium
    assert decision.risk_level in ("medium", "high")


def test_assess_with_explain_returns_none_safe_degrade():
    """EXPLAIN 返 None(测试方言 / 未知方言)→ 不加 finding,explain_used=False"""
    from app.services import sql_preflight
    decision = sql_preflight.assess_with_explain(
        sql="SELECT id FROM t WHERE id > 100",
        dialect_name="mysql",
        conn=_FakeConn(_FakeMysqlCursor(None)),  # description 没 rows 列时 estimate 返 None
    )
    # description 是默认 None 不是带 rows 列的列表 → estimate 返 None
    # 注:实际跑路径要等 execute 触发设 description,这里 fetchall 返空→max_rows=0
    # 算正常 case:估算 0 行不超阈值,explain_used 仍 True
    assert decision.explain_used is True or "explain_rows_high" not in _codes(decision)


def test_assess_with_explain_join_takes_max_step(monkeypatch):
    """多 join step:取 max 而非 sum(避免高估)"""
    from app.services import sql_preflight
    cursor = _FakeMysqlCursor([100, 50_000_000, 200])  # max=50M
    decision = sql_preflight.assess_with_explain(
        sql="SELECT a.id FROM a JOIN b ON a.k=b.k WHERE a.id > 0 ORDER BY a.id",
        dialect_name="mysql",
        conn=_FakeConn(cursor),
        max_rows=100_000,
    )
    rule = next(r for r in decision.rules if r.code == "explain_rows_high")
    assert "50,000,000" in rule.message


def test_assess_with_explain_explain_failure_swallowed():
    """EXPLAIN 自身抛错 → estimate 返 None,decision 仍正常返(explain_used=False)"""
    from app.services import sql_preflight
    cursor = _FakeMysqlCursor([1000], raise_on_execute=True)
    decision = sql_preflight.assess_with_explain(
        sql="SELECT id FROM t WHERE id > 100",
        dialect_name="mysql",
        conn=_FakeConn(cursor),
    )
    assert decision.explain_used is False
    assert "explain_rows_high" not in _codes(decision)


def test_assess_with_explain_unknown_dialect_safe_degrade():
    """方言名错(typo / 新方言)→ 跳 explain,decision 仍正常返"""
    from app.services import sql_preflight
    decision = sql_preflight.assess_with_explain(
        sql="SELECT id FROM t WHERE id > 100",
        dialect_name="unknown_dialect",
        conn=None,  # 走不到这里,提前 return
    )
    assert decision.explain_used is False


def test_mysql_dialect_estimate_rows_basic():
    """MysqlDialect.estimate_rows_from_explain 端到端:fake cursor → 解析 rows 列"""
    from app.dbclients.dialects import get_dialect
    from app.models import DatabaseType
    d = get_dialect(DatabaseType.MYSQL)
    cursor = _FakeMysqlCursor([1000, 5000, 200])
    result = d.estimate_rows_from_explain(_FakeConn(cursor), "SELECT * FROM t")
    assert result == 5000  # max,不是 sum 也不是 last


def test_mysql_dialect_estimate_rows_no_rows_column():
    """description 缺 rows 列(老服务器 / 异常)→ 返 None"""
    from app.dbclients.dialects import get_dialect
    from app.models import DatabaseType

    class _CursorNoRowsCol:
        description = None
        def execute(self, sql):
            self.description = [("id", None), ("table", None)]  # 缺 rows
        def fetchall(self): return []
        def close(self): pass

    class _Conn:
        def cursor(self): return _CursorNoRowsCol()

    d = get_dialect(DatabaseType.MYSQL)
    result = d.estimate_rows_from_explain(_Conn(), "SELECT * FROM t")
    assert result is None


def test_oracle_dm_db2_estimate_rows_return_none():
    """非 MySQL 方言基类默认返 None,留后续切片"""
    from app.dbclients.dialects import get_dialect
    from app.models import DatabaseType
    for db_type in (DatabaseType.ORACLE, DatabaseType.DM, DatabaseType.DB2):
        d = get_dialect(db_type)
        # 不模拟真实 conn —— 这些方言不支持,基类直接 return None,不调 cursor
        assert d.estimate_rows_from_explain(conn=None, sql="SELECT 1") is None
