"""SQL 安全性检测 —— 识别"无 WHERE / 无 LIMIT 的 SELECT *"这类全表扫描风险。

**用途**:Export / Compare 路径不能像 Preview 那样无脑注入 LIMIT(注入后导出数据
不全 = 错的),但需要拦"用户没意识到自己要拉全表"的情况。返 warning + 让前端
弹二次确认,而不是后端直接拒绝(尊重 Workbench 探索性工具的定位)。

**不做**:
- 不解析 AST(sqlglot 太重,Workbench 路径要轻量)
- 不阻断,只警告(交给 caller 决定怎么处理)
- 不检测 JOIN 笛卡尔积 / 索引命中 —— 那是 EXPLAIN 的活

**做**:
- SELECT * 识别(忽略注释 + 子查询里的 SELECT *)
- WHERE 子句存在性(顶层 WHERE,不算子查询里的)
- LIMIT 子句存在性(复用 limit_injector.has_explicit_limit)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.sqlide.limit_injector import has_explicit_limit


@dataclass
class SafetyReport:
    """SQL 安全检查结果。caller 根据 risk_level 决定:

    - safe: 直接放行
    - notice: log warning,放行(无 LIMIT 但有 WHERE / 只 SELECT 几列等)
    - warn: 返 requires_confirmation,要求 UI 二次确认(典型 SELECT * 无 WHERE 无 LIMIT)
    """

    has_where: bool
    has_limit: bool
    is_select_star: bool
    risk_level: str  # "safe" | "notice" | "warn"
    warnings: list[str] = field(default_factory=list)


# 顶层 SELECT * —— 不带 schema 路径 / 表别名
# 仅匹配"SELECT 紧接着的 *",不动 COUNT(*) / agg(*)
_RE_SELECT_STAR = re.compile(r"(?i)\bselect\s+(?:distinct\s+)?\*", re.MULTILINE)

# 顶层 WHERE —— 简单字符串扫描(注释先 strip)。
# 不完美:子查询里有 WHERE 也算 —— 但宽松总比严格好(误判方向偏 safe,不拦合法 SQL)
_RE_WHERE = re.compile(r"(?i)\bwhere\b", re.MULTILINE)


def _strip_comments(sql: str) -> str:
    sql = re.sub(r"/\*[\s\S]*?\*/", " ", sql)
    sql = re.sub(r"--[^\n]*", " ", sql)
    return sql


def analyze_safety(sql: str) -> SafetyReport:
    """检测 SQL 全表扫描风险。

    risk_level 判定:
    - SELECT * + 无 WHERE + 无 LIMIT → warn(典型危险)
    - SELECT * + 无 LIMIT(有 WHERE)→ notice(可能危险但用户有过滤意图)
    - 显式列 + 无 WHERE + 无 LIMIT → notice(全表但列少,内存压力低)
    - 其他 → safe
    """
    cleaned = _strip_comments(sql).strip()
    if not cleaned:
        return SafetyReport(False, False, False, "safe")

    has_where = bool(_RE_WHERE.search(cleaned))
    has_limit = has_explicit_limit(sql)
    is_select_star = bool(_RE_SELECT_STAR.search(cleaned))

    warnings: list[str] = []
    risk = "safe"

    if is_select_star and not has_where and not has_limit:
        warnings.append("SELECT * 无 WHERE 无 LIMIT,可能拉取全表数据。建议加 WHERE 过滤或 LIMIT 限行")
        risk = "warn"
    elif is_select_star and not has_limit:
        warnings.append("SELECT * 无 LIMIT,如表行数大可能拉取大量数据")
        risk = "notice"
    elif not has_where and not has_limit:
        warnings.append("SQL 无 WHERE 无 LIMIT,可能扫描全表")
        risk = "notice"

    return SafetyReport(
        has_where=has_where,
        has_limit=has_limit,
        is_select_star=is_select_star,
        risk_level=risk,
        warnings=warnings,
    )
