"""Slow SQL analyze 的实质数据增强 —— 在规则 issues 基础上挂 schema 上下文 +
具体 CREATE INDEX DDL 候选,让不依赖 LLM 的建议也能可执行。

逻辑:
1. sqlglot 解析 SQL,提取 (table → WHERE 列 + JOIN ON 列) 映射
2. 对每个 full_table_scan / high_row_scan issue 命中的表,调用 datasource_introspect
   拉表行数 + 现有索引
3. 用现有索引的前导列匹配 WHERE/JOIN 列,识别 uncovered_columns
4. 给 uncovered 列生成具体 `CREATE INDEX idx_<table>_<col> ON <qualified>(<col>)` DDL
5. 含函数包列(TRIM/CASE 等)在 rationale 里给出原因,不生成 DDL(普通索引救不了)

向后兼容:slow_sql.analyze_sql 在 result 里新增 `schema_context` 字段,不动现有
`issues` / `suggestions` 字段。前端不展示也不报错。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# 普通索引救不了的列引用模式(函数包列 / 表达式包列)— 给 rationale 里提示
_WRAPPED_COL_HINT = (
    "JOIN/WHERE 条件含 CASE / TRIM / UPPER 等函数包列,普通 BTree 索引无效;"
    "选项:(a)改写消除函数(把转换前置到 ETL 上游),(b)Oracle/DM 用函数索引"
)


@dataclass
class TableUsage:
    table: str           # 限定名 "schema.table" 或裸表名
    alias: str = ""      # SQL 里的别名(若有)
    where_columns: set[str] = field(default_factory=set)   # 本表在 WHERE 用到的列
    join_columns: set[str] = field(default_factory=set)    # 本表在 JOIN ON 用到的列
    # per-column 函数包列追踪:某列至少有 1 处出现在非函数上下文 → 可建普通索引
    unwrapped_columns: set[str] = field(default_factory=set)
    wrapped_columns: set[str] = field(default_factory=set)  # 全部出现都在函数里

    @property
    def has_wrapped_anywhere(self) -> bool:
        return bool(self.wrapped_columns - self.unwrapped_columns)


@dataclass
class EnhancedSuggestion:
    table: str
    schema: str = ""
    table_row_count: int | None = None
    existing_indexes: list[dict[str, Any]] = field(default_factory=list)
    where_columns: list[str] = field(default_factory=list)
    join_columns: list[str] = field(default_factory=list)
    uncovered_columns: list[str] = field(default_factory=list)
    ddl_candidates: list[str] = field(default_factory=list)
    rationale: str = ""


def extract_table_usage(sql: str, dialect: str = "mysql") -> dict[str, TableUsage]:
    """走 sqlglot AST 拿 (table → usage) 映射。

    解析失败 / 拿不到 AST 时返 {},caller 跳过 enhance(不让 enhance 报错搞挂主流程)。

    限制:
    - 派生表 / CTE 内部列不归属真表(返回的 alias=derived 别名,后续按 alias 不到表则跳过)
    - WHERE 子句内的子查询展开(sqlglot 自动 walk),拿到嵌套引用
    - CASE 表达式 / 函数调用展开后能拿到内部 Column,同时 wrapped=True
    """
    try:
        import sqlglot
        from sqlglot import exp
    except ImportError:
        logger.warning("sqlglot 不可用,跳过 enhance")
        return {}

    try:
        parsed = sqlglot.parse_one(sql, dialect=dialect)
    except Exception as exc:
        logger.info("slow-sql enhance: sqlglot 解析失败,跳过(%s)", exc)
        return {}
    if parsed is None:
        return {}

    # 1) 先扫所有 Table 节点,建 alias → 限定表名映射
    # 2) 仅顶层(非派生表内部)Where / Join ON 子句的 Column 才计入 usage
    alias_to_full: dict[str, str] = {}
    for tbl in parsed.find_all(exp.Table):
        name = tbl.name
        if not name:
            continue
        # 限定名:schema.table 形式
        db = tbl.args.get("db")
        full = f"{db.name}.{name}" if db else name
        alias_node = tbl.args.get("alias")
        alias = alias_node.name if alias_node else name
        # 同别名首个胜出(避免子查询里 alias 冲突)
        alias_to_full.setdefault(alias, full)
        alias_to_full.setdefault(name, full)  # 别名缺省时直接用表名

    usages: dict[str, TableUsage] = {}

    def _record(col_node: exp.Column, in_func: bool) -> None:
        col_table_alias = col_node.table or ""
        col_name = col_node.name
        if not col_name or not col_table_alias:
            return  # 不限定别名的列归属不明,跳过
        full = alias_to_full.get(col_table_alias)
        if not full:
            return  # 别名指向派生表 / CTE,跳过
        u = usages.setdefault(full, TableUsage(table=full, alias=col_table_alias))
        # 区分 in_func — 由 caller 决定挂 where/join 哪个 set 之前再细分
        u._pending_cols = getattr(u, "_pending_cols", [])  # type: ignore[attr-defined]
        u._pending_cols.append((col_name, in_func))         # type: ignore[attr-defined]

    def _is_real_func(node: Any) -> bool:
        """判断是不是「真函数」(包列让 BTree 索引失效那种)。

        sqlglot 把 Or / And / Not / EQ / NEQ / LT / GT 等 boolean & 比较运算符都
        继承自 exp.Func(设计上为了让 evaluation engine 统一)。直接用
        isinstance(_, exp.Func) 会把 `a.x = 1` 也识别为 wrapped。需要排除:
        - Binary (Or / And / Connector / Predicate / Binary 算术)
        - Unary (Not / 单目算术)
        - Paren (括号)
        - DPipe / Concat 等字符串拼接 — concat 会让索引失效,保留为 wrapped
        """
        if isinstance(node, (exp.Case, exp.Cast)):
            return True
        if isinstance(node, (exp.Binary, exp.Unary, exp.Paren)):
            return False
        return isinstance(node, exp.Func)

    def _walk_for_cols(node: exp.Expression | None, target_attr: str) -> None:
        if node is None:
            return
        # 收所有 Column,标记是否在 func 内部
        # sqlglot 的 .walk 默认 BFS 遍历,parent 链可以推断
        for col in node.find_all(exp.Column):
            in_func = False
            par = col.parent
            while par is not None and par is not node:
                if _is_real_func(par):
                    in_func = True
                    break
                par = par.parent
            u_alias = col.table or ""
            full = alias_to_full.get(u_alias)
            if not full or not col.name:
                continue
            u = usages.setdefault(full, TableUsage(table=full, alias=u_alias))
            getattr(u, target_attr).add(col.name)
            if in_func:
                u.wrapped_columns.add(col.name)
            else:
                u.unwrapped_columns.add(col.name)

    # WHERE 子句
    where_node = parsed.args.get("where")
    if where_node is not None:
        _walk_for_cols(where_node, "where_columns")

    # 所有 JOIN ON 子句(包括 LEFT/RIGHT/INNER)
    for join in parsed.find_all(exp.Join):
        on = join.args.get("on")
        if on is not None:
            _walk_for_cols(on, "join_columns")

    # 给所有出现过的表(包括派生表内 FROM 的 ods.* 表)建 empty stub,
    # 让 enhance 能展示 rows + indexes(即使该表没在外层 WHERE/JOIN 直接引用)
    for full_name in set(alias_to_full.values()):
        usages.setdefault(full_name, TableUsage(table=full_name))

    return usages


# 标识符校验 — datasource_introspect 已用同样白名单,这里复用规则防 DDL 注入
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


def _safe_ident(s: str) -> bool:
    return bool(_IDENT_RE.match(s))


def _build_index_ddl(schema: str, table: str, columns: list[str]) -> str | None:
    """生成 CREATE INDEX DDL。返 None 表示标识符不合法(防注入)。"""
    if not _safe_ident(table):
        return None
    if schema and not _safe_ident(schema):
        return None
    if not all(_safe_ident(c) for c in columns):
        return None
    # 索引名约束: idx_<table>_<col1>_<col2>... 截断到 60 chars(MySQL 64 上限)
    idx_name = f"idx_{table}_{'_'.join(columns)}"[:60]
    if schema:
        qfull = f"`{schema}`.`{table}`"
    else:
        qfull = f"`{table}`"
    cols = ", ".join(f"`{c}`" for c in columns)
    return f"CREATE INDEX `{idx_name}` ON {qfull} ({cols});"


def _split_schema_table(name: str) -> tuple[str, str]:
    if "." in name:
        a, b = name.split(".", 1)
        return a, b
    return "", name


def _index_covers(idx: dict[str, Any], col: str) -> bool:
    """前导列匹配:idx.columns[0] == col(case-insensitive)。前导列匹配才用得上。"""
    cols = idx.get("columns") or []
    if not cols:
        return False
    return cols[0].lower() == col.lower()


def enhance_for_issues(
    *,
    datasource_id: str,
    sql: str,
    issues: list[dict[str, Any]],
    dialect: str = "mysql",
) -> list[dict[str, Any]]:
    """对 full_table_scan / high_row_scan issue 命中的表,拉 schema 上下文并产
    具体 DDL 建议。返 list[dict] 给 endpoint 直接 JSON 化。

    任一步出错(introspect 失败 / sqlglot 失败)返 [],主流程仍能拿到规则建议。
    """
    # 只对 mysql 启用(Oracle/DM 走 PLAN_TABLE 协议,后续补)
    if dialect != "mysql":
        return []
    if not issues:
        return []

    interesting_tables: set[str] = set()
    for i in issues:
        code = i.get("code") or ""
        if code in ("full_table_scan", "high_row_scan"):
            t = (i.get("table") or "").strip()
            if t and t != "<unknown>" and not t.startswith("<"):
                # 跳过 EXPLAIN <derived2> / <auto_key0> 这种伪表
                interesting_tables.add(t)
    if not interesting_tables:
        return []

    # 解析 SQL 拿表用法 + alias 映射
    usages = extract_table_usage(sql, dialect=dialect)
    # usages 的 key 是 schema.table 限定名;EXPLAIN 里 table 列可能是:
    # - SQL 写的别名(`a` / `b`)
    # - 裸表名(`ODS_AST_NOR_ACC_FUND`)
    # - 限定名(很少见)
    # 建三种查找方式
    by_full: dict[str, TableUsage] = {}
    by_base: dict[str, list[TableUsage]] = {}
    by_alias: dict[str, TableUsage] = {}
    for full, u in usages.items():
        by_full[full] = u
        _, base = _split_schema_table(full)
        by_base.setdefault(base.lower(), []).append(u)
        if u.alias and u.alias != base:
            by_alias[u.alias.lower()] = u

    # lazy import 防循环 + 启动成本
    from app.services.datasource_introspect import (
        introspect_indexes,
        introspect_row_count,
    )

    results: list[dict[str, Any]] = []
    for table_in_plan in sorted(interesting_tables):
        # table_in_plan 可能是 alias / 裸表名 / schema.table(EXPLAIN 输出格式)
        # 查找优先级: 限定名 → SQL 别名 → 裸表名(case-insensitive)
        usage: TableUsage | None = by_full.get(table_in_plan)
        if usage is None:
            usage = by_alias.get(table_in_plan.lower())
        if usage is None:
            base = table_in_plan.lower()
            cands = by_base.get(base) or []
            if len(cands) == 1:
                usage = cands[0]
            elif len(cands) > 1:
                # 多个同名表(不同 schema),取 join 列最多的
                usage = max(cands, key=lambda x: len(x.join_columns) + len(x.where_columns))

        if usage is None:
            continue

        # 拉 schema 上下文(best-effort,失败给 None / 空)
        full_name = usage.table  # "schema.table" 或裸 "table"
        try:
            indexes = introspect_indexes(datasource_id, full_name) or []
        except Exception as exc:
            logger.warning("introspect_indexes failed for %s: %s", full_name, exc)
            indexes = []
        try:
            row_count = introspect_row_count(datasource_id, full_name)
        except Exception as exc:
            logger.warning("introspect_row_count failed for %s: %s", full_name, exc)
            row_count = None

        where_cols = sorted(usage.where_columns)
        join_cols = sorted(usage.join_columns)
        all_key_cols = list(dict.fromkeys(where_cols + join_cols))  # 顺序: where 先 join 后, dedup

        # 找未被任何索引前导列覆盖的关键列
        uncovered: list[str] = []
        for col in all_key_cols:
            if not any(_index_covers(idx, col) for idx in indexes):
                uncovered.append(col)

        # 生成 DDL 候选:per-column,只为「至少有一处未被函数包」的列生成
        # WHERE 多列 + JOIN 多列时,优先 WHERE 列;复合索引留给 LLM / 人工判断
        schema, base = _split_schema_table(full_name)
        ddl_candidates: list[str] = []
        for col in uncovered:
            if col not in usage.unwrapped_columns:
                continue  # 该列全部出现都被函数包,普通索引救不了
            ddl = _build_index_ddl(schema, base, [col])
            if ddl:
                ddl_candidates.append(ddl)

        # 写 rationale
        rationale_parts: list[str] = []
        if row_count is not None:
            rationale_parts.append(f"表近似 {row_count:,} 行")
        if indexes:
            idx_names = ", ".join(idx.get("name", "?") for idx in indexes)
            rationale_parts.append(f"现有索引: {idx_names}")
        else:
            rationale_parts.append("现有无任何索引(PK 也无)")
        # 全部出现都被函数包的列,单独列出说明加普通索引无效
        cols_always_wrapped = sorted(
            (usage.wrapped_columns - usage.unwrapped_columns) & set(all_key_cols)
        )
        if cols_always_wrapped:
            rationale_parts.append(
                f"{_WRAPPED_COL_HINT}(影响列: {', '.join(cols_always_wrapped)})"
            )
        if where_cols:
            rationale_parts.append(f"WHERE 用列: {', '.join(where_cols)}")
        if join_cols:
            rationale_parts.append(f"JOIN 用列: {', '.join(join_cols)}")
        coverable_uncovered = [c for c in uncovered if c in usage.unwrapped_columns]
        if coverable_uncovered:
            rationale_parts.append(
                f"未被现有索引前导列覆盖且可建索引: {', '.join(coverable_uncovered)}"
            )

        results.append(EnhancedSuggestion(
            table=base,
            schema=schema,
            table_row_count=row_count,
            existing_indexes=indexes,
            where_columns=where_cols,
            join_columns=join_cols,
            uncovered_columns=uncovered,
            ddl_candidates=ddl_candidates,
            rationale=" · ".join(rationale_parts),
        ).__dict__)

    return results
