"""按目标表把 DML 操作折叠成 target_summary。

`insert_mappings` 是行级数据（一条 INSERT 列出 N 个 select 表达式 → N 个
mapping 项），下游想知道"这张目标表本脚本里被怎么写"得自己再聚合。
这个模块直接走 parsed statement 列表，按目标表归并 INSERT/UPDATE/MERGE
/DELETE/TRUNCATE 操作次数，并识别 DELETE+INSERT / TRUNCATE+INSERT 全量
重刷模式（Phase 7 Track B 第 2 项）。

DELETE/TRUNCATE 走的是 parsed statement 直扫——`analysis_statements()` 只
保留 INSERT/SELECT/UNION/UPDATE/MERGE，所以聚合得在那一步之前自己拿数据。
"""
from __future__ import annotations

from typing import Any

from app.lineage._common import normalize_table_name as _normalize_table_name
from app.lineage.dml import (
    create_dml_type, create_target_table, insert_dml_type, insert_target_table,
    is_temp_create,
)
from app.lineage.helpers import exp
from app.lineage.tables import table_name


_INSERT_FAMILY = frozenset({
    "INSERT", "INSERT_OVERWRITE", "REPLACE",
    "CREATE_TABLE_AS", "CREATE_OR_REPLACE_TABLE_AS",
})


def extract_statement_title(statement: Any) -> str:
    """从 sqlglot 节点的 .comments 列表里挑出第一段非空文本作为业务标题。

    sqlglot 把语句先行注释（行内 `--` 和 块 `/* */`）自动挂到节点的
    `comments` 属性上；这个 helper 取第一条注释里第一行非空文本，截断
    到 200 字符内（标题再长就不是标题了）。

    Oracle hint（`/*+ parallel(...) */` / `/*+ leading(...) */` 等）也会被
    sqlglot 同样塞进 `.comments`（`+` 已被剥掉），如果不过滤会误把 hint 当
    业务标题。这里通过 `_looks_like_oracle_hint` 跳过 hint。
    """
    comments = getattr(statement, "comments", None) or []
    for raw in comments:
        if raw is None:
            continue
        for line in str(raw).splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if _looks_like_oracle_hint(stripped):
                continue
            return stripped[:200]
    return ""


# Oracle hint 词汇表常见项。匹配上首词（裸关键字 / 函数式调用都覆盖）就视为 hint，
# 不当业务标题。漏一个不致命——还有"必须含中文 / 或不含 ASCII 括号-开头"等弱过滤
# 兜底。常见 hint 关键字参考 Oracle Database SQL Tuning Guide。
_ORACLE_HINT_KEYWORDS = frozenset({
    "parallel", "noparallel", "parallel_index", "no_parallel_index",
    "leading", "ordered", "use_hash", "use_nl", "use_merge", "use_nl_with_index",
    "no_use_hash", "no_use_nl", "no_use_merge",
    "index", "no_index", "index_asc", "index_desc", "index_combine", "index_join",
    "index_ffs", "no_index_ffs", "index_ss", "no_index_ss",
    "full", "rowid", "cluster", "hash",
    "first_rows", "all_rows", "choose", "rule",
    "append", "noappend", "append_values",
    "cache", "nocache",
    "merge", "no_merge", "unnest", "no_unnest", "push_pred", "no_push_pred",
    "push_subq", "no_push_subq", "expand_gset_to_union", "rewrite", "no_rewrite",
    "qb_name", "materialize", "inline", "with_plsql", "opt_param",
    "driving_site", "dynamic_sampling", "cardinality", "selectivity",
    "gather_plan_statistics", "monitor", "no_monitor",
    "result_cache", "no_result_cache",
})

import re as _re

_RE_HINT_HEAD = _re.compile(r"^([A-Za-z_][\w]*)", flags=_re.ASCII)


def _looks_like_oracle_hint(text: str) -> bool:
    match = _RE_HINT_HEAD.match(text.strip())
    if not match:
        return False
    return match.group(1).lower() in _ORACLE_HINT_KEYWORDS


def collect_target_operations(statements: list[Any]) -> list[dict[str, Any]]:
    """按 statement 顺序扫一遍，每个 DML 产出一条 op 记录。

    返回字段：order（脚本里的顺序，1-based）、target_table、dml_type、
    has_where（DELETE 没 WHERE 视为全表重置）、title（前置注释第一段，
    没注释则空）。CREATE TEMP 跳过——临时表不应进 target_summary 业务视图。
    """
    e = exp()
    ops: list[dict[str, Any]] = []
    for index, statement in enumerate(statements, start=1):
        if statement is None:
            continue
        title = extract_statement_title(statement)
        if isinstance(statement, e.Insert):
            target = insert_target_table(statement.this)
            if target:
                ops.append(_make_op(index, target, insert_dml_type(statement), title=title))
        elif isinstance(statement, e.Update):
            target = _table_target(statement.this, e)
            if target:
                ops.append(_make_op(
                    index, target, "UPDATE",
                    has_where=statement.args.get("where") is not None,
                    title=title,
                ))
        elif isinstance(statement, e.Merge):
            target = _table_target(statement.this, e)
            if target:
                ops.append(_make_op(index, target, "MERGE", title=title))
        elif isinstance(statement, e.Delete):
            target = _table_target(statement.this, e)
            if target:
                ops.append(_make_op(
                    index, target, "DELETE",
                    has_where=statement.args.get("where") is not None,
                    title=title,
                ))
        elif isinstance(statement, e.Create):
            if is_temp_create(statement):
                continue
            target = create_target_table(statement)
            if target and statement.args.get("expression") is not None:
                ops.append(_make_op(index, target, create_dml_type(statement), title=title))
        elif type(statement).__name__ == "TruncateTable":
            for table in statement.expressions or []:
                target = _table_target(table, e)
                if target:
                    ops.append(_make_op(index, target, "TRUNCATE", title=title))
    return ops


def aggregate_target_summary(operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同一目标表的 ops 折叠成一条 summary。

    - insert/update/merge/delete/truncate count 直接计数
    - delete_before_insert / truncate_before_insert：先 DELETE/TRUNCATE
      再 INSERT 同一张表
    - refresh_mode：truncate_insert > delete_insert（DELETE 无 WHERE）>
      delete_insert_partial > merge / update / append > mixed
    """
    by_target: dict[str, dict[str, Any]] = {}
    for op in operations:
        key = _normalize_table_name(op["target_table"])
        bucket = by_target.setdefault(key, {
            "target_table": op["target_table"],
            "ops": [],
        })
        bucket["ops"].append(op)

    summaries: list[dict[str, Any]] = []
    for bucket in by_target.values():
        ops = bucket["ops"]
        insert_count = sum(1 for o in ops if o["dml_type"] in _INSERT_FAMILY)
        update_count = sum(1 for o in ops if o["dml_type"] == "UPDATE")
        merge_count = sum(1 for o in ops if o["dml_type"] == "MERGE")
        delete_count = sum(1 for o in ops if o["dml_type"] == "DELETE")
        truncate_count = sum(1 for o in ops if o["dml_type"] == "TRUNCATE")

        delete_before_insert = _has_followed_by(ops, {"DELETE"}, _INSERT_FAMILY)
        truncate_before_insert = _has_followed_by(ops, {"TRUNCATE"}, _INSERT_FAMILY)
        has_full_delete = any(
            o["dml_type"] == "DELETE" and not o.get("has_where") for o in ops
        )

        # 收集本目标表所有写操作的 title（去重保序，给前端展示业务含义）。
        titles: list[str] = []
        seen_titles: set[str] = set()
        for op in sorted(ops, key=lambda o: o["order"]):
            title = op.get("title") or ""
            if title and title not in seen_titles:
                seen_titles.add(title)
                titles.append(title)

        summaries.append({
            "target_table": bucket["target_table"],
            "insert_count": insert_count,
            "update_count": update_count,
            "merge_count": merge_count,
            "delete_count": delete_count,
            "truncate_count": truncate_count,
            "delete_before_insert": delete_before_insert,
            "truncate_before_insert": truncate_before_insert,
            "refresh_mode": _classify_refresh_mode(
                insert_count, update_count, merge_count,
                delete_before_insert, truncate_before_insert, has_full_delete,
            ),
            "titles": titles,
        })
    return summaries


def _make_op(order: int, target: str, dml_type: str, has_where: bool = False, title: str = "") -> dict[str, Any]:
    return {
        "order": order,
        "target_table": target,
        "dml_type": dml_type,
        "has_where": has_where,
        "title": title,
    }


def _table_target(node: Any, e: Any) -> str:
    if isinstance(node, e.Table):
        return table_name(node)
    return ""


def _has_followed_by(ops: list[dict[str, Any]], earlier: set[str], later: set[str]) -> bool:
    """earlier 类的 op 之后是否再出现过 later 类——按 order 升序判断。"""
    seen_earlier = False
    for op in sorted(ops, key=lambda o: o["order"]):
        if op["dml_type"] in earlier:
            seen_earlier = True
            continue
        if seen_earlier and op["dml_type"] in later:
            return True
    return False


def _classify_refresh_mode(
    insert_count: int,
    update_count: int,
    merge_count: int,
    delete_before_insert: bool,
    truncate_before_insert: bool,
    has_full_delete: bool,
) -> str | None:
    if truncate_before_insert:
        return "truncate_insert"
    if delete_before_insert and has_full_delete:
        return "delete_insert"
    if delete_before_insert:
        return "delete_insert_partial"
    only_merge = merge_count and not insert_count and not update_count
    only_update = update_count and not insert_count and not merge_count
    only_insert = insert_count and not update_count and not merge_count
    if only_merge:
        return "merge"
    if only_update:
        return "update"
    if only_insert:
        return "append"
    if insert_count or update_count or merge_count:
        return "mixed"
    return None
