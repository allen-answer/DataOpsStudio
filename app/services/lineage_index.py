"""Phase 10 #3 v1：全局 lineage 索引（lazy + TTL + workflow_run 触发失效）。

汇集最近 N 个 workflow_run 中所有 lineage 节点 output 的 graph_edges /
table_roles / target_summary，给前端一个"按 asset_id 直接查图"的入口，
不用每次都提交完整 graph 数据。

设计要点：
- **存储**：纯内存 dict + 双向邻接表。够用直到引入 Repository 层（CLAUDE.md
  Phase 9 ADR 6）/ SQLite store
- **触发失效**：
  1. 显式 `invalidate()`（提供 admin endpoint）
  2. TTL（默认 300s）
  3. workflow_run 数量变化（粗粒度，避免漏掉新跑的）
- **数据来源**：`list_workflow_runs(limit=50)`，每个 run 扫所有 type=lineage
  的 node_run 取 output；取不到 graph_edges 字段就跳过（兼容 batch lineage
  output 用 `table_edges` 字段名 —— `_normalize_output` 已经把两个名都填上了）
- **table_meta 聚合**：同一表在多个 run 中出现 → role 取出现频次最高的，
  refresh_modes 合并去重，记 last_seen_run_id
- **线程安全**：rebuild 用 RLock 包，避免 race；query 期间允许并发读
"""
from __future__ import annotations

import logging
import threading
import time
from collections import Counter
from typing import Any, Iterable, Literal

from app.services.lineage_graph_query import bfs_subgraph
from app.services.workflow_history import (
    get_cached_run_payloads,
    list_workflow_runs,
)


logger = logging.getLogger(__name__)


Direction = Literal["upstream", "downstream", "both"]


_DEFAULT_TTL_SECONDS = 300.0
_DEFAULT_RUN_LIMIT = 50


class LineageIndex:
    """全局 lineage 反向索引。

    线程安全 —— rebuild 用 RLock 串行；query 操作返回快照（不持锁），所以
    rebuild 进行时其它线程能继续用旧索引（最终一致）。
    """

    def __init__(self, *, ttl_seconds: float = _DEFAULT_TTL_SECONDS, run_limit: int = _DEFAULT_RUN_LIMIT) -> None:
        self._ttl = ttl_seconds
        self._run_limit = run_limit
        self._lock = threading.RLock()
        self._built_at: float = 0.0
        self._source_run_count: int = 0
        # 节点元数据：name → {schemas, primary_role, refresh_modes, last_seen_run_id, ...}
        self._table_meta: dict[str, dict[str, Any]] = {}
        # 双向邻接（用 list 保留多次出现的边）
        self._edges_forward: dict[str, list[dict[str, Any]]] = {}
        self._edges_backward: dict[str, list[dict[str, Any]]] = {}
        # 全部 edges 的扁平拷贝（去重后；query 端会按 asset_id BFS）
        self._all_edges: list[dict[str, Any]] = []
        # 全表 roles（query 端用）
        self._all_table_roles: list[dict[str, Any]] = []

    # ─── 失效 / 重建 ─────────────────────────────────────────────────────────

    def invalidate(self) -> None:
        """显式失效 —— 下次 query 触发 rebuild。"""
        with self._lock:
            self._built_at = 0.0

    def _maybe_rebuild(self) -> None:
        """决定是否需要 rebuild：TTL 过期 / 从未 build / source 数变了。"""
        if not self._needs_rebuild():
            return
        with self._lock:
            # 进锁后重新检查 —— 可能另一线程已经 rebuild 了
            if not self._needs_rebuild():
                return
            self._rebuild_locked()

    def _needs_rebuild(self) -> bool:
        """需不需要重建（不持锁；可能 false positive 但有锁内复检兜底）。"""
        if not self._built_at:
            return True
        if (time.time() - self._built_at) >= self._ttl:
            return True
        try:
            count = len(list_workflow_runs(limit=self._run_limit))
        except Exception:
            return False  # cannot determine → 信任当前缓存
        return count != self._source_run_count

    def _rebuild_locked(self) -> None:
        """实际重建 —— 必须持锁调用。"""
        started = time.perf_counter()
        # 走 workflow_history.get_cached_run_payloads —— 跟 assets / search 共享
        # 同一份解析后的 payloads。LineageIndex 自身的 TTL+run_count 检查仍
        # 保留（其内部状态比单纯 payload 更复杂），但底层 JSON parse 不重复。
        try:
            runs = list(get_cached_run_payloads(self._run_limit))
        except Exception as exc:  # pragma: no cover
            logger.warning("lineage index: get_cached_run_payloads failed: %s", exc)
            runs = []
        # source_run_count 跟 _needs_rebuild() 里的 count 用同一指标
        # （list_workflow_runs 长度），避免「JSON corrupted 让两个 count 永远不等
        # → constant rebuild」。
        try:
            run_count_for_invalidation = len(list_workflow_runs(limit=self._run_limit))
        except Exception:  # pragma: no cover
            run_count_for_invalidation = len(runs)

        edges: list[dict[str, Any]] = []
        edge_seen: set[tuple[str, str, str]] = set()  # (src, tgt, edge_type) 去重
        role_by_table: dict[str, Counter[str]] = {}
        roles_set_by_table: dict[str, set[str]] = {}
        refresh_by_table: dict[str, set[str]] = {}
        last_seen: dict[str, tuple[str, str]] = {}  # name → (run_id, started_at)
        schemas_by_table: dict[str, set[str]] = {}

        for run in runs:
            # WorkflowRun model 字段是 run_id，不是 id
            run_id = str(run.get("run_id") or run.get("id") or "")
            run_started_at = str(run.get("started_at") or run.get("created_at") or "")
            for node_run in (run.get("nodes") or []):
                if str(node_run.get("type") or "").lower() != "lineage":
                    continue
                output = node_run.get("output") or {}
                # 1. graph_edges 聚合
                for edge in output.get("graph_edges") or output.get("edges") or []:
                    if not isinstance(edge, dict):
                        continue
                    src = str(edge.get("source_table") or edge.get("source") or "").strip()
                    tgt = str(edge.get("target_table") or edge.get("target") or "").strip()
                    if not src or not tgt:
                        continue
                    edge_type = str(edge.get("edge_type") or edge.get("type") or "table")
                    key = (src, tgt, edge_type)
                    if key in edge_seen:
                        continue
                    edge_seen.add(key)
                    # 归一化的 edge —— 留下原 dict 的关键字段
                    edges.append({
                        "source_table": src,
                        "target_table": tgt,
                        "edge_type": edge_type,
                        "confidence": str(edge.get("confidence") or "high"),
                        "script": edge.get("script") or "",
                        "run_id": run_id,
                    })
                    for tab in (src, tgt):
                        last_seen[tab] = (run_id, run_started_at)
                        if "." in tab:
                            schemas_by_table.setdefault(tab, set()).add(tab.split(".")[0])
                # 2. table_roles 聚合
                for role_entry in output.get("table_roles") or []:
                    if not isinstance(role_entry, dict):
                        continue
                    name = str(role_entry.get("table") or "").strip()
                    if not name:
                        continue
                    primary = str(role_entry.get("primary_role") or "").strip()
                    if primary:
                        role_by_table.setdefault(name, Counter())[primary] += 1
                    for r in role_entry.get("roles") or []:
                        roles_set_by_table.setdefault(name, set()).add(str(r))
                # 3. target_summary 聚合（refresh_mode）
                for ts in output.get("target_summary") or []:
                    if not isinstance(ts, dict):
                        continue
                    name = str(ts.get("target_table") or "").strip()
                    if not name:
                        continue
                    rm = ts.get("refresh_mode")
                    if rm:
                        refresh_by_table.setdefault(name, set()).add(str(rm))

        # 装配 forward / backward 邻接
        forward: dict[str, list[dict[str, Any]]] = {}
        backward: dict[str, list[dict[str, Any]]] = {}
        for edge in edges:
            forward.setdefault(edge["source_table"], []).append(edge)
            backward.setdefault(edge["target_table"], []).append(edge)

        # 装配节点元数据
        all_tables: set[str] = set(forward.keys()) | set(backward.keys()) | set(role_by_table.keys()) | set(refresh_by_table.keys())
        meta: dict[str, dict[str, Any]] = {}
        for name in all_tables:
            primary_role = ""
            counter = role_by_table.get(name)
            if counter:
                primary_role = counter.most_common(1)[0][0]
            meta[name] = {
                "name": name,
                "primary_role": primary_role,
                "roles": sorted(roles_set_by_table.get(name, set())),
                "refresh_modes": sorted(refresh_by_table.get(name, set())),
                "upstream_count": len(backward.get(name, [])),
                "downstream_count": len(forward.get(name, [])),
                "last_seen_run_id": last_seen.get(name, ("", ""))[0],
                "last_seen_at": last_seen.get(name, ("", ""))[1],
                "schemas": sorted(schemas_by_table.get(name, set())),
            }

        # 装配 table_roles list（query 端 role_filter 用）
        all_roles = [
            {"table": name, "primary_role": m["primary_role"], "roles": m["roles"]}
            for name, m in meta.items()
            if m["primary_role"]
        ]

        # 提交（持锁状态下原子赋值）
        self._edges_forward = forward
        self._edges_backward = backward
        self._all_edges = edges
        self._all_table_roles = all_roles
        self._table_meta = meta
        self._source_run_count = run_count_for_invalidation
        self._built_at = time.time()
        elapsed = time.perf_counter() - started
        logger.info(
            "lineage index rebuilt: %d runs, %d tables, %d edges, %.3fs",
            len(runs), len(meta), len(edges), elapsed,
        )

    # ─── query 接口 ──────────────────────────────────────────────────────────

    def query_subgraph(
        self,
        asset_id: str,
        *,
        direction: Direction = "both",
        depth: int = 1,
        edge_types: Iterable[str] | None = None,
        confidences: Iterable[str] | None = None,
        role_filter: str | None = None,
        max_nodes: int = 2000,
    ) -> dict[str, Any]:
        """从全局索引切子图。复用 lineage_graph_query.bfs_subgraph 逻辑。"""
        self._maybe_rebuild()
        # 拍快照（避免 BFS 期间被 rebuild 替换）
        with self._lock:
            edges = list(self._all_edges)
            roles = list(self._all_table_roles)
        return bfs_subgraph(
            edges=edges,
            asset_id=asset_id,
            direction=direction,
            depth=depth,
            edge_types=edge_types,
            confidences=confidences,
            table_roles=roles,
            role_filter=role_filter,
            max_nodes=max_nodes,
        )

    def get_table_metadata(self, name: str) -> dict[str, Any] | None:
        self._maybe_rebuild()
        with self._lock:
            return dict(self._table_meta.get(name)) if name in self._table_meta else None

    def stats(self) -> dict[str, Any]:
        self._maybe_rebuild()
        with self._lock:
            return {
                "table_count": len(self._table_meta),
                "edge_count": len(self._all_edges),
                "source_run_count": self._source_run_count,
                "built_at": self._built_at,
                "ttl_seconds": self._ttl,
            }

    def list_assets(self, *, limit: int = 200, prefix: str = "") -> list[dict[str, Any]]:
        """列举索引里的资产，用作 admin 控制台 / autocomplete。"""
        self._maybe_rebuild()
        with self._lock:
            items = list(self._table_meta.values())
        if prefix:
            p = prefix.lower()
            items = [x for x in items if x["name"].lower().startswith(p)]
        # 按 last_seen_at 倒序，缺失的放后面
        items.sort(key=lambda x: x.get("last_seen_at") or "", reverse=True)
        return items[:limit]


# 模块级单例 —— 跟 datasource_store / task_store 一致的"懒式全局状态"
_INSTANCE = LineageIndex()


def get_lineage_index() -> LineageIndex:
    return _INSTANCE


__all__ = ["LineageIndex", "get_lineage_index"]
