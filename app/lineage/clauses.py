"""SELECT 子句信息抽取：JOIN / WHERE / GROUP BY / UNION。

这些不参与字段级血缘核心，只是给前端表格 / 调试用的辅助信息。每个函数
返回 dict 列表，内容自包含。
"""
from __future__ import annotations

from typing import Any

from app.lineage.helpers import exp, sql
from app.lineage.tables import join_target


def joins(select: Any, select_index: int) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for join in select.args.get("joins") or []:
        on_expression = join.args.get("on")
        result.append(
            {
                "select_index": str(select_index),
                "table": join_target(join.this),
                "kind": (join.args.get("kind") or "JOIN").upper(),
                "on": sql(on_expression) if on_expression else "",
            }
        )
    return result


def filters(select: Any, select_index: int) -> list[dict[str, str]]:
    where = select.args.get("where")
    if not where:
        return []
    return [{"select_index": str(select_index), "condition": sql(where.this)}]


def group_by(select: Any, select_index: int) -> list[dict[str, str]]:
    group = select.args.get("group")
    if not group:
        return []
    return [{"select_index": str(select_index), "expression": sql(item)} for item in group.expressions]


def unions(statement: Any) -> list[dict[str, str]]:
    e = exp()
    result: list[dict[str, str]] = []
    for index, union in enumerate(statement.find_all(e.Union), start=1):
        is_distinct = union.args.get("distinct")
        result.append(
            {
                "index": str(index),
                "type": "UNION" if is_distinct else "UNION ALL",
                "left": sql(union.left) if union.left else "",
                "right": sql(union.right) if union.right else "",
            }
        )
    return result
