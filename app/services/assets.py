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

from typing import Any

from app.services.history import list_result_history
from app.services.repositories import datasource_store, task_store, workflow_store
from app.services.search import _all_tokens_in, _extract_tables
from app.services.workflow_history import get_workflow_run, list_workflow_runs


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
    tables 含 table_name 的脚本。"""
    target = table_name.lower()
    out: list[dict[str, Any]] = []
    for r in list_workflow_runs(limit=30):
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
                        "run_id": r.get("id"),
                        "workflow_id": r.get("workflow_id"),
                        "node_id": node_run.get("node_id"),
                        "file_name": f.get("file_name") or f.get("name") or "",
                        "match_role": role,
                    })
    return out


def _scan_history_referencing(table_name: str, project_id: str) -> list[dict[str, Any]]:
    """history record 是 task 的运行实例 —— task_name 命中或关联 task 引用过 table_name。
    MVP 简化：只看 task_name 完全 / 部分匹配。"""
    target = table_name.lower()
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in list_result_history(project_id=project_id):
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
        # list_workflow_runs 只返回 summary（不含 nodes），按 run_id 拉完整 payload
        # —— 跟 lineage_index._rebuild_locked 同样的两阶段。
        for summary in list_workflow_runs(limit=run_limit):
            rid = str(summary.get("run_id") or "")
            if not rid:
                continue
            full = get_workflow_run(rid)
            if not full:
                continue
            run_id = rid
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


def get_column_lineage(
    table_name: str,
    column_name: str,
    *,
    project_id: str = "",
    run_limit: int = 50,
) -> dict[str, list[dict[str, Any]]]:
    """S1.B：字段血缘热点深化 —— 给定 (table, column)，返回上下游字段链。

    返回 `{"upstream": [{table, column, count}], "downstream": [...]}`。
    upstream = 这个字段从哪些 source 字段来（看 insert_mappings 中
    target=(table.column) 的 source_columns）；downstream = 这个字段流向
    哪些 target 字段（看 insert_mappings 中 source 含 table.column 的
    target_column）。

    数据源跟 get_table_columns 一样：最近 run_limit 个 workflow_run 的 lineage
    insert_mappings。同 (src_t, src_c, dst_t, dst_c) 的边累加 count，不去重，
    让前端可以排序"哪条字段链最频繁"。
    """
    if not table_name or not table_name.strip():
        raise ValueError("table_name is required")
    if not column_name or not column_name.strip():
        raise ValueError("column_name is required")
    target_t = table_name.strip().lower()
    target_t_base = target_t.split(".")[-1]
    target_c = column_name.strip().lower()

    upstream_counter: dict[tuple[str, str], int] = {}    # (src_table, src_col) → count
    downstream_counter: dict[tuple[str, str], int] = {}  # (dst_table, dst_col) → count

    def _matches_table(t: str) -> bool:
        t = t.strip().lower()
        return t == target_t or t == target_t_base

    def _matches_source_col(sc: str, source_tables: list[str]) -> bool:
        """source_column 是否指向 target.target_c。三种形式：
        - 完全限定 'table.col' → 比较两段
        - 单源 mapping 且 unqualified col → 比较 col
        - 多源 + unqualified → 拒绝（无法确认归属，跟 get_table_columns 同规则）
        """
        sc_lower = sc.strip().lower()
        if "." in sc_lower:
            parts = sc_lower.rsplit(".", 1)
            return _matches_table(parts[0]) and parts[1] == target_c
        # unqualified
        if len(source_tables) == 1 and _matches_table(source_tables[0]):
            return sc_lower == target_c
        return False

    try:
        for summary in list_workflow_runs(limit=run_limit):
            rid = str(summary.get("run_id") or "")
            if not rid:
                continue
            full = get_workflow_run(rid)
            if not full:
                continue
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
                        target_table = str(mapping.get("target_table") or "")
                        target_column = str(mapping.get("target_column") or "").strip().lower()
                        source_tables = [str(t) for t in (mapping.get("source_tables") or [])]
                        source_cols = [str(s) for s in (mapping.get("source_columns") or [])]

                        # upstream：mapping 写到 (target_t, target_c)，则它的 source_columns 是 upstream
                        if _matches_table(target_table) and target_column == target_c:
                            for sc in source_cols:
                                sc_lower = sc.strip().lower()
                                if "." in sc_lower:
                                    parts = sc_lower.rsplit(".", 1)
                                    src_t, src_c = parts[0], parts[1]
                                else:
                                    # unqualified —— 仅单源 mapping 能归到 source_tables[0]
                                    if len(source_tables) != 1:
                                        continue
                                    src_t, src_c = source_tables[0].lower(), sc_lower
                                if not src_t or not src_c:
                                    continue
                                key = (src_t, src_c)
                                upstream_counter[key] = upstream_counter.get(key, 0) + 1

                        # downstream：mapping 的某个 source_column 是当前 (target_t, target_c)
                        # → 它的 target 是 downstream
                        if any(_matches_source_col(sc, source_tables) for sc in source_cols):
                            if target_table and target_column:
                                key = (target_table.lower(), target_column)
                                # 排除自指：target == 当前节点
                                if not (_matches_table(target_table) and target_column == target_c):
                                    downstream_counter[key] = downstream_counter.get(key, 0) + 1
    except Exception:  # pragma: no cover
        return {"upstream": [], "downstream": []}

    def _to_list(counter: dict[tuple[str, str], int]) -> list[dict[str, Any]]:
        return sorted(
            [{"table": t, "column": c, "count": n} for (t, c), n in counter.items()],
            key=lambda x: x["count"], reverse=True,
        )

    return {
        "upstream": _to_list(upstream_counter),
        "downstream": _to_list(downstream_counter),
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
