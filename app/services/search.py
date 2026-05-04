"""Phase 10 第 2 项：全局搜索 / 反向索引。

跨 datasources / compare tasks / workflows / history / workflow lineage 节点
做关键词搜索，返回分类命中。当前是基于 JsonStore 的内存搜索 —— 小数据量够
用；等 Phase 10 后续 Repository / SQLite 落地后再换正经索引。

设计要点：
- AND 语义：query 拆 token 后所有 token 都要命中（任一字段全命中即可）
- 字段权重：name > 引用的表名 > body（SQL / description / host）
- 项目空间过滤：传 project_id 时只返回该项目的资产（空 project_id 表示
  全局可见，永远命中）
- 反向索引懒构建 —— JsonStore 已有 mtime 缓存，每次 search 调 .list() 即可
- 跟 DataHub / Atlan 的 platform-level search 思路对齐：用户搜"用户表" →
  所有引用它的 task / workflow / lineage script 一击命中
"""
from __future__ import annotations

import re
from typing import Any

from app.services.history import list_result_history
from app.services.repositories import datasource_store, task_store, workflow_store
from app.services.workflow_history import list_workflow_runs


# ─── 表名抽取（轻量正则，不调 sqlglot） ──────────────────────────────────────
# 搜索路径要快 —— sqlglot 对每条 task 重复解析太重，靠 ETL 关键字 + 表名
# 占位符即可。漏掉一些（subquery / CTE）也能接受 —— 反正 SQL 全文也会被
# fallback 匹配。
_TABLE_REF_RE = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE|MERGE\s+INTO|TRUNCATE\s+TABLE|TRUNCATE)\s+([A-Za-z0-9_$@.]+)",
    re.IGNORECASE,
)


def _extract_tables(sql: str) -> set[str]:
    if not sql:
        return set()
    return {m.group(1).lower() for m in _TABLE_REF_RE.finditer(sql)}


def _all_tokens_in(value: str, tokens: list[str]) -> int:
    """value 里包含所有 token → 返回命中 token 数；否则 0。"""
    if not value or not tokens:
        return 0
    v = value.lower()
    matched = sum(1 for tok in tokens if tok in v)
    return matched if matched == len(tokens) else 0


def _snippet(text: str, tokens: list[str], length: int = 160) -> str:
    """从 text 里取一段以第一个 token 为中心的 snippet（≤ length 字符）。"""
    if not text:
        return ""
    lower = text.lower()
    first_tok = tokens[0] if tokens else ""
    if first_tok and first_tok in lower:
        idx = lower.index(first_tok)
        start = max(0, idx - 40)
        end = min(len(text), idx + length - 40)
        snippet = text[start:end].replace("\n", " ")
        if start > 0:
            snippet = "…" + snippet
        if end < len(text):
            snippet = snippet + "…"
        return snippet
    truncated = text[:length].replace("\n", " ")
    return truncated + ("…" if len(text) > length else "")


def _project_visible(item_project_id: str, query_project_id: str) -> bool:
    """item 是否对当前 query 可见。
    - 空 query_project_id：返回所有（不过滤）
    - 非空 query_project_id：item.project_id 必须为空（全局）或等于 query_project_id
    """
    if not query_project_id:
        return True
    return not item_project_id or item_project_id == query_project_id


def search(
    q: str,
    *,
    kinds: list[str] | None = None,
    limit: int = 50,
    project_id: str = "",
) -> dict[str, Any]:
    """跨 5 类资产搜索。"""
    q = (q or "").strip()
    if not q:
        return {"query": q, "total": 0, "hits": [], "by_kind": {}}
    tokens = [t for t in q.lower().split() if t]
    if not tokens:
        return {"query": q, "total": 0, "hits": [], "by_kind": {}}

    enabled = set(kinds or [])
    all_kinds = not enabled

    hits: list[dict[str, Any]] = []
    if all_kinds or "datasource" in enabled:
        hits.extend(_search_datasources(tokens, project_id))
    if all_kinds or "task" in enabled:
        hits.extend(_search_tasks(tokens, project_id))
    if all_kinds or "workflow" in enabled:
        hits.extend(_search_workflows(tokens, project_id))
    if all_kinds or "history" in enabled:
        hits.extend(_search_history(tokens, project_id))
    if all_kinds or "lineage_script" in enabled:
        hits.extend(_search_lineage_scripts(tokens, project_id))

    hits.sort(key=lambda h: h["score"], reverse=True)
    by_kind: dict[str, int] = {}
    for h in hits:
        by_kind[h["kind"]] = by_kind.get(h["kind"], 0) + 1
    return {
        "query": q,
        "total": len(hits),
        "hits": hits[:limit],
        "by_kind": by_kind,
    }


# ─── per-kind search ─────────────────────────────────────────────────────────


def _search_datasources(tokens: list[str], project_id: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for ds in datasource_store.list():
        if not _project_visible(getattr(ds, "project_id", ""), project_id):
            continue
        name_score = _all_tokens_in(ds.name, tokens) * 100
        host_score = _all_tokens_in(ds.host, tokens) * 30
        db_score = _all_tokens_in(ds.database, tokens) * 30
        score = max(name_score, host_score, db_score)
        if not score:
            continue
        if name_score >= host_score and name_score >= db_score:
            match_path = "name"
            snippet = ds.name
        elif host_score >= db_score:
            match_path = "host"
            snippet = f"{ds.host}:{ds.port}"
        else:
            match_path = "database"
            snippet = ds.database
        db_type_str = ds.db_type.value if hasattr(ds.db_type, "value") else str(ds.db_type)
        out.append({
            "kind": "datasource",
            "id": ds.id,
            "name": ds.name,
            "snippet": snippet,
            "match_path": match_path,
            "score": score,
            "project_id": getattr(ds, "project_id", ""),
            "metadata": {
                "db_type": db_type_str,
                "host": ds.host,
                "database": ds.database,
            },
        })
    return out


def _search_tasks(tokens: list[str], project_id: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in task_store.list():
        if not _project_visible(getattr(t, "project_id", ""), project_id):
            continue
        name_score = _all_tokens_in(t.name, tokens) * 100
        tables = _extract_tables(t.source_sql) | _extract_tables(t.target_sql)
        table_score = max((_all_tokens_in(tab, tokens) * 50 for tab in tables), default=0)
        sql_score = max(
            _all_tokens_in(t.source_sql, tokens),
            _all_tokens_in(t.target_sql, tokens),
        ) * 10
        score = max(name_score, table_score, sql_score)
        if not score:
            continue
        if name_score >= table_score and name_score >= sql_score:
            match_path = "name"
            snippet = t.name
        elif table_score >= sql_score:
            matched_table = next((tab for tab in tables if _all_tokens_in(tab, tokens)), "")
            match_path = "tables"
            snippet = f"涉及表：{matched_table}"
        else:
            match_path = "sql"
            sql_text = t.source_sql or t.target_sql or ""
            snippet = _snippet(sql_text, tokens)
        out.append({
            "kind": "task",
            "id": t.id,
            "name": t.name,
            "snippet": snippet,
            "match_path": match_path,
            "score": score,
            "project_id": getattr(t, "project_id", ""),
            "metadata": {"tables": sorted(tables)[:10]},
        })
    return out


def _search_workflows(tokens: list[str], project_id: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for w in workflow_store.list():
        if not _project_visible(getattr(w, "project_id", ""), project_id):
            continue
        name_score = _all_tokens_in(w.name, tokens) * 100
        desc_score = _all_tokens_in(getattr(w, "description", "") or "", tokens) * 20
        tag_score = max(
            (_all_tokens_in(tag, tokens) * 40 for tag in (getattr(w, "tags", None) or [])),
            default=0,
        )
        node_score = 0
        node_match: tuple[str, str] | None = None
        for node in (w.nodes or []):
            for v in (node.config or {}).values():
                if not isinstance(v, str):
                    continue
                s = _all_tokens_in(v, tokens)
                if not s:
                    continue
                if s * 15 > node_score:
                    node_score = s * 15
                    node_match = (node.id, _snippet(v, tokens))
        score = max(name_score, desc_score, tag_score, node_score)
        if not score:
            continue
        if name_score >= max(desc_score, tag_score, node_score):
            match_path = "name"
            snippet = w.name
        elif tag_score >= max(desc_score, node_score):
            match_path = "tags"
            snippet = ", ".join(getattr(w, "tags", None) or [])
        elif node_score >= desc_score and node_match:
            match_path = f"nodes[id={node_match[0]}].config"
            snippet = node_match[1]
        else:
            match_path = "description"
            snippet = _snippet(getattr(w, "description", "") or "", tokens)
        out.append({
            "kind": "workflow",
            "id": w.id,
            "name": w.name,
            "snippet": snippet,
            "match_path": match_path,
            "score": score,
            "project_id": getattr(w, "project_id", ""),
            "metadata": {"node_count": len(w.nodes or [])},
        })
    return out


def _search_history(tokens: list[str], project_id: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for record in list_result_history(project_id=project_id):
        rid = str(record.get("id") or record.get("run_id") or "")
        if not rid:
            continue
        task_name = str(record.get("task_name") or "")
        name_score = _all_tokens_in(task_name, tokens) * 80
        if not name_score:
            continue
        out.append({
            "kind": "history",
            "id": rid,
            "name": task_name,
            "snippet": f"{record.get('started_at') or ''} · {record.get('status') or ''}",
            "match_path": "task_name",
            "score": name_score,
            "project_id": str(record.get("project_id") or ""),
            "metadata": {
                "started_at": record.get("started_at") or "",
                "status": record.get("status") or "",
            },
        })
    return out


def _search_lineage_scripts(tokens: list[str], project_id: str) -> list[dict[str, Any]]:
    """搜最近 workflow runs 中 lineage 节点产出的 script —— 命中 file_name
    或 read/write tables。每条命中给出 run_id + workflow_id 让前端能跳转。
    """
    out: list[dict[str, Any]] = []
    runs = list_workflow_runs(limit=30)
    for r in runs:
        for node_run in (r.get("nodes") or []):
            output = node_run.get("output") or {}
            files = output.get("files") or []
            for f in files:
                if not isinstance(f, dict):
                    continue
                file_name = str(f.get("file_name") or f.get("name") or "")
                if not file_name:
                    continue
                file_score = _all_tokens_in(file_name, tokens) * 60
                tables = list((f.get("read_tables") or [])) + list((f.get("write_tables") or []))
                table_score = max(
                    (_all_tokens_in(str(tab), tokens) * 50 for tab in tables),
                    default=0,
                )
                final = max(file_score, table_score)
                if not final:
                    continue
                if file_score >= table_score:
                    match_path = "file_name"
                    snippet = file_name
                else:
                    matched_tab = next((str(t) for t in tables if _all_tokens_in(str(t), tokens)), "")
                    match_path = "tables"
                    snippet = f"涉及表：{matched_tab}"
                out.append({
                    "kind": "lineage_script",
                    "id": f"{r.get('id') or ''}::{file_name}",
                    "name": file_name,
                    "snippet": snippet,
                    "match_path": match_path,
                    "score": final,
                    "project_id": "",
                    "metadata": {
                        "run_id": r.get("id"),
                        "workflow_id": r.get("workflow_id"),
                        "node_id": node_run.get("node_id"),
                    },
                })
    return out


__all__ = ["search"]
