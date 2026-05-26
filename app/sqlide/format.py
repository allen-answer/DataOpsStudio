"""SQL 格式化 —— sqlglot.transpile(pretty=True),dialect 复用 lineage 别名表。

设计:
- 不强制 SELECT/WITH:用户可能粘任意 SQL 进来按"格式化"看清结构,格式化本身不
  执行,纯字符串变换,安全
- 失败时不修改原 SQL,前端拿到 success=False + error 即可恢复
- 方言映射跟 lineage.dialects.resolve_dialect 完全一致(dm/dameng → oracle,
  ob_mysql/oceanbase → mysql,ob_oracle → oracle)
"""
from __future__ import annotations

from dataclasses import dataclass

from app.lineage.dialects import resolve_dialect


@dataclass
class FormatResult:
    success: bool
    formatted_sql: str = ""
    dialect: str = ""
    error: str | None = None


def format_sql(sql: str, db_type: str = "") -> FormatResult:
    """Pretty-print SQL via sqlglot。

    db_type 接 datasource.db_type.value(MySQL / Oracle / DM / DB2 / OceanBase...);
    resolve_dialect 把它归一化成 sqlglot 认识的 dialect。
    """
    if not sql or not sql.strip():
        return FormatResult(success=False, error="SQL 为空")

    dialect = resolve_dialect((db_type or "").lower()) or "mysql"
    try:
        import sqlglot
        out = sqlglot.transpile(sql, read=dialect, write=dialect, pretty=True)
    except Exception as exc:  # 含 ParseError + 任何 sqlglot 内部异常
        return FormatResult(success=False, dialect=dialect, error=f"格式化失败: {exc}")

    if not out:
        return FormatResult(success=False, dialect=dialect, error="格式化返空(SQL 解析失败)")

    # transpile 多语句时返多个;v0.1 sql_guard 已经拦多语句执行,但格式化允许
    # 多语句(用户可能粘整个文件进来),用换行拼接
    formatted = "\n\n".join(out).strip()
    if not formatted:
        return FormatResult(success=False, dialect=dialect, error="格式化返空")
    return FormatResult(success=True, formatted_sql=formatted, dialect=dialect)
