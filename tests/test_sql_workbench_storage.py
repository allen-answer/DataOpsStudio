"""SQL Workbench storage 单测 —— 不依赖 FastAPI client,直接打 SqlWorkbenchStore。"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.sqlide.models import ConsoleCreate, ConsoleUpdate, HistoryEntry
from app.sqlide.storage import SqlWorkbenchStore


@pytest.fixture
def store(tmp_path: Path) -> SqlWorkbenchStore:
    return SqlWorkbenchStore(tmp_path / "sql_workbench.json")


def test_list_consoles_empty_returns_empty_list(store: SqlWorkbenchStore) -> None:
    assert store.list_consoles() == []
    assert store.list_consoles(owner_user_id="alice") == []


def test_create_console_assigns_id_and_timestamps(store: SqlWorkbenchStore) -> None:
    c = store.create_console(ConsoleCreate(name="t1", datasource_id="ds-1"), owner_user_id="u-1")
    assert c.id
    assert c.name == "t1"
    assert c.datasource_id == "ds-1"
    assert c.owner_user_id == "u-1"
    assert c.created_at  # iso 8601
    assert c.updated_at


def test_list_consoles_filters_by_owner(store: SqlWorkbenchStore) -> None:
    store.create_console(ConsoleCreate(name="alice-tab"), owner_user_id="u-a")
    store.create_console(ConsoleCreate(name="bob-tab"), owner_user_id="u-b")
    alice = store.list_consoles(owner_user_id="u-a")
    bob = store.list_consoles(owner_user_id="u-b")
    everyone = store.list_consoles()
    assert [c.name for c in alice] == ["alice-tab"]
    assert [c.name for c in bob] == ["bob-tab"]
    assert len(everyone) == 2


def test_update_console_partial(store: SqlWorkbenchStore) -> None:
    c = store.create_console(ConsoleCreate(name="t1", sql="SELECT 1"), owner_user_id="u-1")
    updated = store.update_console(c.id, ConsoleUpdate(sql="SELECT 2"))
    assert updated.sql == "SELECT 2"
    assert updated.name == "t1"  # 没传 name 不应被清空
    assert updated.updated_at >= c.updated_at  # iso 8601 字符序保单调


def test_update_console_missing_raises(store: SqlWorkbenchStore) -> None:
    with pytest.raises(KeyError):
        store.update_console("missing", ConsoleUpdate(name="x"))


def test_delete_console(store: SqlWorkbenchStore) -> None:
    c = store.create_console(ConsoleCreate(name="t1"), owner_user_id="u-1")
    store.delete_console(c.id)
    assert store.get_console(c.id) is None
    with pytest.raises(KeyError):
        store.delete_console(c.id)


def test_history_append_and_list(store: SqlWorkbenchStore) -> None:
    for i in range(3):
        store.append_history(HistoryEntry(
            id=f"h-{i}",
            datasource_id="ds-1",
            sql=f"SELECT {i}",
            executed_by="alice",
            executed_at="2026-05-26T00:00:00+00:00",
            success=True,
            row_count=i,
        ))
    items = store.list_history(owner_user_id="alice", limit=10)
    # 最新优先
    assert [h.sql for h in items] == ["SELECT 2", "SELECT 1", "SELECT 0"]


def test_history_filter_by_datasource(store: SqlWorkbenchStore) -> None:
    store.append_history(HistoryEntry(
        id="h-a", datasource_id="ds-1", sql="SELECT 1",
        executed_by="alice", executed_at="2026-01-01T00:00:00Z", success=True,
    ))
    store.append_history(HistoryEntry(
        id="h-b", datasource_id="ds-2", sql="SELECT 2",
        executed_by="alice", executed_at="2026-01-01T00:00:00Z", success=True,
    ))
    filtered = store.list_history(datasource_id="ds-2", limit=10)
    assert [h.sql for h in filtered] == ["SELECT 2"]


def test_history_ring_buffer_caps_growth(store: SqlWorkbenchStore, monkeypatch) -> None:
    # 把 cap 调小快验证
    import app.sqlide.storage as storage_mod
    monkeypatch.setattr(storage_mod, "_HISTORY_CAP", 3)
    for i in range(7):
        store.append_history(HistoryEntry(
            id=f"h-{i}", datasource_id="ds", sql=f"SELECT {i}",
            executed_by="alice", executed_at="t", success=True,
        ))
    items = store.list_history(limit=100)
    # 保留最新 3 条:SELECT 4 / 5 / 6
    assert [h.sql for h in items] == ["SELECT 6", "SELECT 5", "SELECT 4"]


def test_root_object_format_persisted(store: SqlWorkbenchStore) -> None:
    """文件落盘是 root object 不是 list,跟用户要求的 sql_workbench.json shape 一致。"""
    import json
    store.create_console(ConsoleCreate(name="x"), owner_user_id="u")
    raw = json.loads(store.path.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    assert "consoles" in raw and "history" in raw
