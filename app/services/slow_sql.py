"""Slow SQL analysis —— EXPLAIN + 规则推断 issues + 优化建议（Phase 12 切片 6 + 16）。

支持的 dialect：
- mysql        EXPLAIN <sql>，解析 type / Extra / rows
- oracle / dm  EXPLAIN PLAN FOR <sql> → SELECT FROM PLAN_TABLE，解析
               operation / options / object_name / cardinality / cost

MySQL 4 条规则（切片 6）：
- type=ALL                    → 全表扫描，建议加索引
- Extra 含 filesort           → ORDER BY 没用上索引
- Extra 含 Using temporary    → GROUP BY / DISTINCT 触发临时表
- rows > 10000 且 type=all/idx → 高扫描行数没走 key

Oracle 5 条规则（切片 16）：
- TABLE ACCESS / FULL                                 → 全表扫描
- SORT / ORDER BY                                     → 未走索引排序
- SORT / GROUP BY|UNIQUE                              → GROUP/DISTINCT 走临时排序
- NESTED LOOPS + cardinality > 10000                  → 大数据集 NL（应改 HASH JOIN）
- cost > 1000 任意非 SELECT STATEMENT 步骤            → 高 cost 提示统计信息 / hint
- cardinality > 100000 + TABLE ACCESS FULL            → 高扫描（与 full_table_scan 同 row 触发）

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


SUPPORTED_DIALECTS = {"mysql", "oracle", "dm"}


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
    if dialect not in SUPPORTED_DIALECTS:
        raise SlowSqlError(
            f"slow-sql analyze 暂支持 mysql / oracle / dm；got {source.db_type.value}"
        )
    if dialect == "mysql":
        return _analyze_mysql(source, sql, max_plan_rows)
    # oracle / dm 共用一套 plan_table 协议
    return _analyze_oracle(source, sql, max_plan_rows)


def _analyze_mysql(source: Any, sql: str, max_plan_rows: int) -> dict[str, Any]:
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


def _analyze_oracle(source: Any, sql: str, max_plan_rows: int) -> dict[str, Any]:
    inner = sql.rstrip().rstrip(";").strip()
    explain_sql = f"EXPLAIN PLAN FOR {inner}"
    try:
        rows = _fetch_oracle_plan(source, inner, max_plan_rows)
    except Exception as exc:
        raise SlowSqlError(f"EXPLAIN failed: {exc}") from exc
    issues = detect_oracle_issues(rows)
    suggestions = build_oracle_suggestions(issues)
    return {
        # 注：DM 时这里仍标 oracle —— UI 显示 dialect 时如需区分，看 source.db_type
        "dialect": "oracle",
        "explain_sql": explain_sql,
        "plan": rows,
        "issues": [asdict(i) for i in issues],
        "suggestions": [asdict(s) for s in suggestions],
    }


def _fetch_oracle_plan(source: Any, sql: str, max_rows: int) -> list[dict[str, Any]]:
    """跑 EXPLAIN PLAN + SELECT FROM PLAN_TABLE。两步合并到一个连接里，commit
    后释放回池。statement_id 用 uuid 防多用户并发污染。

    Why 不用 DBMS_XPLAN.DISPLAY 文本输出：那玩意是 hierarchical 文本，列宽
    不一，解析正则脆；直接读 PLAN_TABLE 结构化字段稳定得多。
    """
    import uuid

    from app.dbclients import pool as _pool
    from app.dbclients.drivers import first_available_module
    from app.dbclients.factory import _connect

    module_name = first_available_module(source.db_type)
    if not module_name:
        raise SlowSqlError(f"{source.db_type.value} driver is not installed")

    stmt_id = f"dataops_{uuid.uuid4().hex[:8]}"
    cap = max(1, max_rows)
    with _pool.borrow(source, lambda: _connect(source, module_name)) as conn:
        cur = conn.cursor()
        try:
            # 防御性清理：上一次同 stmt_id 残留（uuid 几乎不冲突，保险起见）
            try:
                cur.execute(f"DELETE FROM PLAN_TABLE WHERE STATEMENT_ID = '{stmt_id}'")
            except Exception:
                pass  # PLAN_TABLE 不存在等错误不致命，继续往下
            cur.execute(f"EXPLAIN PLAN SET STATEMENT_ID = '{stmt_id}' FOR {sql}")
            cur.execute(
                "SELECT id, operation, options, object_name, cardinality, cost, bytes, "
                "depth, parent_id FROM PLAN_TABLE "
                f"WHERE STATEMENT_ID = '{stmt_id}' ORDER BY id"
            )
            cols = [d[0].lower() for d in cur.description]
            raw_rows = cur.fetchmany(cap)
            try:
                conn.commit()
            except Exception:
                pass
            return [dict(zip(cols, r)) for r in raw_rows]
        finally:
            try:
                cur.close()
            except Exception:
                pass


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


# ─── Oracle rules（切片 16） ────────────────────────────────────────────────


def detect_oracle_issues(plan_rows: list[dict[str, Any]]) -> list[Issue]:
    """对每条 PLAN_TABLE row 匹配 6 条 Oracle 规则。同 row 多 issue 不去重。

    PLAN_TABLE 字段（lower-cased）：id, operation, options, object_name,
    cardinality, cost, bytes, depth, parent_id
    """
    issues: list[Issue] = []
    for row in plan_rows:
        op = _norm_oracle_text(row.get("operation"))
        opt = _norm_oracle_text(row.get("options"))
        table = str(row.get("object_name") or "")
        card = _to_int(row.get("cardinality"))
        cost = _to_int(row.get("cost"))

        is_full_scan = op == "TABLE ACCESS" and opt == "FULL"
        if is_full_scan:
            issues.append(Issue(
                severity="warning",
                code="full_table_scan",
                message=f"{table or '<unknown>'} 走全表扫描（TABLE ACCESS FULL）",
                table=table,
                detail=f"cardinality≈{card}; cost={cost}",
            ))
        if op == "SORT" and "ORDER" in opt:
            issues.append(Issue(
                severity="warning",
                code="sort_order_by",
                message=f"{table or '<unknown>'} ORDER BY 触发 SORT（未走索引）",
                table=table,
                detail=f"options={opt}",
            ))
        if op == "SORT" and ("GROUP" in opt or "UNIQUE" in opt):
            issues.append(Issue(
                severity="warning",
                code="sort_group_by",
                message=f"GROUP BY / DISTINCT 触发 SORT（{opt}）",
                table=table,
                detail=f"options={opt}",
            ))
        if op == "NESTED LOOPS" and card > 10000:
            issues.append(Issue(
                severity="warning",
                code="nested_loops_high_card",
                message=f"NESTED LOOPS 在大数据集上效率低（cardinality≈{card}）",
                table=table,
                detail="可考虑改 HASH JOIN",
            ))
        if cost > 1000 and op not in ("SELECT STATEMENT", ""):
            issues.append(Issue(
                severity="info",
                code="high_cost",
                message=f"{op} {opt} 步骤 cost={cost} 偏高",
                table=table,
                detail=f"op={op} {opt}",
            ))
        if is_full_scan and card > 100000:
            issues.append(Issue(
                severity="warning",
                code="high_row_scan",
                message=f"{table or '<unknown>'} 扫描行数偏高（{card}）",
                table=table,
                detail=f"op={op} {opt}",
            ))
    return issues


def build_oracle_suggestions(issues: list[Issue]) -> list[Suggestion]:
    """Oracle 建议派生，同类按 table（或全局）去重。"""
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
                message=f"考虑在 {issue.table or '该表'} 的 WHERE / JOIN 列上加索引消除 TABLE ACCESS FULL",
            ))
        elif issue.code == "sort_order_by":
            tag = f"order_index:{issue.table}"
            if tag in seen:
                continue
            seen.add(tag)
            out.append(Suggestion(
                code="order_by_index",
                message=f"为 {issue.table or '该表'} 的 ORDER BY 列建索引可避免 SORT 步骤",
            ))
        elif issue.code == "sort_group_by":
            if "group_index" in seen:
                continue
            seen.add("group_index")
            out.append(Suggestion(
                code="group_by_index",
                message="GROUP BY / DISTINCT 列上建索引可消除 SORT GROUP BY / UNIQUE",
            ))
        elif issue.code == "nested_loops_high_card":
            if "force_hash_join" in seen:
                continue
            seen.add("force_hash_join")
            out.append(Suggestion(
                code="force_hash_join",
                message="在大数据集上 HASH JOIN 通常优于 NESTED LOOPS；可加 /*+ USE_HASH */ hint 或 ANALYZE 表",
            ))
        elif issue.code == "high_cost":
            if "review_cost" in seen:
                continue
            seen.add("review_cost")
            out.append(Suggestion(
                code="review_cost",
                message="Plan 中有高 cost 步骤（cost>1000），检查 ANALYZE 统计信息是否过时、考虑 hint 强制访问路径",
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


def _norm_oracle_text(value: Any) -> str:
    return str(value or "").strip().upper()


def _to_int(value: Any) -> int:
    try:
        return int(value) if value is not None else 0
    except (TypeError, ValueError):
        return 0


# ─── AI enrichment（Phase 12 切片 8） ────────────────────────────────────────


SLOW_SQL_ENRICH_PROMPT = (
    "你是关系型数据库慢 SQL 优化助手。用户的 dialect 字段会指明是 mysql / oracle / dm，\n"
    "请按对应方言的执行计划语义解读。Oracle / DM 的 PLAN_TABLE 字段是 operation /\n"
    "options / object_name / cardinality / cost；MySQL 的 EXPLAIN 字段是 type /\n"
    "Extra / rows / table。\n\n"
    "用户已经跑过 EXPLAIN 拿到 plan，并用规则推断了一组\n"
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
    dialect: str = "mysql",
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
        "dialect": (dialect or "mysql").lower(),
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
