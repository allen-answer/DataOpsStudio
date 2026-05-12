"""Slow SQL analysis —— EXPLAIN + 规则推断 issues + 优化建议（Phase 12 切片 6）。

MVP 范围：
- 仅 MySQL（其他方言 plan 列名不一致，留下个切片）
- 接 SELECT/WITH 用户 SQL（过 sql_guard 拦 DML/DDL），自己 prepend `EXPLAIN`
- 解析 EXPLAIN plan rows，按 4 条规则触发 issue + suggestion：
  * type=ALL → 全表扫描，建议加索引
  * Extra 含 filesort → ORDER BY 没用上索引
  * Extra 含 Using temporary → GROUP BY / DISTINCT 触发临时表
  * rows > 10000 且 type 是 all/index → 高扫描行数没走 key

Phase 12 切片 8：`enrich_via_ai` 把上面规则推断的 issues + plan + 原始 SQL
喂给 LLM provider，让它：
- 复核规则发现的 issues 是不是真问题（confirmed / false_positive / insufficient_info）
- 补漏：规则没抓到的问题（如「LEFT JOIN 没必要、应该是 INNER」）
- 给具体 DDL（CREATE INDEX / 改写 SQL）
- 如果 caller 传了 expected_optimizations（来自 yml workload），对比覆盖率
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass
from typing import Any

from app.dbclients.factory import fetch_rows
from app.services.repositories import datasource_store
from app.utils.sql_guard import validate_readonly_sql


logger = logging.getLogger(__name__)


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


# ─── AI enrichment（Phase 12 切片 8） ────────────────────────────────────────


SLOW_SQL_ENRICH_PROMPT = (
    "你是 MySQL 慢 SQL 优化助手。用户已经跑过 EXPLAIN 拿到 plan，并用规则推断了一组\n"
    "issues + suggestions（rule_issues / rule_suggestions 字段）。你需要在此基础上：\n"
    "1. 复核每条 rule_issue：confirmed（真问题）/ false_positive（误报）/ insufficient_info\n"
    "2. 补漏：规则没抓到的问题（如 LEFT JOIN 多余 / 子查询本可改 JOIN / 谓词不可索引化）\n"
    "3. 给具体优化 DDL 或 SQL 改写片段（sql 字段填可直接执行的语句）\n"
    "4. 如果 expected_optimizations 非空：找出 LLM 建议里命中的（matched）+ 漏掉的（missing），\n"
    "   coverage_pct = matched 数 / expected 总数 × 100\n\n"
    "硬性规则：\n"
    "- 仅返回 JSON 对象，含 summary / issue_review / extra_suggestions / expected_coverage 四键\n"
    "- summary：1~2 句中文总结这条 SQL 的核心性能问题\n"
    "- issue_review：每条 {code: 原 rule code, verdict: confirmed|false_positive|insufficient_info, rationale: 中文一句话}\n"
    "- extra_suggestions：每条 {message: 中文建议, sql: 可执行的 SQL 片段 或 空, confidence: high|medium|low}\n"
    "- expected_coverage：{matched: [...], missing: [...], coverage_pct: 数值}；\n"
    "  没传 expected_optimizations 时 matched=missing=[]，coverage_pct=0\n"
    "- 不要发明 plan 里没出现的表名 / 字段名；不要 markdown 围栏；不要 JSON 之外的解释"
)


@dataclass
class EnrichResult:
    ok: bool
    summary: str
    issue_review: list[dict[str, Any]]
    extra_suggestions: list[dict[str, Any]]
    expected_coverage: dict[str, Any]
    provider: str
    model: str
    elapsed_seconds: float
    error: str = ""


def enrich_via_ai(
    *,
    sql: str,
    plan: list[dict[str, Any]],
    issues: list[dict[str, Any]],
    suggestions: list[dict[str, Any]],
    expected_optimizations: list[str] | None = None,
    max_plan_chars: int = 4000,
) -> EnrichResult:
    """把 rule-driven 输出 + plan 喂给 LLM，拿回复核 + 补漏 + 覆盖率。

    走 lineage_ai 持久化的 provider 配置（mock / openai / anthropic / ollama）。
    provider=off 或没 api_key 时返回 ok=False + 占位字段，给 endpoint 走 200 降级。
    """
    # lazy import 避免 services 层依赖 api 层（_call_ai 住在 api/ai_utils）
    from app.api.ai_utils import _call_ai
    from app.services.lineage_ai import _config as _ai_config

    config = _ai_config()
    provider_name = (config.provider or "off").lower()
    started = time.perf_counter()
    base_result = EnrichResult(
        ok=False,
        summary="",
        issue_review=[],
        extra_suggestions=[],
        expected_coverage={"matched": [], "missing": list(expected_optimizations or []), "coverage_pct": 0.0},
        provider=provider_name,
        model=config.model or "",
        elapsed_seconds=0.0,
    )
    if provider_name in {"off", "disabled", "none", ""}:
        base_result.error = "AI provider 未启用；admin → AI 配置可开启"
        return base_result

    user_payload: dict[str, Any] = {
        "sql": sql[:2000],
        "plan": plan[:50],  # 大 plan 截断防超 token
        "rule_issues": issues,
        "rule_suggestions": suggestions,
        "expected_optimizations": list(expected_optimizations or []),
    }
    # plan 整体字符上限二次防护
    plan_json = json.dumps(user_payload["plan"], ensure_ascii=False)
    if len(plan_json) > max_plan_chars:
        user_payload["plan"] = user_payload["plan"][: max(1, len(user_payload["plan"]) // 2)]
        user_payload["plan_truncated"] = True

    try:
        raw = _call_ai(provider_name, config, SLOW_SQL_ENRICH_PROMPT, user_payload)
    except Exception as exc:
        logger.warning("slow_sql enrich failed: %s", exc)
        base_result.error = str(exc)
        base_result.elapsed_seconds = round(time.perf_counter() - started, 3)
        return base_result

    summary = str((raw or {}).get("summary") or "").strip()
    issue_review = _ensure_list_of_dict(raw.get("issue_review")) if isinstance(raw, dict) else []
    extra_suggestions = _ensure_list_of_dict(raw.get("extra_suggestions")) if isinstance(raw, dict) else []
    coverage = raw.get("expected_coverage") if isinstance(raw, dict) else {}
    expected_coverage = _normalize_coverage(coverage, expected_optimizations or [])

    return EnrichResult(
        ok=True,
        summary=summary,
        issue_review=issue_review,
        extra_suggestions=extra_suggestions,
        expected_coverage=expected_coverage,
        provider=provider_name,
        model=config.model or "",
        elapsed_seconds=round(time.perf_counter() - started, 3),
    )


def _ensure_list_of_dict(value: Any) -> list[dict[str, Any]]:
    """LLM 偶尔返字符串列表 / null / 单 dict，统一成 list[dict]，过滤非 dict 项。"""
    if not isinstance(value, list):
        return []
    return [v for v in value if isinstance(v, dict)]


def _normalize_coverage(
    raw_coverage: Any,
    expected: list[str],
) -> dict[str, Any]:
    """coverage 字段防御：LLM 可能漏字段 / 给非法 pct。"""
    if not isinstance(raw_coverage, dict):
        raw_coverage = {}
    matched_raw = raw_coverage.get("matched") if isinstance(raw_coverage.get("matched"), list) else []
    missing_raw = raw_coverage.get("missing") if isinstance(raw_coverage.get("missing"), list) else []
    matched = [str(x) for x in matched_raw]
    missing = [str(x) for x in missing_raw]
    pct_raw = raw_coverage.get("coverage_pct")
    try:
        pct = float(pct_raw) if pct_raw is not None else 0.0
    except (TypeError, ValueError):
        pct = 0.0
    # expected 非空 + 提供了 matched 但 LLM 漏 pct，按比例反算
    if expected and matched and pct == 0.0:
        pct = round(len(matched) / len(expected) * 100, 1)
    pct = max(0.0, min(100.0, pct))
    return {"matched": matched, "missing": missing, "coverage_pct": pct}
