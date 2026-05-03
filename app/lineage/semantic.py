"""把 Track B 各模块的输出收口成一个 semantic_lineage 字段。

Phase 7 Track B 第 7 项。给前端 / 第三方一个 "脚本干啥的" 的结构化语义视图：
  • procedures：脚本里的存储过程列表（name / kind / segment_count）
  • targets：每张目标表的整合视图（合并 target_summary + table_roles + titles）
  • business_groups + grouped_edges：业务分组（依赖第 5 项 YAML 配置，先留空）
  • observations：派生的人类可读观察（"X 张目标表，Y 张全量重刷……"）
  • risks：派生的风险点（parse_error / 低置信动态 SQL）

不重新解析 SQL —— 完全基于 `analyze_sql_lineage()` 已有结果做汇总和派生，所以
能加塞到主流程末尾、不增加 sqlglot 解析成本。
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any


def build_semantic_lineage(result: dict[str, Any]) -> dict[str, Any]:
    """汇总 analyze_sql_lineage 的产物为 semantic_lineage。

    入参直接是 analyze_sql_lineage 即将返回的 dict（在它自己 return 前调用），
    不修改入参，返回新字典。
    """
    procedures = _build_procedures(result.get("procedure_segments", []))
    targets = _build_targets(
        result.get("target_summary", []),
        result.get("table_roles", []),
    )
    observations = _build_observations(
        targets, procedures,
        result.get("table_roles", []),
        result.get("dynamic_sql_count", 0),
    )
    risks = _build_risks(
        result.get("parse_errors", []),
        result.get("dynamic_sql_segments", []),
    )
    return {
        "procedures": procedures,
        "targets": targets,
        "business_groups": [],
        "grouped_edges": [],
        "observations": observations,
        "risks": risks,
    }


def _build_procedures(procedure_segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同名 procedure 多段 DML 折叠成一条记录。"""
    grouped: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"name": "", "kind": "", "segment_count": 0}
    )
    for seg in procedure_segments:
        name = seg.get("procedure_name", "") or ""
        if not name:
            continue
        proc = grouped[name]
        proc["name"] = name
        proc["kind"] = seg.get("procedure_kind", "") or proc["kind"]
        proc["segment_count"] += 1
    return list(grouped.values())


def _build_targets(
    target_summary: list[dict[str, Any]],
    table_roles: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    role_map = {(r.get("table") or "").lower(): r for r in table_roles}
    out: list[dict[str, Any]] = []
    for summary in target_summary:
        name = summary.get("target_table", "") or ""
        role_entry = role_map.get(name.lower(), {})
        out.append({
            "table": name,
            "primary_role": role_entry.get("primary_role") or "target",
            "roles": role_entry.get("roles") or ["target"],
            "refresh_mode": summary.get("refresh_mode"),
            "titles": summary.get("titles", []) or [],
            "counts": {
                "insert": summary.get("insert_count", 0),
                "update": summary.get("update_count", 0),
                "merge": summary.get("merge_count", 0),
                "delete": summary.get("delete_count", 0),
                "truncate": summary.get("truncate_count", 0),
            },
        })
    return out


def _build_observations(
    targets: list[dict[str, Any]],
    procedures: list[dict[str, Any]],
    table_roles: list[dict[str, Any]],
    dynamic_sql_count: int,
) -> list[str]:
    """从已聚合数据派生人类可读观察。返回顺序固定，方便测试和前端展示。"""
    observations: list[str] = []
    if targets:
        observations.append(f"脚本写入 {len(targets)} 张目标表")
        refresh_counts = Counter(t["refresh_mode"] for t in targets if t["refresh_mode"])
        full_refresh = refresh_counts.get("truncate_insert", 0) + refresh_counts.get("delete_insert", 0)
        if full_refresh:
            observations.append(
                f"其中 {full_refresh} 张走全量重刷（truncate_insert / delete_insert）"
            )
        merge_count = refresh_counts.get("merge", 0)
        if merge_count:
            observations.append(f"{merge_count} 张走 MERGE 合并写入")
        intermediate = sum(1 for t in targets if t["primary_role"] == "intermediate")
        if intermediate:
            observations.append(f"{intermediate} 张为中转表（既写又读）")
    if procedures:
        total_segs = sum(p["segment_count"] for p in procedures)
        observations.append(f"含 {len(procedures)} 个存储过程，共 {total_segs} 段 DML")
    dblink_count = sum(
        1 for r in table_roles if "remote_dblink" in (r.get("roles") or [])
    )
    if dblink_count:
        observations.append(f"{dblink_count} 张表通过 Oracle DB Link 引用远端")
    if dynamic_sql_count:
        observations.append(f"识别到 {dynamic_sql_count} 段动态 SQL")
    return observations


# 风险等级 —— 按 confidence / 错误类型映射，前端按 level 着色。
_DYNAMIC_SQL_RISK_LEVEL = {
    "low": "medium",     # 变量拼接 / 兜底字面量 -> 极可能漏识别血缘
    "medium": "low",     # 长字面量识别 -> 大概率是真 SQL，但置信度不足
}


def _build_risks(
    parse_errors: list[dict[str, Any]],
    dynamic_sql_segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """从 warnings 数据派生风险点。每条 risk 含 level / type / message 三元组。"""
    risks: list[dict[str, Any]] = []
    for err in parse_errors:
        risks.append({
            "level": "high",
            "type": "parse_error",
            "message": err.get("error", "") or "解析失败",
        })
    for seg in dynamic_sql_segments:
        confidence = seg.get("confidence", "") or ""
        level = _DYNAMIC_SQL_RISK_LEVEL.get(confidence)
        if level is None:
            continue  # high 置信不算风险
        risks.append({
            "level": level,
            "type": "dynamic_sql_low_confidence",
            "message": f"动态 SQL（来源 {seg.get('source', '?')}，置信 {confidence}），可能漏识别血缘",
        })
    return risks
