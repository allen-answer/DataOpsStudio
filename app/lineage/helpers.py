"""血缘分析的小工具：sqlglot 子模块懒加载、SQL 文本提取、statement 去重 /
分解、表达式分类、变量识别。这些函数没业务语义，纯辅助。"""
from __future__ import annotations

from typing import Any

from app.lineage._common import normalize_table_name as _normalize_table_name
from app.lineage._common import unique_strings as _unique_strings
from app.lineage.variables import variable_names as _variable_names


def exp():
    """懒加载 sqlglot.exp 模块。让所有用到 sqlglot 类型常量的函数都从这一处
    拿，避免散落顶层 import 触发 sqlglot 没装时的 ImportError。"""
    from sqlglot import exp as _exp

    return _exp


def sql(expression: Any | None) -> str:
    """把 AST 节点 .sql() 出来。某些 segment（动态 SQL 的字面量段）会挂
    `_lineage_original_sql` 属性保留原始文本，优先取它避免 sqlglot 重写丢信息。"""
    if expression is None:
        return ""
    original_sql = getattr(expression, "_lineage_original_sql", "")
    if original_sql:
        return original_sql
    try:
        from sqlglot import ErrorLevel

        return expression.sql(unsupported_level=ErrorLevel.IGNORE)
    except TypeError:
        return expression.sql()


def unique_parsed_statements(statements: list[Any]) -> list[Any]:
    """按 .sql() 文本去重 parsed statement —— 主语法块和 segment-based 重解析
    经常产生两个语义相同的 AST 节点。"""
    result: list[Any] = []
    seen: set[str] = set()
    for statement in statements:
        key = sql(statement) if statement is not None else ""
        if key in seen:
            continue
        seen.add(key)
        result.append(statement)
    return result


def unique_analysis_statements(statements: list[Any]) -> list[Any]:
    """按规范化的 SQL 去重分析层 statement。

    一个 PROCEDURE 块在顶层和它的 procedure_segments 重解析会产出两个
    AST 实例，但语义相同。比较小写 + 单空格压缩后的 sqlglot 输出。
    """
    result: list[Any] = []
    seen: set[str] = set()
    for statement in statements:
        if statement is None:
            continue
        canonical = " ".join(sql(statement).lower().split())
        if canonical in seen:
            continue
        seen.add(canonical)
        result.append(statement)
    return result


def analysis_statements(statement: Any) -> list[Any]:
    """从 parsed statement 抽出可分析的 DML / DQL 子节点。

    CREATE TABLE 包 INSERT 时返回内部 INSERTs；CREATE ... AS SELECT 整个 CREATE
    本身就是分析单元；普通 INSERT/SELECT/UNION/UPDATE/MERGE 直接返回。
    """
    e = exp()
    if isinstance(statement, e.Create):
        nested = [item for item in statement.find_all(e.Insert)]
        if nested:
            return nested
        if isinstance(statement.args.get("expression"), (e.Select, e.Union)):
            return [statement]
    if isinstance(statement, (e.Insert, e.Select, e.Union, e.Update, e.Merge, e.Delete)):
        return [statement]
    return []


def transform_type(expression: Any) -> str:
    """字段表达式分类：聚合 / 直接映射 / 表达式（含函数 / 计算）。"""
    e = exp()
    if any(True for _ in expression.find_all(e.AggFunc)):
        return "聚合"
    if isinstance(expression, e.Column):
        return "直接映射"
    if isinstance(expression, e.Alias) and isinstance(expression.this, e.Column):
        return "直接映射"
    return "表达式"


def variables_in_expression(expression: Any, script_variables: list[dict[str, str]]) -> list[str]:
    """识别一段表达式里引用的脚本变量。考虑两种引用形式：
    1. SQL 文本里 `${var}` / `:var` —— variable_names() 文本扫
    2. 已知变量名以 Column 形式出现（DBMS 把 `@var` 解析成 Column）
    """
    e = exp()
    sql_text = sql(expression)
    known = {item["name"] for item in script_variables}
    names = [variable for variable in _variable_names(sql_text) if not known or variable in known]
    if known:
        names.extend(column.name for column in expression.find_all(e.Column) if column.name in known)
    return _unique_strings(names)


def normalize_schema(schema: dict[str, list[str]]) -> dict[str, list[str]]:
    """把 user 传进来的 schema 字典 key 全部用 _normalize_table_name 规范化，
    后续查表才能命中（避免 'public.users' vs 'users' 大小写 / quoting 差异）。"""
    return {_normalize_table_name(table): list(columns) for table, columns in schema.items()}


def unique_items(items: Any) -> list[Any]:
    """按 repr 去重的对象列表。用于把 tables 这种 dict 列表去重。"""
    result: list[Any] = []
    seen: set[str] = set()
    for item in items:
        key = repr(item)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def statement_indexed_items(analyses: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    """把多个 statement 的子项扁平化，每个 item 加上 statement_index。
    例如 insert_mappings 来自不同 statement，前端要知道每条来自哪个。"""
    result: list[dict[str, Any]] = []
    for statement_index, analysis in enumerate(analyses, start=1):
        for item in analysis[key]:
            result.append({"statement_index": statement_index, **item})
    return result
