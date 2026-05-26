"""SQL 工作台 metadata cache v0.3 单测 —— 纯 store 层,不打 DB。"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.sqlide import metadata_cache, search


@pytest.fixture(autouse=True)
def cache_tmp(tmp_path: Path):
    """把 cache 目录切到 tmp,避免污染真实 config/metadata_cache/。"""
    metadata_cache.set_cache_dir(tmp_path / "cache")
    yield
    metadata_cache.set_cache_dir(Path("config/metadata_cache"))


def test_load_cache_returns_empty_when_no_file():
    cache = metadata_cache.load_cache("ds-1")
    assert cache["datasource_id"] == "ds-1"
    for scope in metadata_cache.SCOPES:
        assert cache[scope] is None


def test_save_scope_then_load_returns_items_and_fetched_at():
    metadata_cache.save_scope("ds-1", "schemas", [{"name": "public"}])
    cache = metadata_cache.load_cache("ds-1")
    items, fetched_at = metadata_cache.get_scope(cache, "schemas")
    assert items == [{"name": "public"}]
    assert fetched_at is not None
    assert "T" in fetched_at  # ISO 8601


def test_partial_scope_write_preserves_other_scopes():
    metadata_cache.save_scope("ds-1", "schemas", [{"name": "public"}])
    metadata_cache.save_scope("ds-1", "tables", {"public": [{"name": "users"}]})

    cache = metadata_cache.load_cache("ds-1")
    schemas_items, _ = metadata_cache.get_scope(cache, "schemas")
    tables_items, _ = metadata_cache.get_scope(cache, "tables")
    assert schemas_items == [{"name": "public"}]
    assert tables_items == {"public": [{"name": "users"}]}


def test_clear_scope_only_affects_that_scope():
    metadata_cache.save_scope("ds-1", "schemas", [{"name": "public"}])
    metadata_cache.save_scope("ds-1", "tables", {"public": [{"name": "u"}]})
    metadata_cache.clear_cache("ds-1", "tables")

    cache = metadata_cache.load_cache("ds-1")
    schemas_items, _ = metadata_cache.get_scope(cache, "schemas")
    tables_items, _ = metadata_cache.get_scope(cache, "tables")
    assert schemas_items == [{"name": "public"}]
    assert tables_items is None


def test_clear_all_removes_file():
    metadata_cache.save_scope("ds-1", "schemas", [{"name": "public"}])
    assert metadata_cache._cache_path("ds-1").exists()
    metadata_cache.clear_cache("ds-1", None)
    assert not metadata_cache._cache_path("ds-1").exists()


def test_cache_summary_lists_fetched_at_per_scope():
    metadata_cache.save_scope("ds-1", "schemas", [{"name": "public"}])
    metadata_cache.save_scope("ds-1", "tables", {"public": []})
    summary = metadata_cache.cache_summary("ds-1")
    assert summary["schemas"]
    assert summary["tables"]
    assert summary["columns"] is None
    assert summary["indexes"] is None
    assert summary["views"] is None


def test_unknown_scope_raises():
    with pytest.raises(ValueError):
        metadata_cache.save_scope("ds-1", "garbage", [])  # type: ignore[arg-type]


def test_corrupt_cache_file_falls_back_to_empty(tmp_path: Path):
    metadata_cache.set_cache_dir(tmp_path / "cache")
    p = metadata_cache._cache_path("ds-corrupt")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not valid json")
    cache = metadata_cache.load_cache("ds-corrupt")
    # 不抛,降级到空骨架
    assert cache["datasource_id"] == "ds-corrupt"
    for scope in metadata_cache.SCOPES:
        assert cache[scope] is None


def test_safe_filename_handles_special_chars(tmp_path: Path):
    metadata_cache.set_cache_dir(tmp_path / "cache")
    # 路径分隔符 / .. 等都该被白名单过滤,不能写到目录外
    metadata_cache.save_scope("../../etc/passwd", "schemas", [])
    files = list((tmp_path / "cache").iterdir())
    # 只产生一个 cache 文件,且文件名不含路径分隔
    assert len(files) == 1
    assert "/" not in files[0].name
    assert ".." not in files[0].stem


# ─── search ───────────────────────────────────────────────────────────


def _seed_full_cache():
    metadata_cache.save_scope("ds-1", "schemas", [{"name": "public"}, {"name": "ods"}])
    metadata_cache.save_scope("ds-1", "tables", {
        "public": [{"name": "users"}, {"name": "orders"}],
        "ods": [{"name": "users_archive"}],
    })
    metadata_cache.save_scope("ds-1", "columns", {
        "public.users": [{"name": "id"}, {"name": "email"}],
        "public.orders": [{"name": "id"}, {"name": "user_id"}],
    })
    metadata_cache.save_scope("ds-1", "views", {
        "public": [{"name": "v_active_users"}],
    })


def test_search_finds_table_by_name():
    _seed_full_cache()
    results = search.search_metadata("ds-1", "orders")
    kinds = [r["kind"] for r in results]
    assert "table" in kinds
    assert any(r["table"] == "orders" for r in results if r["kind"] == "table")


def test_search_finds_column_across_tables():
    _seed_full_cache()
    results = search.search_metadata("ds-1", "user_id")
    cols = [r for r in results if r["kind"] == "column"]
    assert any(r["table"] == "orders" and r["column"] == "user_id" for r in cols)


def test_search_finds_view():
    _seed_full_cache()
    results = search.search_metadata("ds-1", "v_active")
    assert any(r["kind"] == "view" and r["view"] == "v_active_users" for r in results)


def test_search_and_token_filtering():
    _seed_full_cache()
    # ods + users → 应只命中 ods.users_archive 这个 table,不命中 public.users
    results = search.search_metadata("ds-1", "ods users")
    table_hits = [r for r in results if r["kind"] == "table"]
    assert len(table_hits) == 1
    assert table_hits[0]["schema"] == "ods"
    assert table_hits[0]["table"] == "users_archive"


def test_search_score_orders_table_above_column():
    _seed_full_cache()
    # "users" 同时命中 public.users (table) 和 public.users.email (column,但 email 不含 users)
    # 实际:public.users (table) 和 ods.users_archive (table) + public.users.id (column 通过 table 名)
    results = search.search_metadata("ds-1", "users")
    assert results  # 至少有命中
    # 排序:table 类型(score=50)排在 column 之前
    table_score = next((r["score"] for r in results if r["kind"] == "table"), 0)
    col_score = next((r["score"] for r in results if r["kind"] == "column"), 0)
    assert table_score >= col_score


def test_search_empty_query_returns_empty():
    _seed_full_cache()
    assert search.search_metadata("ds-1", "") == []
    assert search.search_metadata("ds-1", "   ") == []


def test_search_no_cache_returns_empty():
    # ds-noexist 一个 scope 都没拉过
    assert search.search_metadata("ds-noexist", "users") == []


def test_search_kinds_filter():
    _seed_full_cache()
    only_table = search.search_metadata("ds-1", "users", kinds=["table"])
    assert all(r["kind"] == "table" for r in only_table)
    only_col = search.search_metadata("ds-1", "users", kinds=["column"])
    assert all(r["kind"] == "column" for r in only_col)


def test_search_limit_caps_results():
    _seed_full_cache()
    # 加大量同名命中
    metadata_cache.save_scope("ds-1", "columns", {
        f"public.t{i}": [{"name": "user_name"}] for i in range(100)
    })
    results = search.search_metadata("ds-1", "user_name", limit=10)
    assert len(results) == 10
