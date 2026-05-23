"""DB 语句超时测试 —— 方言 statement_timeout_sql 输出 + factory 的 best-effort
下发（MySQL 下发 / 其它方言跳过 / env 关闭 / 下发失败被吞）。
"""
from __future__ import annotations

import pytest

from app.dbclients.dialects import get_dialect
from app.dbclients.factory import (
    _apply_statement_timeout,
    _statement_timeout_seconds,
    query_timeout_override,
)
from app.models import DatabaseType


class _FakeCursor:
    """记录 execute 调用的假 cursor；fail=True 时模拟不支持该变量的服务器。"""

    def __init__(self, fail: bool = False) -> None:
        self.executed: list[str] = []
        self.fail = fail

    def execute(self, sql: str) -> None:
        if self.fail:
            raise RuntimeError("Unknown system variable 'MAX_EXECUTION_TIME'")
        self.executed.append(sql)


# ─── 方言 statement_timeout_sql ─────────────────────────────────────────────


def test_mysql_timeout_sql_is_milliseconds():
    assert get_dialect(DatabaseType.MYSQL).statement_timeout_sql(900) == \
        "SET SESSION MAX_EXECUTION_TIME=900000"
    assert get_dialect(DatabaseType.MYSQL).statement_timeout_sql(30) == \
        "SET SESSION MAX_EXECUTION_TIME=30000"


def test_oracle_dm_db2_have_no_timeout_sql():
    # Oracle / DM 走连接属性路径(apply_call_timeout),非 SQL —— 返回 None
    # DB2 都不支持,留缺口
    assert get_dialect(DatabaseType.ORACLE).statement_timeout_sql(900) is None
    assert get_dialect(DatabaseType.DM).statement_timeout_sql(900) is None
    assert get_dialect(DatabaseType.DB2).statement_timeout_sql(900) is None


# ─── apply_call_timeout(连接属性路径,Oracle / DM)──────────────────────────


class _FakeConn:
    """模拟 oracledb / dmPython Connection,支持 callTimeout 属性。

    支持矩阵:
    - `supports=True` (默认):正常接受 conn.callTimeout=ms
    - `supports=False`:setattr 抛 AttributeError(老驱动 / 不兼容版本)
    """

    def __init__(self, supports: bool = True) -> None:
        # 跳过自定义 __setattr__ 直接初始化,避免 init 阶段触发 AttributeError
        object.__setattr__(self, "_supports", supports)
        object.__setattr__(self, "callTimeout", None)

    def __setattr__(self, name: str, value) -> None:
        if name == "callTimeout" and not getattr(self, "_supports", True):
            raise AttributeError("driver doesn't support callTimeout")
        object.__setattr__(self, name, value)


def test_oracle_apply_call_timeout_sets_milliseconds():
    conn = _FakeConn()
    assert get_dialect(DatabaseType.ORACLE).apply_call_timeout(conn, 900) is True
    assert conn.callTimeout == 900_000


def test_dm_apply_call_timeout_inherits_oracle():
    # DmDialect 继承 OracleDialect,无 override —— 同行为
    conn = _FakeConn()
    assert get_dialect(DatabaseType.DM).apply_call_timeout(conn, 120) is True
    assert conn.callTimeout == 120_000


def test_apply_call_timeout_returns_false_when_unsupported():
    # 老 dmPython / 不支持 callTimeout 的驱动 —— setattr 抛 AttributeError
    conn = _FakeConn(supports=False)
    assert get_dialect(DatabaseType.ORACLE).apply_call_timeout(conn, 900) is False


def test_mysql_apply_call_timeout_returns_false_by_default():
    # MySQL 不走连接属性,走 SQL 路径 —— Dialect 基类默认 False
    conn = _FakeConn()
    assert get_dialect(DatabaseType.MYSQL).apply_call_timeout(conn, 900) is False
    assert conn.callTimeout is None  # 没碰


# ─── _statement_timeout_seconds（env 解析）──────────────────────────────────


def test_timeout_seconds_default(monkeypatch):
    monkeypatch.delenv("DATAOPS_DB_STATEMENT_TIMEOUT_SECONDS", raising=False)
    assert _statement_timeout_seconds() == 900.0


def test_timeout_seconds_from_env(monkeypatch):
    monkeypatch.setenv("DATAOPS_DB_STATEMENT_TIMEOUT_SECONDS", "120")
    assert _statement_timeout_seconds() == 120.0


def test_timeout_seconds_zero_disables(monkeypatch):
    monkeypatch.setenv("DATAOPS_DB_STATEMENT_TIMEOUT_SECONDS", "0")
    assert _statement_timeout_seconds() == 0.0


def test_timeout_seconds_negative_disables(monkeypatch):
    monkeypatch.setenv("DATAOPS_DB_STATEMENT_TIMEOUT_SECONDS", "-5")
    assert _statement_timeout_seconds() == 0.0


def test_timeout_seconds_garbage_falls_back(monkeypatch):
    monkeypatch.setenv("DATAOPS_DB_STATEMENT_TIMEOUT_SECONDS", "abc")
    assert _statement_timeout_seconds() == 900.0


# ─── _apply_statement_timeout ───────────────────────────────────────────────


def test_apply_issues_set_for_mysql(monkeypatch):
    monkeypatch.setenv("DATAOPS_DB_STATEMENT_TIMEOUT_SECONDS", "300")
    cursor = _FakeCursor()
    _apply_statement_timeout(cursor, DatabaseType.MYSQL)
    assert cursor.executed == ["SET SESSION MAX_EXECUTION_TIME=300000"]


def test_apply_skips_db2(monkeypatch):
    # DB2 仍是文档化缺口(无 SQL 也无 callTimeout 实现)
    monkeypatch.setenv("DATAOPS_DB_STATEMENT_TIMEOUT_SECONDS", "300")
    cursor = _FakeCursor()
    _apply_statement_timeout(cursor, DatabaseType.DB2)
    assert cursor.executed == []


def test_apply_oracle_uses_call_timeout(monkeypatch):
    # Oracle 现在走连接属性路径:不走 cursor.execute,设 conn.callTimeout
    monkeypatch.setenv("DATAOPS_DB_STATEMENT_TIMEOUT_SECONDS", "300")
    cursor = _FakeCursor()
    conn = _FakeConn()
    _apply_statement_timeout(cursor, DatabaseType.ORACLE, conn)
    assert cursor.executed == []
    assert conn.callTimeout == 300_000


def test_apply_dm_uses_call_timeout(monkeypatch):
    monkeypatch.setenv("DATAOPS_DB_STATEMENT_TIMEOUT_SECONDS", "60")
    cursor = _FakeCursor()
    conn = _FakeConn()
    _apply_statement_timeout(cursor, DatabaseType.DM, conn)
    assert cursor.executed == []
    assert conn.callTimeout == 60_000


def test_apply_oracle_no_connection_falls_through(monkeypatch):
    # 老 caller 不传 connection —— Oracle 无 SQL fallback,什么都不做(向后兼容)
    monkeypatch.setenv("DATAOPS_DB_STATEMENT_TIMEOUT_SECONDS", "300")
    cursor = _FakeCursor()
    _apply_statement_timeout(cursor, DatabaseType.ORACLE)
    assert cursor.executed == []


def test_apply_oracle_unsupported_driver_swallowed(monkeypatch):
    # 老 dmPython 不支持 callTimeout —— setattr AttributeError 被吞,不影响查询
    monkeypatch.setenv("DATAOPS_DB_STATEMENT_TIMEOUT_SECONDS", "300")
    cursor = _FakeCursor()
    conn = _FakeConn(supports=False)
    _apply_statement_timeout(cursor, DatabaseType.ORACLE, conn)  # 不抛
    assert cursor.executed == []
    assert getattr(conn, "callTimeout", None) is None


def test_apply_mysql_with_connection_still_uses_sql(monkeypatch):
    # MySQL 即使 caller 传 connection,apply_call_timeout 默认 False → fallback SQL
    monkeypatch.setenv("DATAOPS_DB_STATEMENT_TIMEOUT_SECONDS", "300")
    cursor = _FakeCursor()
    conn = _FakeConn()
    _apply_statement_timeout(cursor, DatabaseType.MYSQL, conn)
    assert cursor.executed == ["SET SESSION MAX_EXECUTION_TIME=300000"]
    assert conn.callTimeout is None


def test_apply_skips_when_disabled(monkeypatch):
    monkeypatch.setenv("DATAOPS_DB_STATEMENT_TIMEOUT_SECONDS", "0")
    cursor = _FakeCursor()
    _apply_statement_timeout(cursor, DatabaseType.MYSQL)
    assert cursor.executed == []


def test_apply_swallows_set_failure(monkeypatch):
    # MariaDB / 老版本不认 MAX_EXECUTION_TIME —— 下发报错必须被吞，不能拖垮真查询
    monkeypatch.setenv("DATAOPS_DB_STATEMENT_TIMEOUT_SECONDS", "300")
    cursor = _FakeCursor(fail=True)
    _apply_statement_timeout(cursor, DatabaseType.MYSQL)  # 不抛异常即通过
    assert cursor.executed == []


# ─── Phase 13 query_timeout_override 单任务覆盖 ─────────────────────────────


def test_query_timeout_override_takes_precedence_over_env(monkeypatch):
    monkeypatch.setenv("DATAOPS_DB_STATEMENT_TIMEOUT_SECONDS", "900")
    with query_timeout_override(60):
        assert _statement_timeout_seconds() == 60.0


def test_query_timeout_override_zero_disables_per_task(monkeypatch):
    """显式 0 → 该任务关闭超时(即使全局开着)"""
    monkeypatch.setenv("DATAOPS_DB_STATEMENT_TIMEOUT_SECONDS", "900")
    with query_timeout_override(0):
        assert _statement_timeout_seconds() == 0.0


def test_query_timeout_override_none_falls_back_to_env(monkeypatch):
    """None → 走 env 默认(向后兼容老 task)"""
    monkeypatch.setenv("DATAOPS_DB_STATEMENT_TIMEOUT_SECONDS", "1200")
    with query_timeout_override(None):
        assert _statement_timeout_seconds() == 1200.0


def test_query_timeout_override_resets_after_with_block(monkeypatch):
    monkeypatch.setenv("DATAOPS_DB_STATEMENT_TIMEOUT_SECONDS", "900")
    with query_timeout_override(60):
        assert _statement_timeout_seconds() == 60.0
    # 出 with 块后回到 env
    assert _statement_timeout_seconds() == 900.0


def test_query_timeout_override_applies_via_mysql_path(monkeypatch):
    """端到端:override 60s 时 MySQL 下发 60000ms,不再是 env 默认 900000"""
    monkeypatch.setenv("DATAOPS_DB_STATEMENT_TIMEOUT_SECONDS", "900")
    cursor = _FakeCursor()
    with query_timeout_override(60):
        _apply_statement_timeout(cursor, DatabaseType.MYSQL)
    assert cursor.executed == ["SET SESSION MAX_EXECUTION_TIME=60000"]


def test_query_timeout_override_applies_via_oracle_call_timeout(monkeypatch):
    """端到端:override 1800s 时 Oracle conn.callTimeout=1800000ms"""
    monkeypatch.setenv("DATAOPS_DB_STATEMENT_TIMEOUT_SECONDS", "900")
    cursor = _FakeCursor()
    conn = _FakeConn()
    with query_timeout_override(1800):
        _apply_statement_timeout(cursor, DatabaseType.ORACLE, conn)
    assert conn.callTimeout == 1_800_000
