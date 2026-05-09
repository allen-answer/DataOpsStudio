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

# S5 PR6：CREATE 节点产生 target_summary 的 kind 白名单。其他 kind
# （PROCEDURE / FUNCTION / PACKAGE / PACKAGE BODY / TRIGGER / TYPE / INDEX
# / SEQUENCE 等）不是 DDL on table，不该把过程名当 target_table。
_CREATE_TABLE_KINDS = frozenset({
    "TABLE", "VIEW", "MATERIALIZED VIEW", "MATERIALIZED_VIEW",
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


def _statement_to_ops(
    statement: Any,
    order: int,
    *,
    title_override: str | None = None,
    procedure_name: str = "",
) -> list[dict[str, Any]]:
    """把单个 parsed statement 转成 0..N 条 op 记录。

    多 target fan-out（INSERT ALL / TRUNCATE TABLE a, b）会产多条。
    `title_override` 走专用注释（procedure_segment 的 preceding_comment）；
    传 None 则从 statement.comments 自动抽。
    """
    if statement is None:
        return []
    e = exp()
    title = title_override if title_override is not None else extract_statement_title(statement)
    out: list[dict[str, Any]] = []
    if isinstance(statement, e.Insert):
        target = insert_target_table(statement.this)
        if target:
            out.append(_make_op(order, target, insert_dml_type(statement),
                                title=title, procedure_name=procedure_name))
    elif isinstance(statement, e.Update):
        target = _table_target(statement.this, e)
        if target:
            out.append(_make_op(
                order, target, "UPDATE",
                has_where=statement.args.get("where") is not None,
                title=title, procedure_name=procedure_name,
            ))
    elif isinstance(statement, e.Merge):
        target = _table_target(statement.this, e)
        if target:
            out.append(_make_op(order, target, "MERGE",
                                title=title, procedure_name=procedure_name))
    elif isinstance(statement, e.Delete):
        target = _table_target(statement.this, e)
        if target:
            out.append(_make_op(
                order, target, "DELETE",
                has_where=statement.args.get("where") is not None,
                title=title, procedure_name=procedure_name,
            ))
    elif isinstance(statement, e.Create):
        if is_temp_create(statement):
            return out
        # S5 PR6：CREATE PROCEDURE / FUNCTION / PACKAGE / TRIGGER 等不是
        # DDL on table，不该入 target_summary
        kind = (statement.args.get("kind") or "").upper()
        if kind and kind not in _CREATE_TABLE_KINDS:
            return out
        target = create_target_table(statement)
        if target and statement.args.get("expression") is not None:
            out.append(_make_op(order, target, create_dml_type(statement),
                                title=title, procedure_name=procedure_name))
    elif type(statement).__name__ == "TruncateTable":
        for table in statement.expressions or []:
            target = _table_target(table, e)
            if target:
                out.append(_make_op(order, target, "TRUNCATE",
                                    title=title, procedure_name=procedure_name))
    # S5 PR8：Oracle INSERT ALL / INSERT FIRST —— 多 target fan-out
    elif type(statement).__name__ == "MultitableInserts":
        for sub in statement.args.get("expressions", []) or []:
            inner = sub.this if hasattr(sub, "this") else sub
            if isinstance(inner, e.Insert):
                target = insert_target_table(inner.this)
                if target:
                    out.append(_make_op(order, target, insert_dml_type(inner),
                                        title=title, procedure_name=procedure_name))
    return out


def collect_target_operations(statements: list[Any]) -> list[dict[str, Any]]:
    """按 statement 顺序扫一遍，每个 DML 产出一条 op 记录。

    返回字段：order（脚本里的顺序，1-based）、target_table、dml_type、
    has_where（DELETE 没 WHERE 视为全表重置）、title（前置注释第一段，
    没注释则空）、procedure_name（top-level 时为 ""）。CREATE TEMP 跳过
    —— 临时表不应进 target_summary 业务视图。
    """
    ops: list[dict[str, Any]] = []
    for index, statement in enumerate(statements, start=1):
        ops.extend(_statement_to_ops(statement, index))
    return ops


def collect_procedure_operations(
    procedure_segments: list[dict[str, Any]],
    sqlglot_module: Any,
    dialect: str | None,
) -> list[dict[str, Any]]:
    """从 procedure_segments 抽 ops，order 用 line_start 保证源 SQL 顺序。

    每个 segment 是单条 procedure-内 DML（已被 segments.py 拆开）。这里
    重新喂 sqlglot 解析 —— 顶层 `parse_lineage_statements` 在过程体外壳
    解析失败时会用 `extract_analyzable_segments` 兜底，但兜底拿到的
    statements 顺序跟源不一致，导致 truncate→insert 模式判错。
    procedure_segments 本身就是按行号顺序，靠它们做主线最稳。

    `procedure_name` 来自 segment 的同名字段，让 aggregator 能区分
    多过程同表写入并把 procedure_origins 列在 summary。
    """
    ops: list[dict[str, Any]] = []
    sorted_segs = sorted(
        procedure_segments,
        key=lambda s: (int(s.get("line_start") or 0), str(s.get("segment_index") or "")),
    )
    for seg in sorted_segs:
        seg_sql = str(seg.get("sql") or "").strip()
        if not seg_sql:
            continue
        try:
            parsed = sqlglot_module.parse(seg_sql, read=dialect or None)
        except Exception:
            continue
        line_start = int(seg.get("line_start") or 0)
        proc_name = str(seg.get("procedure_name") or "")
        title = str(seg.get("preceding_comment") or "")
        for offset, stmt in enumerate(parsed):
            # order 编码：(line_start * 1000 + offset) 让同一行多语句仍有先后
            order = line_start * 1000 + offset
            ops.extend(_statement_to_ops(
                stmt, order,
                title_override=title if offset == 0 else "",
                procedure_name=proc_name,
            ))
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

        # delete_before_insert / truncate_before_insert：跨范围（top-level + 各
        # procedure 体内）任一处出现「先 X 后 Y」就算成立。procedure 内部的
        # 顺序通过 _has_followed_by_within_scope 单独检查，避免「proc1 truncate
        # → proc2 insert」这种跨过程巧合也被当成主动重刷模式。
        delete_before_insert = _has_followed_by_within_scope(ops, {"DELETE"}, _INSERT_FAMILY)
        truncate_before_insert = _has_followed_by_within_scope(ops, {"TRUNCATE"}, _INSERT_FAMILY)
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

        # procedure_origins：所有 procedure-内写过本目标表的 procedure 名（去重保序）。
        # 让 UI 能展示「dwd.users 被 refresh_dwd_users 过程重刷」等溯源信息。
        # top-level ops 的 procedure_name="" 不计入。
        proc_origins: list[str] = []
        seen_origins: set[str] = set()
        for op in sorted(ops, key=lambda o: o["order"]):
            pn = op.get("procedure_name") or ""
            if pn and pn not in seen_origins:
                seen_origins.add(pn)
                proc_origins.append(pn)

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
            "procedure_origins": proc_origins,
        })
    return summaries


def _make_op(
    order: int, target: str, dml_type: str,
    has_where: bool = False, title: str = "", procedure_name: str = "",
) -> dict[str, Any]:
    return {
        "order": order,
        "target_table": target,
        "dml_type": dml_type,
        "has_where": has_where,
        "title": title,
        "procedure_name": procedure_name,
    }


def _table_target(node: Any, e: Any) -> str:
    if isinstance(node, e.Table):
        return table_name(node)
    return ""


def _has_followed_by(ops: list[dict[str, Any]], earlier: set[str], later: set[str]) -> bool:
    """earlier 类的 op 之后是否再出现过 later 类——按 order 升序判断。

    保留向后兼容（旧调用方不分 procedure scope）。新代码请用
    `_has_followed_by_within_scope`，避免跨过程的偶然顺序被当成 refresh 模式。
    """
    seen_earlier = False
    for op in sorted(ops, key=lambda o: o["order"]):
        if op["dml_type"] in earlier:
            seen_earlier = True
            continue
        if seen_earlier and op["dml_type"] in later:
            return True
    return False


def _has_followed_by_within_scope(
    ops: list[dict[str, Any]], earlier: set[str], later: set[str],
) -> bool:
    """同 _has_followed_by，但「先后」必须在同一作用域里成立。

    作用域 = `procedure_name`：
    - 同一过程内 TRUNCATE → INSERT 算成立（典型刷数过程）
    - 顶层（procedure_name=""）的 TRUNCATE → 顶层 INSERT 算成立
    - proc1 TRUNCATE / proc2 INSERT 不算 —— 跨过程顺序不可推
    - 顶层 TRUNCATE / proc INSERT 算成立（用户先手 truncate 再调 proc 装载）
    - proc TRUNCATE / 顶层 INSERT 反过来也算（proc 清表后顶层补数据）
    """
    sorted_ops = sorted(ops, key=lambda o: o["order"])
    # 按 scope 分组：每个 procedure_name 独立 + 一个「跨范围」组（top + 任一 proc）
    by_scope: dict[str, list[dict[str, Any]]] = {}
    for op in sorted_ops:
        scope = op.get("procedure_name") or ""
        by_scope.setdefault(scope, []).append(op)
    # 1) 每个 procedure 内单独检查
    for scope, scope_ops in by_scope.items():
        if scope and _has_followed_by(scope_ops, earlier, later):
            return True
    # 2) 顶层 + 全局 ops（包含 proc）按 order 检查 —— 顶层 op 跟任意 proc op 跨界算
    top_ops = by_scope.get("", [])
    if top_ops:
        # 取顶层 op + 与之有时间关系的 proc op，混合按 order 看
        if _has_followed_by(sorted_ops, earlier, later):
            # 但要排除 proc1→proc2 的纯跨过程巧合：必须至少一端是顶层
            for op in sorted_ops:
                if op["dml_type"] in earlier and not op.get("procedure_name"):
                    # 顶层 earlier 后是否有任何 later
                    later_ops = [o for o in sorted_ops if o["order"] > op["order"] and o["dml_type"] in later]
                    if later_ops:
                        return True
                if op["dml_type"] in later and not op.get("procedure_name"):
                    earlier_ops = [o for o in sorted_ops if o["order"] < op["order"] and o["dml_type"] in earlier]
                    if earlier_ops:
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
