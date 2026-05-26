"""SQL 工作台对象搜索 —— 跨 table / column / view 三类 metadata 的索引搜索。

设计:
- 数据源完全走 metadata_cache(per-ds JSON)—— 不打实库,**搜索必须快**
- AND 多 token 命中(空格分隔):每个 token 必须出现在 (schema | table | column | view) 任一字段
- 字段权重:table.name 50 > column.name 40 > view.name 30 > schema 名前缀 10
- 单一返回结构 {kind, schema, table, column?, view?, score, snippet}
- 不强制 cache 完全填充:caller 可先确保 schemas/tables/columns/views 都拉过

跟仓库现有 services/search.py(资产级搜索)的区别:
- 那个搜 task / workflow / lineage_script 等业务对象(仓库 JsonStore)
- 这里搜 metadata(per-ds cache),粒度细到字段级,只在 SQL 工作台用
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

from app.sqlide import metadata_cache as _meta_cache

logger = logging.getLogger(__name__)

# 字段权重 —— 命中权重越高的字段,结果排越前
_W_TABLE = 50
_W_COLUMN = 40
_W_VIEW = 30
_W_SCHEMA = 10

# kind 闭集,前端用来给 chip 上色
KIND_TABLE = "table"
KIND_COLUMN = "column"
KIND_VIEW = "view"
_DEFAULT_KINDS = (KIND_TABLE, KIND_COLUMN, KIND_VIEW)


def search_metadata(
    datasource_id: str,
    q: str,
    kinds: Iterable[str] | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """对 datasource 的 cache 做反向索引搜索。

    q 拆 token 后做 AND 命中:每个 token(小写)必须 substring-match 至少一个字段。
    返回按 score 倒序,带 snippet 让前端高亮关键字。

    完全 cache-only —— 如果 cache 是空的就返 []。caller(API 层)负责在搜之前
    确保 cache 已经填充(常见路径:用户先在树里展开过 schema,tables/columns 自然
    落到 cache;实在没缓存就先调 list_schemas + list_tables 触发)。
    """
    q = (q or "").strip()
    if not q:
        return []
    tokens = [t.lower() for t in q.split() if t.strip()]
    if not tokens:
        return []

    kind_set = set(kinds) if kinds else set(_DEFAULT_KINDS)
    cache = _meta_cache.load_cache(datasource_id)

    results: list[dict[str, Any]] = []
    if KIND_TABLE in kind_set:
        results.extend(_search_tables(cache, tokens))
    if KIND_COLUMN in kind_set:
        results.extend(_search_columns(cache, tokens))
    if KIND_VIEW in kind_set:
        results.extend(_search_views(cache, tokens))

    results.sort(key=lambda r: (-r["score"], r.get("schema", ""), r.get("table", "")))
    return results[:limit]


def _search_tables(cache: dict, tokens: list[str]) -> list[dict[str, Any]]:
    tables_items, _ = _meta_cache.get_scope(cache, "tables")
    if not isinstance(tables_items, dict):
        return []
    hits: list[dict[str, Any]] = []
    for schema_key, tables in tables_items.items():
        if not isinstance(tables, list):
            continue
        schema_name = "" if schema_key == "__default__" else schema_key
        for t in tables:
            name = str(t.get("name") or "").strip()
            if not name:
                continue
            haystack = f"{schema_name} {name}".lower()
            if not _all_tokens_match(haystack, tokens):
                continue
            score = _W_TABLE + _schema_bonus(schema_name, tokens)
            hits.append({
                "kind": KIND_TABLE,
                "schema": schema_name,
                "table": name,
                "score": score,
                "snippet": f"{schema_name}.{name}" if schema_name else name,
            })
    return hits


def _search_columns(cache: dict, tokens: list[str]) -> list[dict[str, Any]]:
    columns_items, _ = _meta_cache.get_scope(cache, "columns")
    if not isinstance(columns_items, dict):
        return []
    hits: list[dict[str, Any]] = []
    for key, columns in columns_items.items():
        if not isinstance(columns, list):
            continue
        # key 形如 "schema.table" 或 "table"
        schema_name, table_name = ("", key) if "." not in key else key.split(".", 1)
        for col in columns:
            col_name = str(col.get("name") or "").strip()
            if not col_name:
                continue
            haystack = f"{schema_name} {table_name} {col_name}".lower()
            if not _all_tokens_match(haystack, tokens):
                continue
            score = _W_COLUMN + _schema_bonus(schema_name, tokens)
            hits.append({
                "kind": KIND_COLUMN,
                "schema": schema_name,
                "table": table_name,
                "column": col_name,
                "data_type": col.get("data_type"),
                "score": score,
                "snippet": (
                    f"{schema_name}.{table_name}.{col_name}"
                    if schema_name else f"{table_name}.{col_name}"
                ),
            })
    return hits


def _search_views(cache: dict, tokens: list[str]) -> list[dict[str, Any]]:
    views_items, _ = _meta_cache.get_scope(cache, "views")
    if not isinstance(views_items, dict):
        return []
    hits: list[dict[str, Any]] = []
    for schema_key, views in views_items.items():
        if not isinstance(views, list):
            continue
        schema_name = "" if schema_key == "__default__" else schema_key
        for v in views:
            name = str(v.get("name") or "").strip()
            if not name:
                continue
            haystack = f"{schema_name} {name}".lower()
            if not _all_tokens_match(haystack, tokens):
                continue
            score = _W_VIEW + _schema_bonus(schema_name, tokens)
            hits.append({
                "kind": KIND_VIEW,
                "schema": schema_name,
                "view": name,
                "table": name,  # 给前端通用跳转方便,view 也填 table 槽位
                "score": score,
                "snippet": f"{schema_name}.{name}" if schema_name else name,
            })
    return hits


def _all_tokens_match(haystack: str, tokens: list[str]) -> bool:
    return all(tok in haystack for tok in tokens)


def _schema_bonus(schema_name: str, tokens: list[str]) -> int:
    # token 命中 schema 名字时给小加分,让"trade_users"类有 schema 前缀的命中
    # 排在前面,避免完全同名表淹没
    if not schema_name:
        return 0
    s = schema_name.lower()
    if any(tok in s for tok in tokens):
        return _W_SCHEMA
    return 0
