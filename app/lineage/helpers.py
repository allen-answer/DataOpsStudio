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

    S5 PR8：Oracle `INSERT ALL ... INTO t1 ... INTO t2 ... SELECT ...` 拆成多个
    synthetic Insert（每个 target 一个，共享 source SELECT），让下游 INSERT
    分析对每个目标都跑一遍 fan-out。INSERT FIRST 同样 kind 也走这条路 ——
    fan-out 的语义是一样的。
    """
    e = exp()
    if isinstance(statement, e.Create):
        nested = [item for item in statement.find_all(e.Insert)]
        if nested:
            return nested
        if isinstance(statement.args.get("expression"), (e.Select, e.Union)):
            return [statement]
    # S5 PR8：MultitableInserts 拆成多个独立 Insert
    if type(statement).__name__ == "MultitableInserts":
        source = statement.args.get("source")
        out: list[Any] = []
        for target in statement.args.get("expressions", []) or []:
            inner = target.this if hasattr(target, "this") else target
            if not isinstance(inner, e.Insert) or source is None:
                continue
            synthetic = inner.copy()
            synthetic.set("expression", source.copy())
            out.append(synthetic)
        if out:
            return out
    if isinstance(statement, (e.Insert, e.Select, e.Union, e.Update, e.Merge, e.Delete)):
        return [statement]
    return []


def transform_type(expression: Any) -> str:
    """字段表达式分类。Phase 7 G：从 3 类细化到 11 类，让前端 / report 能看清"这个
    输出列是 CASE 条件还是窗口函数还是 CAST"，避免一律打 "表达式" 兜底。

    优先级（外层语义优先）：
        WINDOW > 聚合 > CASE > CAST > COALESCE > 算术 > 字符串/日期/数值函数 > UDF > 字面量 > 直接映射 > 表达式
    """
    e = exp()
    inner = expression.this if isinstance(expression, e.Alias) else expression

    # 窗口 / 聚合 —— 外层语义；用 find_all 是因为 sqlglot 经常把 AggFunc 包在 Alias / Window 里
    if any(True for _ in expression.find_all(e.Window)):
        return "窗口"
    if any(True for _ in expression.find_all(e.AggFunc)):
        return "聚合"

    # CASE / 条件 / CAST / COALESCE 类
    if isinstance(inner, e.Case) or any(True for _ in expression.find_all(e.Case)):
        return "条件"
    if isinstance(inner, (e.Cast, e.TryCast)):
        return "类型转换"
    coalesce_types = tuple(
        cls for cls in (
            getattr(e, "Coalesce", None),
            getattr(e, "Nvl", None),
            getattr(e, "Nvl2", None),
            getattr(e, "Nullif", None),
            getattr(e, "If", None),
        ) if cls is not None
    )
    if coalesce_types and isinstance(inner, coalesce_types):
        return "空值兜底"

    # 算术（Add / Sub / Mul / Div / Mod）
    arithmetic_types = tuple(
        cls for cls in (
            getattr(e, "Add", None), getattr(e, "Sub", None), getattr(e, "Mul", None),
            getattr(e, "Div", None), getattr(e, "Mod", None), getattr(e, "Neg", None),
        ) if cls is not None
    )
    if arithmetic_types and isinstance(inner, arithmetic_types):
        return "算术"

    # 字符串 / 日期 / 数值函数家族（按类名前缀简单分类，覆盖各方言）
    string_funcs = {"Concat", "Lower", "Upper", "Substring", "Substr", "Trim", "LTrim",
                    "RTrim", "Length", "Replace", "Repeat", "Reverse", "Left", "Right", "Pad"}
    date_funcs = {"CurrentDate", "CurrentTimestamp", "CurrentTime", "DateAdd", "DateDiff",
                  "DateSub", "DateTrunc", "DateFromParts", "DatetimeAdd", "DatetimeDiff",
                  "Extract", "Year", "Month", "Day", "Hour", "Minute", "Second",
                  "ToDate", "TimeStr", "StrToDate", "FromUnixtime", "UnixToTime"}
    numeric_funcs = {"Round", "Ceil", "Floor", "Abs", "Mod", "Power", "Sqrt", "Exp", "Ln", "Log"}
    cls_name = type(inner).__name__
    if cls_name in string_funcs:
        return "字符串函数"
    if cls_name in date_funcs:
        return "日期函数"
    if cls_name in numeric_funcs:
        return "数值函数"

    # 字面量常量（无 column 来源）
    if isinstance(inner, e.Literal):
        return "字面量"

    # UDF / 未知函数：sqlglot 用 Anonymous 表示无 builtin 映射的函数；带 . 的 column-style
    # 函数（Oracle pkg.fn）也算 UDF
    if isinstance(inner, getattr(e, "Anonymous", tuple())):
        return "UDF"
    if isinstance(inner, e.Func) and not any(True for _ in inner.find_all(e.Column)):
        # 没列引用、纯参数函数（CURRENT_DATE / RAND() 等）
        return "字面量"
    if isinstance(inner, e.Func):
        return "函数"

    # 直接映射（Column 或 Alias(Column)）
    if isinstance(inner, e.Column):
        return "直接映射"

    return "表达式"


def window_partition_columns(expression: Any) -> dict[str, list[str]]:
    """把表达式里所有 Window 节点的 PARTITION BY / ORDER BY 列名抽出来。

    为窗口函数提供"分组依赖"的额外可见性 —— 这些列虽然也被 source_info 当作普通源列
    收进 source_columns，但前端通常想单独标"这是 partition / 排序键"。
    """
    e = exp()
    partition_columns: list[str] = []
    order_columns: list[str] = []
    for window in expression.find_all(e.Window):
        for partition in window.args.get("partition_by") or []:
            for column in partition.find_all(e.Column):
                partition_columns.append(sql(column))
        order = window.args.get("order")
        if order is not None:
            for column in order.find_all(e.Column):
                order_columns.append(sql(column))
    return {
        "partition_by": _unique_strings(partition_columns),
        "order_by": _unique_strings(order_columns),
    }


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
