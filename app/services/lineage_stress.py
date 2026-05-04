"""Phase 10 第 1 项：血缘大图压测 fixture 生成器。

生成合成的 lineage 报告（graph_groups + graph_edges + table_roles +
target_summary），用于在浏览器里跑 G6 vs Cytoscape 双引擎压测，验证 300 /
1000 / 5000 节点下的渲染 / 筛选 / 聚焦 / compound 容器 / 内存表现。

**为什么需要**：当前 LineageGraphPanel 双引擎共存（G6 稳定 / Cytoscape
实验），但没有真实大图数据来判断 Cytoscape 是否能转正、各种交互的 P50/P95
延迟、内存占用。规则分析能跑出几十节点的 fixture（a_cispnew_f3045.sql 等
真实 Oracle proc），但没法稳定生成 1000+ 节点。这个生成器走纯合成路径，
避免污染血缘解析器的真实测试。

**用法**：
- HTTP：`GET /api/lineage/stress-fixture?size=1000` → 返回完整 lineage result
- 前端：访问 `#/lineage?stress=1000` 自动调上面这个端点把 result 填入
  `lineage.result`，跳过分析步骤
- 测量：Chrome DevTools Performance 录一段（init 渲染 → 拖动 → 缩放 → focal
  切换 → schema 折叠），看 main thread 耗时 / FPS / Memory 峰值。两个引擎
  各做一次，对比同操作下的开销

**fixture 结构**：
- 6 schemas（ods / dwd / dws / dim / ref / fct）按 30 / 25 / 20 / 5 / 5 / 15
  比例分布
- 边按 ods→dwd→dws→fct 主链 + dim/ref 跨切横边生成
- 每张目标表标 refresh_mode（采样 truncate_insert / append / merge / mixed）
- table_roles 按 schema 模式打 source_fact / intermediate / target / dimension /
  reference + 命名 role
"""
from __future__ import annotations

import random
from typing import Any

# schema 分布权重 —— 加起来为 100，分别落进 ods/dwd/dws/dim/ref/fct
_SCHEMA_WEIGHTS: list[tuple[str, int]] = [
    ("ods", 30), ("dwd", 25), ("dws", 20), ("fct", 15), ("dim", 5), ("ref", 5),
]
_LAYER_ORDER = {"ods": 0, "dim": 1, "ref": 1, "dwd": 2, "dws": 3, "fct": 4}

# upstream layer → 允许的 source schemas
_UPSTREAM_MAP = {
    "ods": [],
    "dim": [],
    "ref": [],
    "dwd": ["ods", "dim", "ref"],
    "dws": ["dwd", "dim", "ref"],
    "fct": ["dwd", "dws", "dim", "ref"],
}

_REFRESH_MODES = ["truncate_insert", "append", "merge", "delete_insert", "mixed"]
_PRIMARY_ROLE_BY_SCHEMA = {
    "ods": "source_fact", "dwd": "intermediate", "dws": "intermediate",
    "fct": "target", "dim": "dimension", "ref": "reference",
}


def _pick_schema(rng: random.Random) -> str:
    pivot = rng.randint(1, 100)
    cumulative = 0
    for schema, weight in _SCHEMA_WEIGHTS:
        cumulative += weight
        if pivot <= cumulative:
            return schema
    return "ods"


def build_stress_fixture(size: int, *, seed: int = 42) -> dict[str, Any]:
    """生成 size 个 table 的合成 lineage result。

    结果可以直接喂给前端 LineageReportView：包含 graph_groups / graph_edges
    / table_roles / target_summary / report / semantic_lineage 等关键字段。

    seed 固定 → 同 size 永远生成同一份 fixture，保证压测可重复。
    """
    if size < 1:
        raise ValueError("size must be >= 1")
    if size > 10000:
        raise ValueError("size capped at 10000 (前端在 10k+ 节点上压测意义不大)")

    rng = random.Random(seed)

    # 1. 生成 N 个 table，每张分配一个 schema
    tables: list[dict[str, Any]] = []
    by_schema: dict[str, list[str]] = {}
    for i in range(size):
        schema = _pick_schema(rng)
        name = f"{schema}.t_{schema}_{i:05d}"
        tables.append({"table": name, "schema": schema})
        by_schema.setdefault(schema, []).append(name)

    # 2. 生成边 —— 每张非 ods/dim/ref 的表挂 1~4 个上游 source
    edges: list[dict[str, Any]] = []
    for t in tables:
        schema = t["schema"]
        upstream_schemas = _UPSTREAM_MAP.get(schema, [])
        if not upstream_schemas:
            continue
        # 每张表挑 1~4 个上游表
        n_sources = rng.randint(1, 4)
        candidates: list[str] = []
        for s in upstream_schemas:
            candidates.extend(by_schema.get(s, []))
        if not candidates:
            continue
        n = min(n_sources, len(candidates))
        sources = rng.sample(candidates, n)
        for src in sources:
            edges.append({
                "source_table": src,
                "target_table": t["table"],
                "edge_type": "table",
                "confidence": rng.choice(["high", "high", "medium"]),  # 大多 high
                "script": f"stress_{rng.randint(0, max(1, size // 100))}.sql",
            })

    # 3. 标 target_summary —— 仅对有写入的表（即 dwd/dws/fct）
    target_summary: list[dict[str, Any]] = []
    for t in tables:
        if t["schema"] not in {"dwd", "dws", "fct"}:
            continue
        ins = rng.randint(1, 5)
        upd = rng.randint(0, 2)
        truncate = 1 if rng.random() < 0.3 else 0
        delete = 1 if rng.random() < 0.2 else 0
        refresh_mode = rng.choice(_REFRESH_MODES)
        target_summary.append({
            "target_table": t["table"],
            "insert_count": ins,
            "update_count": upd,
            "merge_count": 0,
            "delete_count": delete,
            "truncate_count": truncate,
            "delete_before_insert": bool(delete and ins),
            "truncate_before_insert": bool(truncate and ins),
            "refresh_mode": refresh_mode,
            "titles": [f"业务模块 {rng.randint(1, 20)}"] if rng.random() < 0.4 else [],
        })

    # 4. table_roles —— schema → primary_role 映射
    table_roles = [
        {
            "table": t["table"],
            "primary_role": _PRIMARY_ROLE_BY_SCHEMA.get(t["schema"], "source_fact"),
            "roles": [_PRIMARY_ROLE_BY_SCHEMA.get(t["schema"], "source_fact")],
        }
        for t in tables
    ]

    # 5. 模拟 LineageAnalysisReport 的 report 字段（让 LineageReportView 能渲染）
    report = {
        "scope": "single",
        "summary": {
            "input_assets": len([t for t in tables if t["schema"] == "ods"]),
            "output_assets": len([t for t in tables if t["schema"] == "fct"]),
            "process_steps": len(edges),
            "warnings": 0,
        },
        "input_assets": [{"name": t["table"], "kind": "table"} for t in tables if t["schema"] in {"ods", "dim", "ref"}][:200],
        "output_assets": [
            {"name": t["target_table"], "kind": "table", "refresh_mode": t["refresh_mode"]}
            for t in target_summary[:200]
        ],
        "process_steps": [],
        "table_lineage": edges[:500],
        "column_lineage": [],
        "impact_analysis": [],
        "risks": [],
        "ai_assist": {},
        "ai_inferred": {},
    }

    # 6. graph_groups —— 按"目标表"分组（跟 batch_analyzer._table_groups 同形态）。
    # useLineageGraphData 期望 {target_table, source_tables, dependency_tables} 三元组，
    # 不是按 schema 分组。之前写成 {schema, tables} 让前端图根本画不出边。
    by_target: dict[str, dict[str, Any]] = {}
    for edge in edges:
        target = edge["target_table"]
        group = by_target.get(target)
        if group is None:
            group = {
                "target_table": target,
                "source_tables": [],
                "dependency_tables": [],  # 留空：stress 不区分条件依赖
                "files": [],
            }
            by_target[target] = group
        if edge["source_table"] not in group["source_tables"]:
            group["source_tables"].append(edge["source_table"])
        f = edge.get("script") or ""
        if f and f not in group["files"]:
            group["files"].append(f)
    graph_groups = list(by_target.values())

    return {
        "stress_fixture": True,  # 让前端能识别这是合成数据
        "stress_size": size,
        "statement_count": 0,
        "tables": tables,
        "columns": [],
        "insert_mappings": [],
        "target_summary": target_summary,
        "table_roles": table_roles,
        "joins": [],
        "filters": [],
        "group_by": [],
        "unions": [],
        "variables": [],
        "aliases": [],
        "dynamic_sql_count": 0,
        "dynamic_sql_segments": [],
        "procedure_segments": [],
        "graph_edges": edges,
        "graph_groups": graph_groups,
        "parse_errors": [],
        "warnings": [],
        "statements": [],
        "semantic_lineage": {
            "procedures": [],
            "targets": [
                {
                    "table": ts["target_table"],
                    "primary_role": "target",
                    "roles": ["target"],
                    "refresh_mode": ts["refresh_mode"],
                    "titles": ts["titles"],
                    "counts": {
                        "insert": ts["insert_count"],
                        "update": ts["update_count"],
                        "merge": 0,
                        "delete": ts["delete_count"],
                        "truncate": ts["truncate_count"],
                    },
                }
                for ts in target_summary[:200]
            ],
            "business_groups": [],
            "grouped_edges": [],
            "observations": [
                f"压测 fixture：{size} 张表 / {len(edges)} 条边 / "
                f"{len(target_summary)} 张目标表",
            ],
            "risks": [],
        },
        "report": report,
    }


__all__ = ["build_stress_fixture"]
