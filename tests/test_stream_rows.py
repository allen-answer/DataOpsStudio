"""stream_rows context manager 单测 —— 用 fake connection / cursor 验证流式语义。

真 SSCursor 集成测试需要 MySQL server,跑在 docker compose --profile demo-db 或 CI。
本文件只覆盖纯 Python 控制流:cursor 选择 / chunk 迭代 / max_rows 截断 / 异常路径。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.dbclients.factory import _create_streaming_cursor, stream_rows
from app.models import DataSource, DatabaseType


# ─── _create_streaming_cursor ────────────────────────────────────────────────

class _FakeSSCursorClass:
    """Stand-in for pymysql.cursors.SSCursor — just need cls identity."""


def test_pymysql_uses_sscursor():
    """MySQL pymysql 路径应该用 SSCursor。本地没装 pymysql 时用 sys.modules 注入 fake module。"""
    import sys
    import types

    fake_conn = MagicMock()
    fake_cursor = MagicMock()
    fake_conn.cursor.return_value = fake_cursor

    # 在 sys.modules 里 inject 一个 fake pymysql.cursors module,让 import 成功
    fake_pymysql = types.ModuleType("pymysql")
    fake_pymysql_cursors = types.ModuleType("pymysql.cursors")
    fake_pymysql_cursors.SSCursor = _FakeSSCursorClass
    fake_pymysql.cursors = fake_pymysql_cursors

    old_pymysql = sys.modules.get("pymysql")
    old_pymysql_cursors = sys.modules.get("pymysql.cursors")
    sys.modules["pymysql"] = fake_pymysql
    sys.modules["pymysql.cursors"] = fake_pymysql_cursors
    try:
        cursor = _create_streaming_cursor(fake_conn, "pymysql")
    finally:
        if old_pymysql is None:
            sys.modules.pop("pymysql", None)
        else:
            sys.modules["pymysql"] = old_pymysql
        if old_pymysql_cursors is None:
            sys.modules.pop("pymysql.cursors", None)
        else:
            sys.modules["pymysql.cursors"] = old_pymysql_cursors

    fake_conn.cursor.assert_called_once_with(_FakeSSCursorClass)
    assert cursor is fake_cursor


def test_pymysql_sscursor_unavailable_fallback_default():
    """SSCursor 拿不到时 fallback 普通 cursor,不 raise。

    本地实际场景:pymysql 未安装 → import 抛 ImportError → 走 fallback。
    """
    fake_conn = MagicMock()
    fake_default_cursor = MagicMock()
    fake_conn.cursor.return_value = fake_default_cursor

    # 不 inject pymysql → import 失败 → fallback
    cursor = _create_streaming_cursor(fake_conn, "pymysql")
    # fallback 到无参 cursor()
    fake_conn.cursor.assert_called_once_with()
    assert cursor is fake_default_cursor


def test_oracle_uses_normal_cursor():
    """Oracle / DM / DB2 走普通 cursor,不引入 SSCursor。"""
    fake_conn = MagicMock()
    fake_cursor = MagicMock()
    fake_conn.cursor.return_value = fake_cursor

    cursor = _create_streaming_cursor(fake_conn, "oracledb")

    # cursor() 不带参数调用
    fake_conn.cursor.assert_called_once_with()
    assert cursor is fake_cursor


# ─── stream_rows chunk 迭代 ──────────────────────────────────────────────────

def _make_fake_source() -> DataSource:
    """造一个能过 first_available_module 的最小 DataSource。"""
    return DataSource(
        id="ds-test",
        name="test",
        db_type=DatabaseType.MYSQL,
        host="x",
        port=3306,
        username="u",
        password="p",
        database="db",
    )


@pytest.fixture
def fake_pool_borrow(monkeypatch):
    """patch _pool.borrow 让它返回 fake connection。"""
    fake_conn = MagicMock()
    from contextlib import contextmanager

    @contextmanager
    def fake_borrow(source, factory):
        yield fake_conn

    monkeypatch.setattr("app.dbclients.factory._pool.borrow", fake_borrow)
    monkeypatch.setattr("app.dbclients.factory.first_available_module", lambda dt: "pymysql")
    monkeypatch.setattr("app.dbclients.factory._apply_statement_timeout", lambda *a, **kw: None)
    return fake_conn


def _setup_cursor_to_yield_chunks(fake_conn, chunks: list[list[tuple]], columns: list[str]):
    """配置 fake cursor 让 fetchmany 按 chunks 顺序返回。"""
    cursor = MagicMock()
    fake_conn.cursor.return_value = cursor
    cursor.description = [(c,) for c in columns]
    cursor.fetchmany.side_effect = chunks + [[]]  # 末尾空 list 表示 EOF
    return cursor


def test_stream_rows_yields_chunks_as_dicts(fake_pool_borrow):
    cursor = _setup_cursor_to_yield_chunks(
        fake_pool_borrow,
        chunks=[
            [(1, "a"), (2, "b")],
            [(3, "c")],
        ],
        columns=["id", "name"],
    )
    src = _make_fake_source()

    with stream_rows(src, "SELECT id, name FROM t", chunk_size=2) as (columns, chunk_iter):
        assert columns == ["id", "name"]
        all_chunks = list(chunk_iter)

    assert all_chunks == [
        [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}],
        [{"id": 3, "name": "c"}],
    ]


def test_stream_rows_respects_max_rows(fake_pool_borrow):
    """max_rows=3 时即使 driver 返了 5 行也只 yield 3 行,生成器自然停止。"""
    cursor = _setup_cursor_to_yield_chunks(
        fake_pool_borrow,
        chunks=[
            [(1,), (2,), (3,), (4,), (5,)],  # 一批 5 行
        ],
        columns=["id"],
    )
    src = _make_fake_source()

    with stream_rows(src, "SELECT id FROM t", max_rows=3) as (columns, chunk_iter):
        rows = []
        for chunk in chunk_iter:
            rows.extend(chunk)

    assert rows == [{"id": 1}, {"id": 2}, {"id": 3}]


def test_stream_rows_max_rows_none_pulls_all(fake_pool_borrow):
    """max_rows=None 拉到表尾。"""
    cursor = _setup_cursor_to_yield_chunks(
        fake_pool_borrow,
        chunks=[
            [(1,), (2,)],
            [(3,)],
        ],
        columns=["id"],
    )
    src = _make_fake_source()

    with stream_rows(src, "SELECT id FROM t", max_rows=None) as (columns, chunk_iter):
        all_rows = [r for chunk in chunk_iter for r in chunk]

    assert all_rows == [{"id": 1}, {"id": 2}, {"id": 3}]


def test_stream_rows_cursor_closed_on_exit(fake_pool_borrow):
    """context manager 退出后 cursor.close() 必须被调用,避免 SSCursor 泄漏 server 资源。"""
    cursor = _setup_cursor_to_yield_chunks(
        fake_pool_borrow,
        chunks=[[(1,)]],
        columns=["id"],
    )
    src = _make_fake_source()

    with stream_rows(src, "SELECT id FROM t") as (columns, chunk_iter):
        list(chunk_iter)

    cursor.close.assert_called_once()


def test_stream_rows_cursor_closed_even_on_exception(fake_pool_borrow):
    """caller 在 iter 中抛异常,cursor 仍要 close 不能泄漏。"""
    cursor = _setup_cursor_to_yield_chunks(
        fake_pool_borrow,
        chunks=[[(1,), (2,)]],
        columns=["id"],
    )
    src = _make_fake_source()

    with pytest.raises(RuntimeError, match="caller raised"):
        with stream_rows(src, "SELECT id FROM t") as (columns, chunk_iter):
            for chunk in chunk_iter:
                raise RuntimeError("caller raised")

    cursor.close.assert_called_once()


def test_stream_rows_execute_failure_wraps_dbclient_error(fake_pool_borrow):
    cursor = MagicMock()
    fake_pool_borrow.cursor.return_value = cursor
    cursor.execute.side_effect = RuntimeError("syntax error")

    from app.dbclients.factory import DbClientError
    src = _make_fake_source()

    with pytest.raises(DbClientError, match="execute SQL failed"):
        with stream_rows(src, "INVALID") as _:
            pass


# ─── JSON streaming writer ───────────────────────────────────────────────────

def test_json_writer_streaming_output(tmp_path):
    """改造后的 _write_json 输出应仍是合法 JSON 数组,空 / 单行 / 多行都对。"""
    from app.services.sql_export import _write_json
    import json

    # 多行
    out = tmp_path / "multi.json"
    count, size = _write_json(out, ["id", "name"], [[1, "a"], [2, "b"], [3, "c"]])
    assert count == 3
    assert size > 0
    parsed = json.loads(out.read_text("utf-8"))
    assert parsed == [
        {"id": 1, "name": "a"},
        {"id": 2, "name": "b"},
        {"id": 3, "name": "c"},
    ]


def test_json_writer_empty_rows(tmp_path):
    from app.services.sql_export import _write_json
    import json

    out = tmp_path / "empty.json"
    count, _ = _write_json(out, ["id"], [])
    assert count == 0
    parsed = json.loads(out.read_text("utf-8"))
    assert parsed == []


def test_json_writer_single_row(tmp_path):
    from app.services.sql_export import _write_json
    import json

    out = tmp_path / "single.json"
    count, _ = _write_json(out, ["id"], [[42]])
    assert count == 1
    parsed = json.loads(out.read_text("utf-8"))
    assert parsed == [{"id": 42}]


def test_json_writer_with_generator_input(tmp_path):
    """改造后的 writer 必须能接 generator(stream_rows 出来的是 generator)。"""
    from app.services.sql_export import _write_json
    import json

    def _gen():
        yield [1, "a"]
        yield [2, "b"]

    out = tmp_path / "gen.json"
    count, _ = _write_json(out, ["id", "name"], _gen())
    assert count == 2
    parsed = json.loads(out.read_text("utf-8"))
    assert parsed == [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]
