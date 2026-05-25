"""SQL Workbench executor 单测 —— mock fetch_rows_with_schema,验证:
- sql_guard 拦 DML / DDL → success=False
- success 路径列序对齐 + truncated 计算
- driver error → success=False
- 单元格类型归一化(datetime / Decimal / bytes)
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from app.dbclients.factory import DbClientError, QueryRows
from app.sqlide.executor import execute_sql


def _fake_ds() -> Any:
    return SimpleNamespace(id="ds-1", name="demo", db_type=SimpleNamespace(value="MySQL"))


def _stub_fetch(rows: list[dict[str, Any]], columns: list[str]):
    def _inner(source, sql, **kwargs):
        return QueryRows(rows=rows, columns=columns, raw_columns=columns, warnings=[])
    return _inner


def test_dml_rejected_by_sql_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    # fetch 不应被调用
    monkeypatch.setattr("app.sqlide.executor.fetch_rows_with_schema",
                        lambda *a, **kw: pytest.fail("fetch should not be called"))
    resp = execute_sql(_fake_ds(), "DELETE FROM users")
    assert resp.success is False
    assert "Forbidden" in resp.error or "Only SELECT" in resp.error


def test_empty_sql_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    resp = execute_sql(_fake_ds(), "  ")
    assert resp.success is False
    assert "empty" in resp.error.lower()


def test_multiple_statements_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    resp = execute_sql(_fake_ds(), "SELECT 1; SELECT 2")
    assert resp.success is False


def test_select_success_columns_and_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.sqlide.executor.fetch_rows_with_schema",
        _stub_fetch(
            rows=[{"id": 1, "name": "a"}, {"id": 2, "name": "b"}],
            columns=["id", "name"],
        ),
    )
    resp = execute_sql(_fake_ds(), "SELECT id, name FROM users", max_rows=10)
    assert resp.success is True
    assert resp.columns == ["id", "name"]
    assert resp.rows == [[1, "a"], [2, "b"]]
    assert resp.row_count == 2
    assert resp.truncated is False


def test_truncation_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    # max_rows=2 → executor 拉 3 行;返 3 行说明 > 2 → truncated=True
    monkeypatch.setattr(
        "app.sqlide.executor.fetch_rows_with_schema",
        _stub_fetch(
            rows=[{"id": i} for i in range(3)],
            columns=["id"],
        ),
    )
    resp = execute_sql(_fake_ds(), "SELECT id FROM users", max_rows=2)
    assert resp.success is True
    assert resp.row_count == 2  # 只展示 max_rows 行
    assert resp.truncated is True


def test_driver_error_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*a, **kw):
        raise DbClientError("connection refused")
    monkeypatch.setattr("app.sqlide.executor.fetch_rows_with_schema", _raise)
    resp = execute_sql(_fake_ds(), "SELECT 1")
    assert resp.success is False
    assert "connection refused" in resp.error


def test_cell_serialization(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.sqlide.executor.fetch_rows_with_schema",
        _stub_fetch(
            rows=[{
                "dt": datetime(2026, 5, 26, 10, 30, 0),
                "dec": Decimal("3.14"),
                "blob": b"\x00\x01",
                "n": None,
                "str": "hello",
            }],
            columns=["dt", "dec", "blob", "n", "str"],
        ),
    )
    resp = execute_sql(_fake_ds(), "SELECT 1")
    assert resp.success is True
    row = resp.rows[0]
    assert row[0] == "2026-05-26T10:30:00"  # datetime → iso
    assert row[1] == 3.14                   # Decimal → float
    assert row[2] == "0001"                 # bytes → hex
    assert row[3] is None
    assert row[4] == "hello"


def test_max_rows_hard_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """caller 传超过硬上限的 max_rows 时执行端会 clamp。"""
    seen_max = []
    def _capture(source, sql, **kwargs):
        seen_max.append(kwargs.get("max_rows"))
        return QueryRows(rows=[], columns=[], raw_columns=[], warnings=[])
    monkeypatch.setattr("app.sqlide.executor.fetch_rows_with_schema", _capture)
    execute_sql(_fake_ds(), "SELECT 1", max_rows=99_999_999)
    # _MAX_ROWS_HARD_CAP=10_000,执行时 fetch 是 cap+1
    assert seen_max == [10_001]
