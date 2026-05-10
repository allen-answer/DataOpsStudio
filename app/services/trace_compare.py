"""Phase 11 MVP — compare × lineage 联动编排器。

给定 focal `(table, column)`，按上游字段血缘扫到 N 跳，把每条
`(upstream → downstream)` 边变成一个 compare 节点配置。Caller 拿到的
是 WorkflowCreate-shaped draft，可保存为正式 workflow 也可直接 ad-hoc 跑。

边界（MVP 范围）：
- 只走 `direct` 策略 —— 假设 upstream 列和 downstream 列值一致（直传 / 改名）。
  聚合 / 过滤 / 类型转换的口径检查需 AI 兜底或人工，不在本切片。
- `key_column` 默认全链一致；caller 可传 `per_table_keys` 按表覆盖。
- `datasource_map` 必须覆盖链上所有表，缺一就在该 hop 上挂 `unmapped_tables`
  让前端提示用户补 datasource 选择。
- `sample_keys` 不传 → 不加 WHERE 过滤（全表 compare）；传了 → IN 子句过滤。
- 标识符走白名单校验，避免 caller 传 `; DROP TABLE` 拼进 SQL。
"""
from __future__ import annotations

import re
from typing import Any

from app.services.assets import get_column_lineage


# 标识符白名单：alphanum / underscore / dot（schema.table）/ $ / # —— 这是
# Oracle / DM / DB2 都允许的合法 SQL 标识符字符集。
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$#.]*$")


def trace_compare(
    *,
    table: str,
    column: str,
    key_column: str,
    base_task_id: str,
    sample_keys: list[Any] | None = None,
    datasource_map: dict[str, str] | None = None,
    per_table_keys: dict[str, str] | None = None,
    depth: int = 3,
    project_id: str = "",
    run_limit: int = 50,
) -> dict[str, Any]:
    """Build chain summary + workflow-draft from a column's upstream lineage."""
    if not table or not table.strip():
        raise ValueError("table is required")
    if not column or not column.strip():
        raise ValueError("column is required")
    if not key_column or not key_column.strip():
        raise ValueError("key_column is required (used to filter sample rows)")
    if not base_task_id or not base_task_id.strip():
        raise ValueError("base_task_id is required (compare nodes need a shell task id)")
    _validate_ident(table, "table")
    _validate_ident(column, "column")
    _validate_ident(key_column, "key_column")

    if depth < 1:
        depth = 1
    if depth > 10:
        depth = 10

    sample_keys = list(sample_keys or [])
    datasource_map = {k.lower(): v for k, v in (datasource_map or {}).items() if k}
    per_table_keys = {k.lower(): v for k, v in (per_table_keys or {}).items() if k}
    for tbl, key in per_table_keys.items():
        _validate_ident(key, f"per_table_keys[{tbl}]")

    lineage = get_column_lineage(
        table_name=table,
        column_name=column,
        project_id=project_id,
        run_limit=run_limit,
        depth=depth,
        max_nodes=200,
    )
    upstream = lineage.get("upstream", [])

    edges = _build_edges(upstream, focal_table=table, focal_column=column)

    nodes: list[dict[str, Any]] = []
    warnings: list[str] = []
    for idx, edge in enumerate(edges, start=1):
        up = edge["upstream"]
        down = edge["downstream"]
        up_ds = datasource_map.get(up["table"].lower(), "")
        down_ds = datasource_map.get(down["table"].lower(), "")
        unmapped = [t for t, ds in ((up["table"], up_ds), (down["table"], down_ds)) if not ds]

        up_key = per_table_keys.get(up["table"].lower(), key_column)
        down_key = per_table_keys.get(down["table"].lower(), key_column)

        try:
            _validate_ident(up["table"], f"hop{idx}.upstream.table")
            _validate_ident(up["column"], f"hop{idx}.upstream.column")
            _validate_ident(down["table"], f"hop{idx}.downstream.table")
            _validate_ident(down["column"], f"hop{idx}.downstream.column")
        except ValueError as exc:
            warnings.append(f"Hop {idx} 跳过：{exc}")
            continue

        nodes.append({
            "id": f"cmp_hop{idx}",
            "type": "compare",
            "name": f"{up['table']}.{up['column']} → {down['table']}.{down['column']}",
            # 各 hop 之间没有真正的数据依赖（每个都是独立的两端 compare），
            # 默认无 depends_on 让 engine 并发执行，更快出结果。
            "depends_on": [],
            "config": {
                "task_id": base_task_id,
                "source_sql_override": _build_sample_sql(up["table"], up_key, up["column"], sample_keys),
                "target_sql_override": _build_sample_sql(down["table"], down_key, down["column"], sample_keys),
                "key_columns_override": [up_key],
                # workflow_engine 不读这个字段；保留给前端 LineageGraphPanel
                # 跑完后按 hop 着色 / 组装 trace 视图。
                "_trace_compare": {
                    "hop": edge["hop"],
                    "strategy": edge["strategy"],
                    "upstream": up,
                    "downstream": down,
                    "datasource_source": up_ds,
                    "datasource_target": down_ds,
                    "unmapped_tables": unmapped,
                },
            },
        })
        if unmapped:
            warnings.append(
                f"Hop {idx} ({up['table']} → {down['table']}) 有未映射的 datasource: "
                + ", ".join(unmapped)
            )

    return {
        "focal": {"table": table, "column": column},
        "chain": edges,
        "workflow_draft": {
            "name": f"Trace compare · {table}.{column}",
            "description": (
                f"Phase 11 MVP — 沿字段血缘对 {table}.{column} 逐跳对比 "
                f"({len(nodes)} hops，depth={depth})"
            ),
            "nodes": nodes,
            "default_variables": {},
            "tags": ["trace-compare"],
            "project_id": project_id,
        },
        "warnings": warnings,
        "stats": {
            "edge_count": len(edges),
            "node_count": len(nodes),
            "upstream_truncated": bool(lineage.get("upstream_truncated")),
        },
    }


def _build_edges(
    upstream: list[dict[str, Any]],
    *,
    focal_table: str,
    focal_column: str,
) -> list[dict[str, Any]]:
    """`get_column_lineage` 返回 BFS 节点 list；这里把每个节点变成一条
    `(upstream → downstream)` 边。

    `hop=1` 节点的 downstream 是 focal；`hop>=2` 节点的 downstream 是它的
    `from` 父节点（"<parent_t>.<parent_c>"）。
    """
    focal_key = f"{focal_table}.{focal_column}"
    edges: list[dict[str, Any]] = []
    for item in upstream:
        upstream_t = str(item.get("table") or "").strip()
        upstream_c = str(item.get("column") or "").strip()
        if not upstream_t or not upstream_c:
            continue
        hop = int(item.get("hop") or 1)
        parent = str(item.get("from") or "").strip() or focal_key
        if "." in parent:
            ds_t, ds_c = parent.rsplit(".", 1)
        else:
            # parent 没带点 = focal 但 focal_table 也没点的边角场景，回落原值
            ds_t, ds_c = parent, focal_column
        edges.append({
            "hop": hop,
            "upstream": {"table": upstream_t, "column": upstream_c},
            "downstream": {"table": ds_t, "column": ds_c},
            "strategy": "direct",
        })
    edges.sort(key=lambda e: (e["hop"], e["upstream"]["table"], e["upstream"]["column"]))
    return edges


def _build_sample_sql(table: str, key_col: str, value_col: str, sample_keys: list[Any]) -> str:
    """SELECT key_col, value_col FROM table [WHERE key_col IN (...)] ORDER BY key_col。

    流式 compare 要求两端按 key 同序，所以一律加 ORDER BY。
    """
    base = f"SELECT {key_col}, {value_col} FROM {table}"
    if sample_keys:
        rendered = ", ".join(_render_literal(v) for v in sample_keys)
        base += f" WHERE {key_col} IN ({rendered})"
    base += f" ORDER BY {key_col}"
    return base


def _render_literal(value: Any) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _validate_ident(value: str, label: str) -> None:
    if not _IDENT_RE.match(value or ""):
        raise ValueError(f"非法标识符 {label}={value!r}")
