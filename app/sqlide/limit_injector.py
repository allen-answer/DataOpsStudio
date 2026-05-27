"""LIMIT 注入器 —— Preview 路径强制给无 LIMIT 的 SELECT 加上限。

**为什么需要**:pymysql 默认 cursor 是 buffered(execute 那一刻把整个结果集
拉到 client 内存),`max_rows + 1` 截断**根本来不及触发**,大表 SELECT 直接
OOM。在 SQL 出门前注入 `LIMIT N` 让 server 端只返 N 行,client 内存恒定。

**只在 Preview 路径用**:Export / Compare 需要完整结果,不能注入 LIMIT(那样
导出的数据就不全了)—— 它们的 OOM 防护走 SSCursor 流式 fetch + writer 落盘。

**dialect-aware**:
- MySQL / OceanBase MySQL / PostgreSQL: `LIMIT N`(标准)
- Oracle / DM / OceanBase Oracle: 包子查询 `SELECT * FROM (...) WHERE ROWNUM <= N`
- DB2: `FETCH FIRST N ROWS ONLY`

**保守策略**:
- 已含 LIMIT / FETCH FIRST / ROWNUM 一律不动(尊重用户意图)
- 多语句 / 含分号尾 / WITH ... SELECT 都识别(WITH 头 + SELECT 体仍可注入)
- 注释里 "LIMIT" 不算(strip comments 后扫)
- 大小写不敏感
- 已 ORDER BY ... LIMIT 也跳过(典型场景)
"""
from __future__ import annotations

import re
from typing import Literal

# 检测已含上限子句的正则。注释已先 strip,所以可以裸扫。
# (?i) 大小写不敏感;\b 词边界防 "SLIMITED" 误判
_HAS_LIMIT = re.compile(r"(?i)\blimit\s+\d+", re.MULTILINE)
_HAS_FETCH_FIRST = re.compile(r"(?i)\bfetch\s+first\s+\d+\s+row", re.MULTILINE)
_HAS_ROWNUM = re.compile(r"(?i)\browumn\b|\brownum\s*<=?\s*\d+", re.MULTILINE)
# Oracle FETCH FIRST N ROWS ONLY 跟 DB2 同语法,都被 _HAS_FETCH_FIRST 覆盖
# SQL Server TOP 不在我们方言列表里,暂不处理


# MySQL family —— LIMIT 是标准 trailing 子句
_MYSQL_LIKE_DIALECTS = {"mysql", "ob_mysql", "oceanbase", "tidb", "doris", "postgres", "postgresql"}
# Oracle family —— ROWNUM 子查询 wrap
_ORACLE_LIKE_DIALECTS = {"oracle", "dm", "dameng", "ob_oracle"}
# DB2 —— FETCH FIRST
_DB2_LIKE_DIALECTS = {"db2"}


def _strip_comments(sql: str) -> str:
    """脱掉 /* */ 块注释 + -- 行注释,只用于"是否已含 LIMIT"扫描,不改原 SQL。"""
    sql = re.sub(r"/\*[\s\S]*?\*/", " ", sql)
    sql = re.sub(r"--[^\n]*", " ", sql)
    return sql


def has_explicit_limit(sql: str) -> bool:
    """SQL 是否已显式带上限子句(LIMIT / FETCH FIRST / ROWNUM)。"""
    cleaned = _strip_comments(sql)
    if _HAS_LIMIT.search(cleaned):
        return True
    if _HAS_FETCH_FIRST.search(cleaned):
        return True
    if _HAS_ROWNUM.search(cleaned):
        return True
    return False


def _normalize_dialect(db_type: str | None) -> Literal["mysql", "oracle", "db2"]:
    """把 db_type 归一到注入方言三档。未识别默认 mysql(LIMIT 是最通用方言)。"""
    if not db_type:
        return "mysql"
    name = db_type.strip().lower()
    if name in _ORACLE_LIKE_DIALECTS:
        return "oracle"
    if name in _DB2_LIKE_DIALECTS:
        return "db2"
    return "mysql"


def _strip_trailing_semicolon(sql: str) -> tuple[str, str]:
    """去掉末尾分号(避免 `SELECT ...;` + 注入 LIMIT 后变 `SELECT ...; LIMIT N`)。
    返回 (主体, 末尾分号片段);wrap 完后再拼回。
    """
    stripped = sql.rstrip()
    trailing = ""
    while stripped.endswith(";"):
        trailing = ";" + trailing
        stripped = stripped[:-1].rstrip()
    return stripped, trailing


def inject_limit(sql: str, *, max_rows: int, db_type: str | None = None) -> str:
    """给无 LIMIT 的 SELECT 注入 `LIMIT max_rows`。

    - SQL 已含 LIMIT / FETCH FIRST / ROWNUM → 原样返回
    - max_rows ≤ 0 → 原样返回(防呆)
    - dialect-aware:MySQL/PG/OB-MySQL → `LIMIT N`;Oracle/DM/OB-Oracle → ROWNUM wrap;DB2 → `FETCH FIRST N ROWS ONLY`
    """
    if max_rows <= 0:
        return sql
    if has_explicit_limit(sql):
        return sql

    dialect = _normalize_dialect(db_type)
    body, trailing_semi = _strip_trailing_semicolon(sql)

    if dialect == "oracle":
        # ROWNUM 子查询 wrap —— 不动原 SQL 内部 ORDER BY 语义
        # 注意:ROWNUM 在 WHERE 求值早于 ORDER BY,所以这种写法等价于 "先 ORDER 再截 N"
        # 标准 Oracle 12c+ 也支持 FETCH FIRST,但 ROWNUM wrap 兼容更老版本
        wrapped = f"SELECT * FROM (\n{body}\n) WHERE ROWNUM <= {max_rows}"
    elif dialect == "db2":
        wrapped = f"{body}\nFETCH FIRST {max_rows} ROWS ONLY"
    else:
        # mysql family —— 直接末尾追加 LIMIT
        wrapped = f"{body}\nLIMIT {max_rows}"

    return wrapped + trailing_semi


def needs_injection(sql: str, *, max_rows: int) -> bool:
    """便利函数:是否需要注入(给 caller log 用)。"""
    return max_rows > 0 and not has_explicit_limit(sql)
