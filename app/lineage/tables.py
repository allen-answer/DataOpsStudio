"""表 / 别名 / CTE / 目标表 / 物理来源判定。

一个 SQL 里出现的 `Table` 节点不一定都是物理来源——可能是 CTE 名字、
INSERT 目标、子查询 alias 等。这一组函数负责把"真正的物理上游来源表"
和其它 Table 节点区分开。
"""
from __future__ import annotations

from typing import Any

from app.lineage._common import normalize_table_name as _normalize_table_name
from app.lineage.helpers import exp, sql


def table_name(table: Any) -> str:
    """从 Table AST 节点拼出 catalog.db.name 形式的限定名。
    各 segment 缺省直接跳过。"""
    catalog = table.args.get("catalog")
    db = table.args.get("db")
    parts = []
    if catalog:
        parts.append(catalog.sql())
    if db:
        parts.append(db.sql())
    parts.append(table.name)
    return ".".join(part for part in parts if part)


def explicit_alias(expression: Any) -> str:
    """显式写在 SQL 里的 AS xxx 别名，没有则空串。"""
    alias = expression.args.get("alias")
    return alias.name if alias else ""


def join_target(expression: Any) -> str:
    """JOIN 右侧目标的可读字符串：表 / 子查询 / 其它表达式。"""
    if expression is None:
        return ""
    e = exp()
    alias = explicit_alias(expression)
    if isinstance(expression, e.Table):
        name = table_name(expression)
        return f"{name} AS {alias}" if alias else name
    if isinstance(expression, e.Subquery):
        return f"子查询 AS {alias}" if alias else "子查询"
    return sql(expression)


def cte_names(statement: Any) -> set[str]:
    """statement 里所有 CTE 的名字 —— 这些不是物理上游，要排除。"""
    return {cte.alias for cte in statement.find_all(exp().CTE) if cte.alias}


def alias_names(statement: Any) -> set[str]:
    """所有 table / subquery / cte alias，规范化后的集合。
    用于过滤掉 `FROM users u JOIN orders o ON u.id = o.uid` 里 `u`/`o` 这种
    被 sqlglot 当成 Table 的 alias 节点。"""
    e = exp()
    aliases: set[str] = set()
    for table in statement.find_all(e.Table):
        alias = explicit_alias(table)
        if alias:
            aliases.add(_normalize_table_name(alias))
    for subquery in statement.find_all(e.Subquery):
        alias = explicit_alias(subquery)
        if alias:
            aliases.add(_normalize_table_name(alias))
    for cte in statement.find_all(e.CTE):
        if cte.alias:
            aliases.add(_normalize_table_name(cte.alias))
    return aliases


def is_physical_source_table(
    table_name_value: str,
    cte_names_set: set[str],
    target_tables: set[str],
    alias_names_set: set[str],
) -> bool:
    """判定一个 Table 节点是不是真物理上游：
    - 是 CTE 名字 → No
    - 是 INSERT/UPDATE/MERGE/CREATE 目标 → No
    - 是别处的 alias 引用（且不带 schema 限定）→ No
    - 其它 → Yes
    """
    normalized = _normalize_table_name(table_name_value)
    if normalized in {_normalize_table_name(name) for name in cte_names_set}:
        return False
    if normalized in {_normalize_table_name(name) for name in target_tables}:
        return False
    if "." not in table_name_value and normalized in alias_names_set:
        return False
    return True


def physical_tables(statement: Any) -> list[dict[str, str]]:
    """收集 statement 的所有物理上游表（含别名）。"""
    e = exp()
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    cte_set = cte_names(statement)
    targets = target_table_names(statement)
    aliases = alias_names(statement)
    for table in statement.find_all(e.Table):
        name = table_name(table)
        if not is_physical_source_table(name, cte_set, targets, aliases):
            continue
        alias = explicit_alias(table)
        key = (name, alias)
        if key in seen:
            continue
        seen.add(key)
        result.append({"table": name, "alias": alias})
    return result


def table_alias_map(statement: Any) -> dict[str, str]:
    """alias → 物理表名 的映射（含 alias 自身指向自己的退化项）。
    column.table 找物理来源时用这个表查。"""
    e = exp()
    alias_map: dict[str, str] = {}
    cte_set = cte_names(statement)
    targets = target_table_names(statement)
    aliases = alias_names(statement)
    for table in statement.find_all(e.Table):
        name = table_name(table)
        if not is_physical_source_table(name, cte_set, targets, aliases):
            continue
        alias = explicit_alias(table)
        if alias:
            alias_map[alias] = name
        alias_map[table.name] = name
    return alias_map


def target_table_names(statement: Any) -> set[str]:
    """所有 DML 目标表（INSERT into / UPDATE / MERGE / CREATE）。
    用于 _is_physical_source_table 过滤。
    注：循环 import —— `from app.lineage.dml import ...` 会反过来引 tables，
    所以 INSERT/CREATE 的 target 名字提取在这里就地内联实现，不依赖 dml 模块。"""
    e = exp()
    targets: set[str] = set()
    for create in statement.find_all(e.Create):
        target = create.this
        if isinstance(target, e.Schema):
            target = target.this
        if isinstance(target, e.Table):
            targets.add(table_name(target))
    for insert in statement.find_all(e.Insert):
        target = insert.this
        if isinstance(target, e.Schema):
            target = target.this
        if isinstance(target, e.Table):
            targets.add(table_name(target))
    for update in statement.find_all(e.Update):
        if isinstance(update.this, e.Table):
            targets.add(table_name(update.this))
    for merge in statement.find_all(e.Merge):
        if isinstance(merge.this, e.Table):
            targets.add(table_name(merge.this))
    for delete in statement.find_all(e.Delete):
        if isinstance(delete.this, e.Table):
            targets.add(table_name(delete.this))
    return targets
