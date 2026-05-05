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
from app.services.workflow_history import list_workflow_runs


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


__all__ = ["get_table_asset", "list_datasource_assets"]
