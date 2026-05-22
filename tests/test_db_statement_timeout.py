"""DB 语句超时测试 —— 方言 statement_timeout_sql 输出 + factory 的 best-effort
下发（MySQL 下发 / 其它方言跳过 / env 关闭 / 下发失败被吞）。
"""
from __future__ import annotations

import pytest

from app.dbclients.dialects import get_dialect
from app.dbclients.factory import _apply_statement_timeout, _statement_timeout_seconds
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
    # Oracle / DM 的语句超时不是一条 SQL 能搞定的 —— 返回 None，是文档化缺口
    assert get_dialect(DatabaseType.ORACLE).statement_timeout_sql(900) is None
    assert get_dialect(DatabaseType.DM).statement_timeout_sql(900) is None
    assert get_dialect(DatabaseType.DB2).statement_timeout_sql(900) is None


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


def test_apply_skips_oracle(monkeypatch):
    monkeypatch.setenv("DATAOPS_DB_STATEMENT_TIMEOUT_SECONDS", "300")
    cursor = _FakeCursor()
    _apply_statement_timeout(cursor, DatabaseType.ORACLE)
    assert cursor.executed == []


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
