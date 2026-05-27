"""SQL 日志脱敏工具 —— prod 模式日志只打 hash + 前 N 字符,不打完整 SQL 和业务参数。

**为什么需要**:生产 SQL 里常带业务参数(WHERE phone IN ('138...', ...) / WHERE
order_id = '20260527001'),完整 SQL + 参数一旦流到 ELK / Loki / 共享日志平台 =
敏感数据泄漏面。Phase 9 已有 `RedactingFilter` 兜底脱敏 password/token/JWT,但
不管业务参数 —— 那些不是技术凭证但是数据隐私。

**dev 模式**:为了 debug 方便,完整 SQL 仍打到日志(本地 ./logs/app.log,不出环境)。

**prod 模式**:
- query start / done / error 日志只打 sql_hash + sql_length + 前 80 字符
- 完整 SQL 仅 DEBUG 级别(默认不开,需要时手动调 logger 级别才看得到)
- IN 子句 / VALUES 子句字面值替换成 `?` (用 SQL "fingerprint"思路)

**hash 用途**:
- 不同 user 跑同一 SQL 模板可关联(相同 hash) → 慢查询统计 / 缓存命中分析
- 跨日志聚合"哪个 SQL 失败最多"而无需暴露字面值
"""
from __future__ import annotations

import hashlib
import os
import re


# IN 子句字面值脱敏:`IN ('a', 'b', 1, 2)` → `IN (?)`
_RE_IN_VALUES = re.compile(r"(?i)\bin\s*\(\s*[^)]*\)")
# VALUES 字面值脱敏:`VALUES ('a', 1)` → `VALUES (?)`(虽然 SELECT 不该有,导出 SQL Insert 才用)
_RE_VALUES = re.compile(r"(?i)\bvalues\s*\(\s*[^)]*\)")
# 字符串字面值脱敏(prod 模式):`= 'abc'` → `= ?`,`= "xyz"` → `= ?`
_RE_STRING_LIT = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"")
# 数字字面值脱敏(prod 模式):`= 123` → `= ?`(只在 = / > / < / <> 后面命中,避免误伤列名)
_RE_NUM_LIT = re.compile(r"(?<=[=<>!])\s*-?\d+(?:\.\d+)?")


def sql_fingerprint(sql: str) -> str:
    """生成稳定 SQL hash —— 同 SQL 同 hash,跨日志聚合用。

    步骤:
    1. lowercase + 折叠空白
    2. 字面值替换成 `?`(IN / VALUES / 字符串 / 数字)
    3. SHA-256 → 取前 12 hex(约 48 bit,千万级 SQL 模板碰撞概率仍极低)
    """
    normalized = _normalize_for_hash(sql)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


def _normalize_for_hash(sql: str) -> str:
    text = sql.lower()
    text = _RE_STRING_LIT.sub("?", text)
    text = _RE_NUM_LIT.sub(" ?", text)
    text = _RE_IN_VALUES.sub("in (?)", text)
    text = _RE_VALUES.sub("values (?)", text)
    # 折叠所有空白(换行 / tab / 连续空格)
    text = " ".join(text.split())
    return text


def is_prod_mode() -> bool:
    """复用 services/auth.py 的判定逻辑。lazy import 避免循环依赖。"""
    return os.getenv("DATAOPS_ENV", "").strip().lower() in {"prod", "production"}


def sanitize_sql_for_log(
    sql: str,
    *,
    max_chars: int = 80,
    force_redact: bool | None = None,
) -> str:
    """给日志用的 SQL 字符串。

    - dev 模式:原样返回(但仍折叠空白让单行日志清爽)
    - prod 模式:字面值脱敏(IN/VALUES/'str'/数字 → ?)+ 截到 max_chars

    Args:
        sql: 原始 SQL
        max_chars: prod 模式下截断长度,默认 80
        force_redact: 显式覆盖 prod 检测。True/False 强制开关,None 走 env 判定。
            测试 / 特殊场景用。
    """
    if not sql:
        return ""

    compact = " ".join(sql.split())

    redact = force_redact if force_redact is not None else is_prod_mode()
    if not redact:
        # dev 模式:保留原样,只折叠空白 + 截到 500(防止单行日志几 MB)
        return compact[:500] + ("..." if len(compact) > 500 else "")

    # prod 模式:字面值脱敏 + 短截
    text = compact
    text = _RE_STRING_LIT.sub("?", text)
    text = _RE_IN_VALUES.sub("IN (?)", text)
    text = _RE_VALUES.sub("VALUES (?)", text)
    text = _RE_NUM_LIT.sub(" ?", text)

    if len(text) > max_chars:
        return text[:max_chars] + "..."
    return text


def format_sql_for_log(sql: str) -> dict[str, str | int]:
    """给结构化日志(extra=...)用的 dict —— 一次性给 sql_hash + sql_preview + sql_length。

    用法:
        logger.info("query start", extra=format_sql_for_log(sql))
        # → JSON 日志多出 sql_hash / sql_preview / sql_length 字段
    """
    return {
        "sql_hash": sql_fingerprint(sql),
        "sql_preview": sanitize_sql_for_log(sql),
        "sql_length": len(sql),
    }
