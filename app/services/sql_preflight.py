"""sql_preflight —— 对比 SQL 的运行前静态体检（安全加固方案 P0 #2）。

跟 resource_guard 互补：resource_guard 看任务形状（max_rows / 格式 / 并发），
sql_preflight 看 SQL 文本本身。三级静态检查，不连库：

1. read-only guard 复检（复用 utils.sql_guard）—— 非单条 SELECT/含副作用 → block
2. sqlglot AST 解析 —— 解析失败保守按 warn 处理，不静默放行
3. AST 规则引擎 —— SELECT * / 无 WHERE / 流式无序 / 宽表 / 高成本算子等

规则级别 info / warn / block。`blocking = 有任一 block`。本模块只产决策，
端点 `POST /api/sql/preflight` 把它当 advisory 返回；是否在 run 时强制是
后续切片的事。规则表见 docs/SQL_PREFLIGHT.md。
"""
from __future__ import annotations

from typing import Literal

import sqlglot
from sqlglot import exp
from pydantic import BaseModel, Field

from app.lineage.dialects import resolve_dialect
from app.utils.sql_guard import validate_readonly_sql


Level = Literal["info", "warn", "block"]
RiskLevel = Literal["low", "medium", "high", "critical"]
Mode = Literal["preview", "compare"]

# 「大任务」阈值：超过它时 SELECT * / 无 WHERE 从 warn 升级成 block。
_LARGE_TASK_ROWS = 1_000_000
# 选择列数超过它给 warn —— 提示裁剪比对字段。
_WIDE_SELECT = 50


class PreflightRuleResult(BaseModel):
    code: str
    level: Level
    message: str
    suggestion: str | None = None


class SQLPreflightDecision(BaseModel):
    dialect: str
    risk_level: RiskLevel
    blocking: bool
    rules: list[PreflightRuleResult] = Field(default_factory=list)
    normalized_sql: str | None = None
    # EXPLAIN Broker 留给后续切片；现恒 False。
    explain_used: bool = False


def assess_sql(
    *,
    sql: str,
    dialect: str = "",
    key_columns: list[str] | None = None,
    mode: Mode = "compare",
    max_rows: int = 100_000,
    stream_compare: bool = False,
) -> SQLPreflightDecision:
    """对一段对比 SQL 做静态体检，返回 SQLPreflightDecision。

    纯函数、不连库。`key_columns` / `stream_compare` 只在 mode="compare" 下
    参与流式有序性检查。
    """
    key_columns = key_columns or []
    rules: list[PreflightRuleResult] = []

    # ── 1. read-only guard 复检 ──
    try:
        validate_readonly_sql(sql)
    except ValueError as exc:
        rules.append(PreflightRuleResult(
            code="not_readonly", level="block",
            message=f"SQL 未通过只读校验：{exc}",
            suggestion="只允许单条 SELECT / WITH 查询，不能含 DML/DDL/多语句",
        ))
        return _decide(dialect, rules, None)

    # ── 2. sqlglot AST 解析 ──
    resolved = resolve_dialect(dialect)
    parsed: exp.Expression | None = None
    try:
        parsed = sqlglot.parse_one(sql, read=resolved or None)
    except Exception as exc:  # noqa: BLE001 —— sqlglot 抛多种异常，统一保守处理
        rules.append(PreflightRuleResult(
            code="parse_failed", level="warn",
            message=f"SQL 解析失败，保守按风险处理：{exc}",
            suggestion="检查方言是否选对；解析不了的 SQL 无法做静态体检",
        ))
        return _decide(dialect, rules, None)
    if parsed is None:
        rules.append(PreflightRuleResult(
            code="parse_failed", level="warn",
            message="SQL 解析结果为空，无法做静态体检",
        ))
        return _decide(dialect, rules, None)

    large = max_rows > _LARGE_TASK_ROWS

    # ── 3. AST 规则 ──
    if list(parsed.find_all(exp.Star)):
        rules.append(PreflightRuleResult(
            code="select_star",
            level="block" if large else "warn",
            message="SELECT * —— 列宽与传输成本不可控" + ("（大任务下禁止）" if large else ""),
            suggestion="显式列出要对比的字段",
        ))

    if parsed.find(exp.Where) is None:
        rules.append(PreflightRuleResult(
            code="no_where",
            level="block" if large else "warn",
            message="查询没有 WHERE 过滤，极易触发全表扫描" + ("（大任务下禁止）" if large else ""),
            suggestion="加 WHERE 限定数据范围（按日期 / 分区等）",
        ))

    order = parsed.find(exp.Order)
    if stream_compare and mode == "compare":
        if order is None:
            rules.append(PreflightRuleResult(
                code="stream_no_order", level="block",
                message="stream_compare 要求两端结果按主键有序，但 SQL 没有 ORDER BY",
                suggestion="加 ORDER BY <主键列>，否则流式归并结果不可信",
            ))
        elif key_columns and not _order_covers_keys(order, key_columns):
            rules.append(PreflightRuleResult(
                code="order_missing_keys", level="block",
                message=f"ORDER BY 未以主键 {key_columns} 为前缀，流式归并会错配",
                suggestion=f"ORDER BY 前缀必须依次是 {', '.join(key_columns)}",
            ))

    select_count = _select_column_count(parsed)
    if select_count > _WIDE_SELECT:
        rules.append(PreflightRuleResult(
            code="wide_select", level="warn",
            message=f"选择了 {select_count} 列，超过 {_WIDE_SELECT}，对比成本高",
            suggestion="裁剪到真正需要比对的字段",
        ))

    expensive = _expensive_ops(parsed)
    if expensive:
        rules.append(PreflightRuleResult(
            code="expensive_ops", level="warn",
            message="查询含高成本算子：" + " / ".join(expensive),
            suggestion="确认这些算子必要；聚合 / 窗口结果不能按行级 diff",
        ))

    if order is not None and _order_has_func(order):
        rules.append(PreflightRuleResult(
            code="order_func_wrapped", level="warn",
            message="ORDER BY 列被函数包裹，可能用不上索引、排序变慢",
            suggestion="排序列尽量用裸列，让数据库走索引",
        ))

    return _decide(dialect, rules, parsed)


# 估算 SQL 返回行数超过 `max_rows × _EXPLAIN_OVERFLOW_MULT` 时加 warn finding —— 阈值含义:
# "比 max_rows 大一个数量级"。MySQL EXPLAIN `rows` 列是估算,不精确,所以宁可松一点
# 也别给正常查询误报;真危险的全表扫描会比 max_rows 高 100×~1000× 远超阈值。
_EXPLAIN_OVERFLOW_MULT = 10


def assess_with_explain(
    *,
    sql: str,
    dialect_name: str,
    conn,
    key_columns: list[str] | None = None,
    mode: Mode = "compare",
    max_rows: int = 100_000,
    stream_compare: bool = False,
) -> SQLPreflightDecision:
    """先静态体检,再(若不阻塞)走 dialect.estimate_rows_from_explain 看 plan 估算。

    估算 > `max_rows × 10` 加 warn finding(code=`explain_rows_high`)。EXPLAIN
    本身失败 / 方言不支持 / 返 None 都安全降级(decision.explain_used=False),
    **不阻塞**真任务。

    `conn` 是 driver 裸连接(`pool.borrow` 给的同款),caller 自管 release —— 本
    函数只借用 cursor 不动 conn 生命周期。`dialect_name` 是 DatabaseType.value,
    取 dialect 走 `get_dialect(DatabaseType(dialect_name))`。
    """
    decision = assess_sql(
        sql=sql,
        dialect=dialect_name,
        key_columns=key_columns,
        mode=mode,
        max_rows=max_rows,
        stream_compare=stream_compare,
    )
    if decision.blocking:
        # 静态已 block,不必再请 DB 跑 EXPLAIN
        return decision
    try:
        from app.dbclients.dialects import get_dialect
        from app.models import DatabaseType
        # DatabaseType.value 用混合大小写("MySQL"/"Oracle"/"DM"/"DB2"),
        # 但调用方常传小写("mysql")。case-insensitive 反查避免 ValueError。
        db_type = next(
            (d for d in DatabaseType if d.value.lower() == dialect_name.lower()),
            None,
        )
        if db_type is None:
            return decision
        dialect = get_dialect(db_type)
    except (ValueError, KeyError, AttributeError):
        return decision
    try:
        estimated = dialect.estimate_rows_from_explain(conn, sql)
    except Exception:
        # caller 仍想用 decision —— EXPLAIN 失败不该让 preflight 整个崩
        return decision
    if estimated is None:
        return decision
    decision = decision.model_copy(update={"explain_used": True})
    threshold = max_rows * _EXPLAIN_OVERFLOW_MULT
    if estimated > threshold:
        new_rule = PreflightRuleResult(
            code="explain_rows_high",
            level="warn",
            message=(
                f"EXPLAIN 估算返回 {estimated:,} 行,超 max_rows × {_EXPLAIN_OVERFLOW_MULT} "
                f"({threshold:,})"
            ),
            suggestion="加更严格 WHERE / 走分区裁剪,或上调 max_rows 已知大任务",
        )
        # 静态规则在前,EXPLAIN 在后(让前端按出现顺序展示)
        new_rules = list(decision.rules) + [new_rule]
        risk = "medium" if decision.risk_level == "low" else decision.risk_level
        decision = decision.model_copy(update={"rules": new_rules, "risk_level": risk})
    return decision


# ─── helpers ────────────────────────────────────────────────────────────────


def _ordered_column_names(order: exp.Order) -> list[str]:
    """ORDER BY 各项的列名（小写）。非裸列项产出空串占位，保持位置对齐。"""
    names: list[str] = []
    for item in order.expressions:
        target = item.this if isinstance(item, exp.Ordered) else item
        if isinstance(target, exp.Column):
            names.append((target.name or "").lower())
        else:
            names.append("")
    return names


def _order_covers_keys(order: exp.Order, key_columns: list[str]) -> bool:
    """ORDER BY 是否以 key_columns 为严格前缀（同序、同名）。"""
    ordered = _ordered_column_names(order)
    keys = [k.strip().lower() for k in key_columns if k.strip()]
    if len(ordered) < len(keys):
        return False
    return ordered[: len(keys)] == keys


def _order_has_func(order: exp.Order) -> bool:
    """ORDER BY 是否有项不是裸列（被函数 / 表达式包裹）。"""
    for item in order.expressions:
        target = item.this if isinstance(item, exp.Ordered) else item
        if not isinstance(target, exp.Column):
            return True
    return False


def _select_column_count(parsed: exp.Expression) -> int:
    """首个 SELECT 的投影列数。SELECT * 记为 1，不会误报宽表。"""
    select = parsed if isinstance(parsed, exp.Select) else parsed.find(exp.Select)
    if select is None:
        return 0
    return len(select.expressions)


def _expensive_ops(parsed: exp.Expression) -> list[str]:
    """检出高成本算子。"""
    found: list[str] = []
    if parsed.find(exp.Distinct) is not None:
        found.append("DISTINCT")
    if parsed.find(exp.Group) is not None:
        found.append("GROUP BY")
    if parsed.find(exp.Window) is not None:
        found.append("WINDOW")
    if isinstance(parsed, exp.Union) or parsed.find(exp.Union) is not None:
        found.append("UNION")
    return found


def _decide(
    dialect: str,
    rules: list[PreflightRuleResult],
    parsed: exp.Expression | None,
) -> SQLPreflightDecision:
    blocking = any(r.level == "block" for r in rules)
    if blocking:
        critical = any(r.code in ("stream_no_order", "order_missing_keys") for r in rules)
        risk: RiskLevel = "critical" if critical else "high"
    elif any(r.level == "warn" for r in rules):
        risk = "medium"
    else:
        risk = "low"
    normalized: str | None = None
    if parsed is not None:
        try:
            normalized = parsed.sql()
        except Exception:  # noqa: BLE001
            normalized = None
    return SQLPreflightDecision(
        dialect=dialect or "auto",
        risk_level=risk,
        blocking=blocking,
        rules=rules,
        normalized_sql=normalized,
    )
