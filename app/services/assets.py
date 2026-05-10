"""Phase 10 第 4 项：资产详情服务 MVP。

把表当一等资产 —— `/api/assets/table/{name}` 返回这张表的：
- 基本信息（schema / basename / primary_role / refresh_mode）
- 反向引用（哪些 task / workflow / lineage 脚本引用了它）
- 最近一次出现的 history / workflow run

复用 `app.services.search` 的反向索引（task SQL 抽表名 / workflow node config
扫字符串 / workflow_run lineage 节点 output.files）。

MVP 范围：仅 table kind；owner / SLA / PII 等 classification 字段放下个 sprint。
"""
from __future__ import annotations

import threading
import time
from typing import Any

from app.services.history import list_result_history
from app.services.repositories import datasource_store, task_store, workflow_store
from app.services.search import _all_tokens_in, _extract_tables
from app.services.workflow_history import (
    get_cached_run_payloads as _get_cached_run_payloads,
    get_workflow_run,
    invalidate_run_payloads_cache as _invalidate_run_payloads_cache,
    list_workflow_runs,
)


def _split_schema(name: str) -> tuple[str, str]:
    """`ods.t_users` → (`ods`, `t_users`)；`t_users` → (`(默认)`, `t_users`)。
    Oracle DB Link `tab@dblink` 先剥 dblink。
    """
    bare = name.split("@")[0]
    if "." in bare:
        schema, basename = bare.rsplit(".", 1)
    else:
        schema, basename = "(默认)", bare
    return schema, basename


def _scan_tasks_referencing(table_name: str, project_id: str) -> list[dict[str, Any]]:
    """找 source_sql / target_sql 里写了 table_name 的 task。"""
    target = table_name.lower()
    out: list[dict[str, Any]] = []
    for t in task_store.list():
        if project_id and getattr(t, "project_id", "") and t.project_id != project_id:
            continue
        tables = _extract_tables(t.source_sql) | _extract_tables(t.target_sql)
        if target not in tables:
            continue
        # 区分源 / 目标
        in_source = target in _extract_tables(t.source_sql)
        in_target = target in _extract_tables(t.target_sql)
        match_role = (
            "source/target" if in_source and in_target
            else ("source" if in_source else "target")
        )
        out.append({
            "id": t.id,
            "name": t.name,
            "match_role": match_role,
            "project_id": getattr(t, "project_id", ""),
        })
    return out


def _scan_workflows_referencing(table_name: str, project_id: str) -> list[dict[str, Any]]:
    """找 node config 字符串 / description / tags 里出现 table_name 的 workflow。"""
    tokens = [table_name.lower()]
    out: list[dict[str, Any]] = []
    for w in workflow_store.list():
        if project_id and getattr(w, "project_id", "") and w.project_id != project_id:
            continue
        # 拼起来扫一遍：description + tags + node configs
        haystacks: list[str] = [getattr(w, "description", "") or ""]
        haystacks.extend(getattr(w, "tags", None) or [])
        for node in (w.nodes or []):
            for v in (node.config or {}).values():
                if isinstance(v, str):
                    haystacks.append(v)
        if not any(_all_tokens_in(h, tokens) for h in haystacks):
            continue
        out.append({
            "id": w.id,
            "name": w.name,
            "node_count": len(w.nodes or []),
            "project_id": getattr(w, "project_id", ""),
        })
    return out


def _scan_lineage_scripts_referencing(table_name: str) -> list[dict[str, Any]]:
    """从最近 30 个 workflow_run 找 lineage 节点 output.files 里 read/write
    tables 含 table_name 的脚本。

    用共享 payload 缓存（`_get_cached_run_payloads`）拿全 run，summary 不含 `nodes`
    —— 旧版直接走 `list_workflow_runs` 拿到的是 summary，`r.get("nodes")` 永远空，
    导致 references.lineage_scripts 一直返回空 list（bug）。
    """
    target = table_name.lower()
    out: list[dict[str, Any]] = []
    for r in _get_cached_run_payloads(30):
        for node_run in (r.get("nodes") or []):
            output = node_run.get("output") or {}
            for f in output.get("files") or []:
                if not isinstance(f, dict):
                    continue
                read_tabs = [str(x).lower() for x in (f.get("read_tables") or [])]
                write_tabs = [str(x).lower() for x in (f.get("write_tables") or [])]
                if target in read_tabs or target in write_tabs:
                    role = (
                        "source/target" if target in read_tabs and target in write_tabs
                        else ("source" if target in read_tabs else "target")
                    )
                    out.append({
                        "run_id": r.get("run_id"),
                        "workflow_id": r.get("workflow_id"),
                        "node_id": node_run.get("node_id"),
                        "file_name": f.get("file_name") or f.get("name") or "",
                        "match_role": role,
                    })
    return out


def _scan_history_referencing(table_name: str, project_id: str) -> list[dict[str, Any]]:
    """history record 是 task 的运行实例 —— task_name 命中或关联 task 引用过 table_name。
    MVP 简化：只看 task_name 完全 / 部分匹配。

    只看最新 200 条历史 —— 旧 task 命中对资产详情的价值有限，跟 search 一致界限。
    """
    target = table_name.lower()
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in list_result_history(project_id=project_id, limit=200):
        rid = str(record.get("id") or record.get("run_id") or "")
        if not rid or rid in seen:
            continue
        seen.add(rid)
        task_name = str(record.get("task_name") or "")
        if target not in task_name.lower():
            continue
        out.append({
            "id": rid,
            "task_name": task_name,
            "started_at": record.get("started_at") or "",
            "status": record.get("status") or "",
        })
    return out


def get_table_asset(name: str, *, project_id: str = "") -> dict[str, Any]:
    """组装表资产详情。返回 dict（不含 None 字段时仍保留 key 以保 contract 稳定）。

    Phase 10 #3 v1：role / refresh_mode / upstream_count / downstream_count
    从全局 lineage 索引（aggregated from 最近 50 workflow_run）填上 ——
    索引里没这张表（即没在最近的 workflow lineage 里出现过）则保持 null。
    """
    if not name or not name.strip():
        raise ValueError("name is required")
    name = name.strip()
    schema, basename = _split_schema(name)

    tasks = _scan_tasks_referencing(name, project_id)
    workflows = _scan_workflows_referencing(name, project_id)
    lineage_scripts = _scan_lineage_scripts_referencing(name)
    history = _scan_history_referencing(name, project_id)

    refs_total = len(tasks) + len(workflows) + len(lineage_scripts) + len(history)

    # 从全局 lineage 索引拉元数据 —— 失败兜底（索引未启用 / rebuild 异常）
    primary_role = None
    refresh_mode = None
    refresh_modes: list[str] = []
    roles: list[str] = []
    upstream_count = 0
    downstream_count = 0
    last_seen_run_id = ""
    last_seen_at = ""
    try:
        from app.services.lineage_index import get_lineage_index
        meta = get_lineage_index().get_table_metadata(name)
        if meta:
            primary_role = meta.get("primary_role") or None
            refresh_modes = meta.get("refresh_modes") or []
            # 多种 refresh_mode 共存时 refresh_mode 字段取第一个；refresh_modes
            # 列表保留全部
            refresh_mode = refresh_modes[0] if refresh_modes else None
            roles = meta.get("roles") or []
            upstream_count = int(meta.get("upstream_count") or 0)
            downstream_count = int(meta.get("downstream_count") or 0)
            last_seen_run_id = str(meta.get("last_seen_run_id") or "")
            last_seen_at = str(meta.get("last_seen_at") or "")
    except Exception:  # pragma: no cover —— 索引不可用时兜底
        pass

    # Custom aspects（owner / pii / sla / sensitive / tag / business_term）——
    # 失败兜底（asset_aspects 表未建 / SQLite 不可用），不拖崩资产详情页
    aspects: list[dict[str, Any]] = []
    try:
        from app.services.asset_aspects import list_aspects_for_asset
        aspects = list_aspects_for_asset("table", name, project_id=project_id)
    except Exception:  # pragma: no cover
        pass

    return {
        "kind": "table",
        "name": name,
        "schema": schema,
        "basename": basename,
        "primary_role": primary_role,
        "refresh_mode": refresh_mode,
        "refresh_modes": refresh_modes,
        "roles": roles,
        "upstream_count": upstream_count,
        "downstream_count": downstream_count,
        "last_seen_run_id": last_seen_run_id,
        "last_seen_at": last_seen_at,
        "aspects": aspects,
        "references": {
            "tasks": tasks,
            "workflows": workflows,
            "lineage_scripts": lineage_scripts,
            "history": history,
        },
        "stats": {
            "total_references": refs_total,
            "task_count": len(tasks),
            "workflow_count": len(workflows),
            "lineage_script_count": len(lineage_scripts),
            "history_count": len(history),
        },
    }


def get_table_columns(name: str, *, project_id: str = "", run_limit: int = 50) -> list[dict[str, Any]]:
    """从最近 run_limit 个 workflow_run 的 lineage 输出 insert_mappings 反查
    table_name 的字段。返回每列的 read_count / write_count / 最近出现 run_id。

    数据语义：
    - **write_count**：mapping.target_table == name 时 +1（insert/update/merge 写入此列）
    - **read_count**：name ∈ mapping.source_tables 时按 source_columns 出现次数 +1。
      多源 mapping（join 多张表）只有当 source_columns 显式带 `name.` 前缀才算
    - **transforms**：每列见过的 transform 字符串集合（聚合 / cast / 窗口 等）
    - **last_seen_run_id**：列最近出现的 run_id

    没在最近 lineage 里出现的表 → 空列表。这是 *workflow_run-based* 视图，不是
    datasource introspection（不需要活的 DB 连接）。
    """
    if not name or not name.strip():
        raise ValueError("name is required")
    target = name.strip()
    target_lower = target.lower()
    target_basename = target_lower.split(".")[-1]  # ods.t_users → t_users 的 alias 形式

    cols: dict[str, dict[str, Any]] = {}

    def _bump(col_key: str, kind: str, run_id: str, run_started_at: str, transform: str = "") -> None:
        col_key = col_key.strip()
        if not col_key:
            return
        # 提取最后一段当显示名（"db.t.col" → "col"；"col" → "col"）
        display = col_key.split(".")[-1]
        if not display:
            return
        entry = cols.setdefault(display, {
            "name": display,
            "read_count": 0,
            "write_count": 0,
            "transforms": set(),
            "last_seen_run_id": "",
            "last_seen_at": "",
        })
        entry[f"{kind}_count"] += 1
        if transform:
            entry["transforms"].add(transform)
        if run_started_at >= entry["last_seen_at"]:
            entry["last_seen_at"] = run_started_at
            entry["last_seen_run_id"] = run_id

    try:
        # 走共享 payload 缓存 —— 跟 _build_column_edge_index 同一份解析后的 runs
        for full in _get_cached_run_payloads(run_limit):
            run_id = str(full.get("run_id") or "")
            if not run_id:
                continue
            run_started_at = str(full.get("started_at") or full.get("created_at") or "")
            for node_run in (full.get("nodes") or []):
                if str(node_run.get("type") or "").lower() != "lineage":
                    continue
                output = node_run.get("output") or {}
                # files 列表（batch lineage）或 result（单脚本 lineage）
                packets = output.get("files") or [output]
                for packet in packets:
                    if not isinstance(packet, dict):
                        continue
                    for mapping in (packet.get("insert_mappings") or []):
                        if not isinstance(mapping, dict):
                            continue
                        target_table = str(mapping.get("target_table") or "").strip().lower()
                        # write
                        if target_table == target_lower or target_table == target_basename:
                            tcol = str(mapping.get("target_column") or "").strip()
                            transform = str(mapping.get("transform") or "")
                            if tcol:
                                _bump(tcol, "write", run_id, run_started_at, transform)
                        # read
                        source_tables = [str(t).lower() for t in (mapping.get("source_tables") or [])]
                        if target_lower in source_tables or target_basename in source_tables:
                            for sc in (mapping.get("source_columns") or []):
                                sc = str(sc).strip()
                                if not sc:
                                    continue
                                # 多源 mapping：仅显式带 `name.` 前缀的算（避免误把
                                # 兄弟表的列归到此表）；单源 mapping 全算
                                if len(source_tables) > 1:
                                    sc_lower = sc.lower()
                                    if not (sc_lower.startswith(target_lower + ".")
                                            or sc_lower.startswith(target_basename + ".")):
                                        continue
                                _bump(sc, "read", run_id, run_started_at)
    except Exception:  # pragma: no cover —— 兜底；workflow_history 不可用 / payload 异常
        return []

    out: list[dict[str, Any]] = []
    for entry in cols.values():
        out.append({
            **entry,
            "transforms": sorted(entry["transforms"]),
        })
    # 按 (write+read 总数) 倒序，热度高的排前
    out.sort(key=lambda c: (c["write_count"] + c["read_count"]), reverse=True)
    return out


# ─── column edge index 缓存（上层） ─────────────────────────────────────────
# 跟 lineage_index.py 一个套路：列出 (src_t, src_c) → (tgt_t, tgt_c) 边的
# 反向 / 正向索引。复用 _get_cached_run_payloads 不重复扫文件。
_COLUMN_EDGE_TTL = 300.0
# 同 workflow_history._run_payloads_cache：keyed by run_limit (1..200 from API +
# 50 from internal callers)；用 8 个槽 FIFO 防止用户连续打不同 run_limit 撑爆缓存。
_COLUMN_EDGE_CACHE_MAX_SLOTS = 8
_column_edge_cache: dict[int, dict[str, Any]] = {}
_column_edge_lock = threading.RLock()


def _get_cached_column_edges(run_limit: int) -> tuple[
    dict[tuple[str, str], dict[tuple[str, str], int]],
    dict[tuple[str, str], dict[tuple[str, str], int]],
]:
    """带缓存的 edge index 拿取。失效条件：
    - TTL 过期（300s）
    - workflow_run 数量变化（粗粒度，新增 / 删除都触发）
    - 显式 invalidate_column_edge_index_cache()
    """
    now = time.time()
    with _column_edge_lock:
        entry = _column_edge_cache.get(run_limit)
        current_run_count = _count_runs_for_invalidation(run_limit)
        if entry is not None and (
            now - entry["built_at"] < _COLUMN_EDGE_TTL
            and entry["source_run_count"] == current_run_count
        ):
            return entry["up_edges"], entry["down_edges"]
        up, down = _build_column_edge_index(run_limit)
        _column_edge_cache.pop(run_limit, None)
        _column_edge_cache[run_limit] = {
            "built_at": now,
            "source_run_count": current_run_count,
            "up_edges": up,
            "down_edges": down,
        }
        if len(_column_edge_cache) > _COLUMN_EDGE_CACHE_MAX_SLOTS:
            for stale_key in list(_column_edge_cache.keys())[
                : len(_column_edge_cache) - _COLUMN_EDGE_CACHE_MAX_SLOTS
            ]:
                _column_edge_cache.pop(stale_key, None)
        return up, down


def _count_runs_for_invalidation(run_limit: int) -> int:
    """边界粗算：取 list_workflow_runs(limit=run_limit) 的长度。新建或删 run 都会变。
    比对索引/写入时间更稳（避免依赖文件系统 mtime 精度）。"""
    try:
        return len(list_workflow_runs(limit=run_limit))
    except Exception:  # pragma: no cover
        return 0


def invalidate_column_edge_index_cache() -> None:
    """admin / 测试用：清空所有 run_limit 的 cache 槽（含底层 payload 缓存）。"""
    with _column_edge_lock:
        _column_edge_cache.clear()
    # 共享 payload 缓存住在 workflow_history（其它服务也用），统一从那里清
    _invalidate_run_payloads_cache()


def _build_column_edge_index(run_limit: int) -> tuple[
    dict[tuple[str, str], dict[tuple[str, str], int]],
    dict[tuple[str, str], dict[tuple[str, str], int]],
]:
    """扫最近 run_limit 个 workflow_run 的 lineage insert_mappings，建：
    - up_edges[(target_t, target_c)] = {(src_t, src_c): count}  —— 反向（upstream）
    - down_edges[(src_t, src_c)] = {(target_t, target_c): count}  —— 正向（downstream）

    一次扫描两边都建，多跳 BFS 直接走索引。
    `(t, c)` 都 lower 化保证大小写不敏感的对比。
    完全限定形式 `t.c`、单源 mapping 的 unqualified `c`（归到 source_tables[0]）
    都进索引；多源 unqualified 拒绝，避免归属不确定。
    """
    up: dict[tuple[str, str], dict[tuple[str, str], int]] = {}
    down: dict[tuple[str, str], dict[tuple[str, str], int]] = {}

    try:
        for full in _get_cached_run_payloads(run_limit):
            for node_run in (full.get("nodes") or []):
                if str(node_run.get("type") or "").lower() != "lineage":
                    continue
                output = node_run.get("output") or {}
                packets = output.get("files") or [output]
                for packet in packets:
                    if not isinstance(packet, dict):
                        continue
                    for mapping in (packet.get("insert_mappings") or []):
                        if not isinstance(mapping, dict):
                            continue
                        tgt_t = str(mapping.get("target_table") or "").strip().lower()
                        tgt_c = str(mapping.get("target_column") or "").strip().lower()
                        if not tgt_t or not tgt_c:
                            continue
                        source_tables = [str(t) for t in (mapping.get("source_tables") or [])]
                        source_cols = [str(s) for s in (mapping.get("source_columns") or [])]

                        for sc in source_cols:
                            sc_lower = sc.strip().lower()
                            if "." in sc_lower:
                                parts = sc_lower.rsplit(".", 1)
                                src_t, src_c = parts[0].strip(), parts[1].strip()
                            else:
                                # 多源 unqualified 拒绝（归属不定）
                                if len(source_tables) != 1:
                                    continue
                                src_t, src_c = source_tables[0].strip().lower(), sc_lower
                            if not src_t or not src_c:
                                continue
                            # 排除自指（target == source）—— 多跳 BFS 时会进死循环
                            if (src_t, src_c) == (tgt_t, tgt_c):
                                continue
                            up.setdefault((tgt_t, tgt_c), {})
                            up[(tgt_t, tgt_c)][(src_t, src_c)] = (
                                up[(tgt_t, tgt_c)].get((src_t, src_c), 0) + 1
                            )
                            down.setdefault((src_t, src_c), {})
                            down[(src_t, src_c)][(tgt_t, tgt_c)] = (
                                down[(src_t, src_c)].get((tgt_t, tgt_c), 0) + 1
                            )
    except Exception:  # pragma: no cover
        pass
    return up, down


def _bfs_column_chain(
    edges: dict[tuple[str, str], dict[tuple[str, str], int]],
    focal: tuple[str, str],
    depth: int,
    max_nodes: int,
    *,
    annotate_hop: bool,
) -> tuple[list[dict[str, Any]], bool]:
    """从 focal 出发按 BFS 走 depth 跳，每个边 (parent → child) 产一个 item。

    `annotate_hop=False` 时只返回 `{table, column, count}` —— 旧 depth=1 caller 用，
    保持 API 向后兼容。`annotate_hop=True` 时多带 `hop` 和 `from="<parent_t>.<parent_c>"`，
    让前端按路径渲染嵌套（hop=1 的 from=None）。

    cycle 通过 visited set 切断。同一节点经多条路径到达只取首次（按 count desc 排序后
    的最频繁路径优先），避免树爆炸。

    返回 `(items, truncated)`：truncated=True 表示 BFS 在 max_nodes 上限处停了，
    实际链路可能更深 —— 让 caller 提示用户「未尽展示」。
    """
    if depth <= 0:
        return [], False
    visited: set[tuple[str, str]] = {focal}
    result: list[dict[str, Any]] = []
    truncated = False
    from collections import deque
    queue: deque[tuple[tuple[str, str], int]] = deque([(focal, 0)])
    while queue:
        if len(result) >= max_nodes:
            # 队列还有未处理的节点 → 视为截断（即便恰好等于 max_nodes 也算，因为
            # 我们没法保证再多走一步不会发现新节点）。
            truncated = True
            break
        node, hop = queue.popleft()
        if hop >= depth:
            continue
        neighbors = edges.get(node, {})
        for (n_t, n_c), count in sorted(neighbors.items(), key=lambda kv: kv[1], reverse=True):
            if (n_t, n_c) in visited:
                continue
            visited.add((n_t, n_c))
            item: dict[str, Any] = {"table": n_t, "column": n_c, "count": count}
            if annotate_hop:
                item["hop"] = hop + 1
                item["from"] = f"{node[0]}.{node[1]}" if hop >= 1 else None
            result.append(item)
            queue.append(((n_t, n_c), hop + 1))
            if len(result) >= max_nodes:
                truncated = True
                break
    return result, truncated


def get_column_lineage(
    table_name: str,
    column_name: str,
    *,
    project_id: str = "",
    run_limit: int = 50,
    depth: int = 1,
    max_nodes: int = 200,
) -> dict[str, list[dict[str, Any]]]:
    """S1.B：字段血缘热点 —— 给定 (table, column)，返回上下游字段链。

    `depth=1`（默认）保留旧行为：直接邻居 chip。返回 item 形如
    `{table, column, count, hop=1, from=null}`。

    `depth>=2` 触发 BFS 多跳追溯。每个 hop>=2 的 item 多带 `from="<parent_t>.<parent_c>"`
    指明上游来自哪条 chip，让前端能按路径渲染嵌套。BFS 切断 cycle、按 max_nodes 截断
    避免深度爆炸。同一节点经多路径到达只首次出现（沿 hop=1 的最频繁边优先）。

    数据源跟 get_table_columns 一样：最近 run_limit 个 workflow_run 的 lineage
    insert_mappings。完全限定 / 单源 unqualified 归属规则与 get_table_columns 一致；
    多源 unqualified 拒绝。
    """
    if not table_name or not table_name.strip():
        raise ValueError("table_name is required")
    if not column_name or not column_name.strip():
        raise ValueError("column_name is required")
    if depth < 1:
        depth = 1
    if max_nodes < 1:
        max_nodes = 1

    target_t = table_name.strip().lower()
    target_t_base = target_t.split(".")[-1]
    target_c = column_name.strip().lower()

    up_edges, down_edges = _get_cached_column_edges(run_limit)

    # 用户传的 table_name 可能是 schema.t 也可能裸 t —— 选实际命中索引的形式当 focal。
    # 优先全限定（避免裸名误匹配多张同名表），命中不到再用 base。
    focal: tuple[str, str]
    if (target_t, target_c) in up_edges or (target_t, target_c) in down_edges:
        focal = (target_t, target_c)
    elif (target_t_base, target_c) in up_edges or (target_t_base, target_c) in down_edges:
        focal = (target_t_base, target_c)
    else:
        focal = (target_t, target_c)

    annotate = depth > 1
    upstream, up_trunc = _bfs_column_chain(up_edges, focal, depth, max_nodes, annotate_hop=annotate)
    downstream, down_trunc = _bfs_column_chain(down_edges, focal, depth, max_nodes, annotate_hop=annotate)
    return {
        "upstream": upstream,
        "downstream": downstream,
        # truncated 在 depth>=2 时才有意义（depth=1 不会因 max_nodes 触顶 —— 单个节点
        # 的直接邻居数通常远少于 max_nodes 200）。但仍透传以让 caller 自己判定。
        "upstream_truncated": up_trunc,
        "downstream_truncated": down_trunc,
        "max_nodes": max_nodes,
    }


def list_datasource_assets(project_id: str = "") -> list[dict[str, Any]]:
    """列举所有 datasource —— 一类辅助资产，让前端可以"按 datasource 看哪些 task 在用"。"""
    out: list[dict[str, Any]] = []
    for ds in datasource_store.list():
        if project_id and getattr(ds, "project_id", "") and ds.project_id != project_id:
            continue
        out.append({
            "id": ds.id,
            "name": ds.name,
            "db_type": ds.db_type.value if hasattr(ds.db_type, "value") else str(ds.db_type),
            "host": ds.host,
            "database": ds.database,
            "project_id": getattr(ds, "project_id", ""),
        })
    return out


__all__ = [
    "get_table_asset",
    "get_table_columns",
    "get_column_lineage",
    "list_datasource_assets",
]
