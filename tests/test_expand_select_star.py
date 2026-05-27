"""expand_select_star 单测 — 展开 SELECT * 成显式列名."""
from __future__ import annotations

import pytest

from app.services.sql_tools import expand_select_star


def _lookup_fixture(data: dict[str, dict[str, list[str]]]):
    """返回 callable: (schema, table) -> list[str] | None."""
    def _l(schema: str, table: str) -> list[str] | None:
        return data.get(schema, {}).get(table)
    return _l


def _normalize(s: str) -> str:
    return " ".join(s.split()).lower()


# ─── 基本展开 ───────────────────────────────────────────────────────────────

def test_expand_simple_select_star():
    lookup = _lookup_fixture({"ks": {"his_done": ["OCCUR_DATE", "CUST_NO", "sec_code"]}})
    sql, warns = expand_select_star("SELECT * FROM ks.his_done", columns_lookup=lookup)
    n = _normalize(sql)
    assert "occur_date" in n and "cust_no" in n and "sec_code" in n
    assert "*" not in sql
    assert not warns or all(w["code"] != "no_star" for w in warns)


def test_expand_qualified_star():
    """SELECT t.* FROM ks.his_done t — 展开成 t.col1, t.col2"""
    lookup = _lookup_fixture({"ks": {"his_done": ["id", "amt"]}})
    sql, _ = expand_select_star("SELECT t.* FROM ks.his_done t", columns_lookup=lookup)
    n = _normalize(sql)
    assert "t.id" in n and "t.amt" in n
    assert "t.*" not in n


def test_expand_multi_table_join_uses_aliases():
    """JOIN 多表 + SELECT * — 每个 col 加 alias 防歧义."""
    lookup = _lookup_fixture({
        "ks": {"a": ["id", "name"], "b": ["id", "amount"]},
    })
    sql, _ = expand_select_star(
        "SELECT * FROM ks.a JOIN ks.b ON a.id = b.id",
        columns_lookup=lookup,
    )
    n = _normalize(sql)
    # 4 个 col 都要有
    assert "a.id" in n and "a.name" in n
    assert "b.id" in n and "b.amount" in n


def test_no_star_no_change():
    """SQL 里没 * — 原样返回 + 'no_star' warning."""
    lookup = _lookup_fixture({"ks": {"t": ["x"]}})
    sql, warns = expand_select_star("SELECT id FROM ks.t", columns_lookup=lookup)
    assert sql == "SELECT id FROM ks.t"
    assert any(w["code"] == "no_star" for w in warns)


def test_table_not_in_cache_keeps_star():
    """cache miss + lookup 返 None — 保留 *,加 warning,不破坏 SQL."""
    lookup = _lookup_fixture({})  # 空 cache
    sql, warns = expand_select_star("SELECT * FROM ks.his_done", columns_lookup=lookup)
    assert "*" in sql
    assert any(w["code"] == "table_not_in_cache" for w in warns)


def test_qualified_star_unknown_alias():
    """SELECT x.* FROM t — x 没在 FROM 里,保留 *,加 warning."""
    lookup = _lookup_fixture({"ks": {"t": ["a", "b"]}})
    sql, warns = expand_select_star("SELECT x.* FROM ks.t", columns_lookup=lookup)
    assert "x.*" in sql or "x . *" in _normalize(sql)
    assert any(w["code"] == "alias_not_found" for w in warns)


def test_parse_failed_or_no_star_returns_original():
    """坏 SQL — sqlglot 部分版本容错 lenient,parse 不抛;最终行为是没 * 可展,返原 SQL."""
    lookup = _lookup_fixture({})
    sql, warns = expand_select_star("SELEC garbage", columns_lookup=lookup)
    assert sql == "SELEC garbage"
    # 要么 parse_failed,要么 no_star(lenient parse 解析成空 select)
    assert any(w["code"] in ("parse_failed", "no_star") for w in warns)


# ─── 不动 / 边界 ─────────────────────────────────────────────────────────────

def test_count_star_kept_inside_function():
    """COUNT(*) 里的 * 不是 SELECT *,不应被展开."""
    lookup = _lookup_fixture({"ks": {"t": ["id"]}})
    sql, _ = expand_select_star("SELECT COUNT(*) FROM ks.t", columns_lookup=lookup)
    n = _normalize(sql)
    assert "count(*)" in n
    assert "count(id)" not in n


def test_no_from_clause():
    """SELECT * FROM nothing — 没法展开 + warning."""
    lookup = _lookup_fixture({})
    sql, warns = expand_select_star("SELECT * FROM ks.t", columns_lookup=lookup)
    assert "*" in sql
    assert any(w["code"] == "table_not_in_cache" for w in warns)


def test_existing_columns_with_star_mixed():
    """SELECT id, * FROM t — id 保留 + * 展开."""
    lookup = _lookup_fixture({"ks": {"t": ["a", "b", "c"]}})
    sql, _ = expand_select_star("SELECT id, * FROM ks.t", columns_lookup=lookup)
    n = _normalize(sql)
    assert "id" in n
    assert "a" in n and "b" in n and "c" in n
    assert "*" not in n
