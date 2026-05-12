"""Slow SQL analysis —— EXPLAIN + 规则推断 issues + 优化建议（Phase 12 切片 6）。

MVP 范围：
- 仅 MySQL（其他方言 plan 列名不一致，留下个切片）
- 接 SELECT/WITH 用户 SQL（过 sql_guard 拦 DML/DDL），自己 prepend `EXPLAIN`
- 解析 EXPLAIN plan rows，按 4 条规则触发 issue + suggestion：
  * type=ALL → 全表扫描，建议加索引
  * Extra 含 filesort → ORDER BY 没用上索引
  * Extra 含 Using temporary → GROUP BY / DISTINCT 触发临时表
  * rows > 10000 且 type 是 all/index → 高扫描行数没走 key

下切片可接 AI enrichment：把 plan + heuristic issues 喂给 LLM 生成
更准的优化方案（对比 expected_optimizations）。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from app.dbclients.factory import fetch_rows
from app.services.repositories import datasource_store
from app.utils.sql_guard import validate_readonly_sql


class SlowSqlError(Exception):
    pass


@dataclass
class Issue:
    severity: str  # "warning" | "info"
    code: str
    message: str
    table: str = ""
    detail: str = ""


@dataclass
class Suggestion:
    code: str
    message: str
    sql: str = ""  # 可选：建议的 CREATE INDEX 等 DDL


def analyze_sql(
    datasource_id: str,
    sql: str,
    *,
    max_plan_rows: int = 100,
) -> dict[str, Any]:
    """跑 EXPLAIN 拿 plan + 触发规则。返回 dict 供 endpoint 直接 JSON 化。"""
    if not datasource_id.strip():
        raise SlowSqlError("datasource_id is required")
    validate_readonly_sql(sql)  # 拦 DML/DDL，避免 EXPLAIN 包了一个删表语句
    source = datasource_store.get(datasource_id)
    if source is None:
        raise SlowSqlError(f"datasource not found: {datasource_id}")
    dialect = source.db_type.value.lower()
    if dialect != "mysql":
        raise SlowSqlError(
            f"slow-sql analyze currently supports MySQL only; got {source.db_type.value}"
        )
    explain_sql = f"EXPLAIN {sql.rstrip().rstrip(';').strip()}"
    try:
        rows = fetch_rows(source, explain_sql, max_rows=max_plan_rows)
    except Exception as exc:
        raise SlowSqlError(f"EXPLAIN failed: {exc}") from exc
    issues = detect_issues(rows)
    suggestions = build_suggestions(issues)
    return {
        "dialect": "mysql",
        "explain_sql": explain_sql,
        "plan": rows,
        "issues": [asdict(i) for i in issues],
        "suggestions": [asdict(s) for s in suggestions],
    }


# ─── pure rules（端到端 DB 测试外可独立跑） ──────────────────────────────────


def detect_issues(plan_rows: list[dict[str, Any]]) -> list[Issue]:
    """对每条 EXPLAIN row 匹配 4 条规则。同表多 issue 不去重 —— 让用户看到全貌。"""
    issues: list[Issue] = []
    for row in plan_rows:
        table = str(row.get("table") or "")
        rtype = str(row.get("type") or "").lower()
        extra = str(row.get("Extra") or row.get("extra") or "")
        extra_lower = extra.lower()
        rows_val = row.get("rows")
        try:
            rcount = int(rows_val) if rows_val is not None else 0
        except (TypeError, ValueError):
            rcount = 0

        if rtype == "all":
            issues.append(Issue(
                severity="warning",
                code="full_table_scan",
                message=f"{table or '<unknown>'} 走全表扫描（type=ALL）",
                table=table,
                detail=f"rows≈{rcount}",
            ))
        if "filesort" in extra_lower:
            issues.append(Issue(
                severity="warning",
                code="filesort",
                message=f"{table or '<unknown>'} ORDER BY 触发 filesort（未走索引）",
                table=table,
                detail=extra,
            ))
        if "using temporary" in extra_lower:
            issues.append(Issue(
                severity="warning",
                code="using_temporary",
                message=f"{table or '<unknown>'} GROUP BY / DISTINCT 用了临时表",
                table=table,
                detail=extra,
            ))
        if rcount > 10000 and rtype in ("all", "index"):
            issues.append(Issue(
                severity="warning",
                code="high_row_scan",
                message=f"{table or '<unknown>'} 扫描行数偏高（{rcount}）",
                table=table,
                detail=f"type={rtype}",
            ))
    return issues


def build_suggestions(issues: list[Issue]) -> list[Suggestion]:
    """按 issue code 派生建议，跨多行 issue 同类去重（避免连排 5 个『加索引』）。"""
    out: list[Suggestion] = []
    seen: set[str] = set()
    for issue in issues:
        if issue.code == "full_table_scan":
            tag = f"add_index:{issue.table}"
            if tag in seen:
                continue
            seen.add(tag)
            out.append(Suggestion(
                code="add_index",
                message=f"考虑在 {issue.table or '该表'} 的 WHERE / JOIN 列上加索引消除全表扫描",
            ))
        elif issue.code == "filesort":
            tag = f"order_index:{issue.table}"
            if tag in seen:
                continue
            seen.add(tag)
            out.append(Suggestion(
                code="order_by_index",
                message=f"为 {issue.table or '该表'} 的 ORDER BY 列建索引可避免 filesort",
            ))
        elif issue.code == "using_temporary":
            if "group_index" in seen:
                continue
            seen.add("group_index")
            out.append(Suggestion(
                code="group_by_index",
                message="GROUP BY / DISTINCT 列上建索引可消除临时表（Using temporary）",
            ))
        elif issue.code == "high_row_scan":
            tag = f"narrow_scan:{issue.table}"
            if tag in seen:
                continue
            seen.add(tag)
            out.append(Suggestion(
                code="narrow_scan",
                message=f"{issue.table or '该表'} 扫描行数过大，考虑加 WHERE 过滤 / 复合索引 / 分区",
            ))
    return out
