"""SQL 工作台 v0.5 — 基础静态文本规则。

跟 `services/sql_preflight.py` 不同:
- preflight 是**对比任务**的"任务前体检"(行数估算、列检查、stream_compare ORDER BY)
- 这里是 SQL 工作台 explain panel 顺带的 4 个轻量规则,目标只是给写 SQL 的人
  做实时提醒,**不**拦截执行

跟 `services/slow_sql.py` 不同:
- slow_sql 是基于 EXPLAIN plan 的 issue 推断(driver 必须先跑通)
- 这里纯文本扫描,即使 unsupported 方言(Oracle/DM)也能给出提示

4 条规则:
1. select_star    — `SELECT *`(列爆炸 / I/O 浪费)
2. no_where       — `FROM ...` 后没有 `WHERE`(可能全表扫描)
3. leading_wildcard — `LIKE '%xxx'` / `LIKE '%xxx%'`(索引失效)
4. order_no_limit — `ORDER BY ...` 但没 `LIMIT` / `FETCH FIRST` / `ROWNUM`
                    (排序大表全量返回)

每条规则用正则识别,不上 sqlglot AST(规则简单 + 不想吃解析开销);
误报可接受,**只**作为提示。
"""
from __future__ import annotations

import re
from dataclasses import dataclass


_RE_SELECT_STAR = re.compile(r"\bSELECT\s+(?:DISTINCT\s+)?\*", re.IGNORECASE)
# COUNT(*) / SUM(*) 这种括号包的 * 不算 select 全表 —— 上面 regex 已经能区分
# (\bSELECT\s+\* 不会匹配 SELECT COUNT(*))

_RE_FROM = re.compile(r"\bFROM\b", re.IGNORECASE)
_RE_WHERE = re.compile(r"\bWHERE\b", re.IGNORECASE)

# LIKE '%xxx' / LIKE "%xxx" / LIKE :name(变量 placeholder)单引号双引号都覆盖
# 不匹配 LIKE 'xxx%'(尾通配符可以走 BTree 前缀,不是问题)
_RE_LEADING_WILDCARD = re.compile(
    r"LIKE\s+['\"]\s*%",
    re.IGNORECASE,
)

_RE_ORDER_BY = re.compile(r"\bORDER\s+BY\b", re.IGNORECASE)
_RE_LIMIT = re.compile(r"\bLIMIT\b", re.IGNORECASE)
_RE_FETCH = re.compile(r"\bFETCH\s+(?:FIRST|NEXT)\b", re.IGNORECASE)
_RE_ROWNUM = re.compile(r"\bROWNUM\b", re.IGNORECASE)
_RE_TOP = re.compile(r"\bTOP\s+\d+\b", re.IGNORECASE)


# 去 SQL 注释(line `--` 和 block `/* */`)和字符串字面量,避免误报
# (字符串里的 SELECT * 或注释里的 ORDER BY 不该触发规则)。
_RE_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_RE_LINE_COMMENT = re.compile(r"--[^\n]*")
# 简化:把字符串字面量替换成 "''",保留 LIKE '%x' 这种(因为 LIKE 检测要看
# 字符串内容)—— 所以只去注释,字符串保留
def _strip_comments(sql: str) -> str:
    sql = _RE_BLOCK_COMMENT.sub(" ", sql)
    sql = _RE_LINE_COMMENT.sub(" ", sql)
    return sql


@dataclass
class SqlHint:
    code: str
    severity: str   # "info" / "warning" / "error"
    message: str

    def to_dict(self) -> dict:
        return {"code": self.code, "severity": self.severity, "message": self.message}


def lint_sql(sql: str) -> list[dict]:
    """返回命中规则的 hint 列表(已 dict 化,API 直接 JSON 序列化)。
    空 SQL / 仅注释 → 返 []。"""
    if not sql or not sql.strip():
        return []
    cleaned = _strip_comments(sql)
    if not cleaned.strip():
        return []
    hints: list[SqlHint] = []

    if _RE_SELECT_STAR.search(cleaned):
        hints.append(SqlHint(
            code="select_star",
            severity="warning",
            message="`SELECT *` 会返回所有列,列多时 I/O 浪费;只 SELECT 真正用的列。",
        ))

    if _RE_FROM.search(cleaned) and not _RE_WHERE.search(cleaned):
        hints.append(SqlHint(
            code="no_where",
            severity="warning",
            message="缺少 WHERE 条件,可能全表扫描;大表上请补过滤或加 LIMIT。",
        ))

    if _RE_LEADING_WILDCARD.search(cleaned):
        hints.append(SqlHint(
            code="leading_wildcard",
            severity="warning",
            message="`LIKE '%xxx'` 前置通配符会让 BTree 索引失效;尝试改成尾通配符或全文索引。",
        ))

    if _RE_ORDER_BY.search(cleaned) and not (
        _RE_LIMIT.search(cleaned)
        or _RE_FETCH.search(cleaned)
        or _RE_ROWNUM.search(cleaned)
        or _RE_TOP.search(cleaned)
    ):
        hints.append(SqlHint(
            code="order_no_limit",
            severity="warning",
            message="`ORDER BY` 没配 LIMIT/FETCH/ROWNUM,大表上排序代价很高;补一个 LIMIT。",
        ))

    return [h.to_dict() for h in hints]
