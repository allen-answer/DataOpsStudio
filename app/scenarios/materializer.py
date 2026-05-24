"""Scenario materializer —— 把 generator 的 dict 落到真实 DB。

`build_materialize_plan(scenario, data)` 纯函数产 DDL + INSERT 计划。
`apply_plan(plan, executor)` 最小 `SqlExecutor` 协议跑计划 —— pymysql/oracle
cursor 都能包，mock 也能包。

方言分派：scenario.dialect → app.scenarios.dialects.get_dialect 选实现，
负责 identifier quote / placeholder / CREATE DATABASE 语义 / DROP 安全语义
等方言相关产物。MySQL / Oracle / DM 已支持（DM 复用 Oracle）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence

from app.scenarios.dialects import MaterializeDialect, get_dialect
from app.scenarios.generator import TableData
from app.scenarios.models import ColumnDef, IndexDef, Scenario, TableDef


class SqlExecutor(Protocol):
    """最小 SQL 执行协议。pymysql / oracle / sqlite cursor 都能适配。"""

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> None: ...

    def executemany(self, sql: str, params_list: list[Sequence[Any]]) -> None: ...


@dataclass
class TablePlan:
    full_name: str  # `ods.orders` 或 `orders`
    schema: str | None
    base_name: str
    quoted_full: str  # `` `ods`.`orders` ``
    columns: list[ColumnDef]  # 已应用 derives_from rename
    drop_sql: str | None  # None when drop_first=False
    create_sql: str
    index_sqls: list[str]
    insert_sql: str
    rows: list[tuple[Any, ...]]


@dataclass
class MaterializePlan:
    dialect: str
    schemas: list[str]  # CREATE DATABASE IF NOT EXISTS ... 语句
    tables: list[TablePlan]
    warnings: list[str] = field(default_factory=list)


# ─── public API ─────────────────────────────────────────────────────────────


def build_materialize_plan(
    scenario: Scenario,
    data: dict[str, TableData],
    *,
    drop_first: bool = True,
) -> MaterializePlan:
    dialect = get_dialect(scenario.dialect)
    schemas_seen: list[str] = []
    schemas_set: set[str] = set()
    tables: list[TablePlan] = []
    warnings: list[str] = []
    all_tables = {t.name: t for t in scenario.tables}
    for table_def in scenario.tables:
        eff_columns = effective_columns(table_def, all_tables)
        if not eff_columns:
            warnings.append(f"table {table_def.name} has no columns; skipped")
            continue
        schema, base = split_name(table_def.name)
        if schema and schema not in schemas_set:
            schemas_set.add(schema)
            schemas_seen.append(schema)
        qfull = dialect.quote_qualified(table_def.name)
        col_names = [c.name for c in eff_columns]
        rows = data.get(table_def.name, [])
        # 按 effective column 顺序拿值（缺列 → None；多余字段忽略）
        param_rows = [tuple(r.get(c) for c in col_names) for r in rows]
        tables.append(TablePlan(
            full_name=table_def.name,
            schema=schema,
            base_name=base,
            quoted_full=qfull,
            columns=eff_columns,
            drop_sql=dialect.drop_table_sql(qfull) if drop_first else None,
            create_sql=dialect.create_table_sql(qfull, eff_columns),
            index_sqls=_build_indexes(dialect, qfull, base, table_def.indexes),
            insert_sql=dialect.insert_sql(qfull, col_names),
            rows=param_rows,
        ))
    schema_sqls: list[str] = []
    for s in schemas_seen:
        sql = dialect.schema_create_sql(s)
        if sql is not None:
            schema_sqls.append(sql)
    return MaterializePlan(
        dialect=dialect.name, schemas=schema_sqls, tables=tables, warnings=warnings,
    )


def apply_plan(
    plan: MaterializePlan,
    executor: SqlExecutor,
    *,
    batch_size: int = 500,
) -> dict[str, Any]:
    """按 plan 顺序跑 schemas → 每表 drop/create/index/inserts。

    返回 summary：{dialect, schemas_created, tables: [{name, rows_inserted, ...}], warnings}
    不包 commit —— caller 决定事务边界。
    """
    summary: dict[str, Any] = {
        "dialect": plan.dialect,
        "schemas_created": list(plan.schemas),
        "tables": [],
        "warnings": list(plan.warnings),
    }
    for sql in plan.schemas:
        executor.execute(sql)
    for t in plan.tables:
        if t.drop_sql:
            executor.execute(t.drop_sql)
        executor.execute(t.create_sql)
        for idx_sql in t.index_sqls:
            executor.execute(idx_sql)
        inserted = 0
        for batch in _chunked(t.rows, batch_size):
            if batch:
                executor.executemany(t.insert_sql, batch)
                inserted += len(batch)
        summary["tables"].append({
            "name": t.full_name,
            "schema": t.schema,
            "rows_inserted": inserted,
            "indexes_created": len(t.index_sqls),
        })
    return summary


class CursorExecutor:
    """适配 dbapi cursor → SqlExecutor。caller 用真 cursor 包一层。"""

    def __init__(self, cursor: Any):
        self._cur = cursor

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> None:
        if params is None:
            self._cur.execute(sql)
        else:
            self._cur.execute(sql, params)

    def executemany(self, sql: str, params_list: list[Sequence[Any]]) -> None:
        self._cur.executemany(sql, params_list)


# ─── helpers（public：recorder 也用） ────────────────────────────────────────


def split_name(name: str) -> tuple[str | None, str]:
    """`schema.base` → (schema, base)；`base` → (None, base)。"""
    if "." in name:
        schema, base = name.split(".", 1)
        return schema, base
    return None, name


def quote_identifier(ident: str) -> str:
    """MySQL identifier quoting —— 反引号包裹，反引号自身 double 转义。"""
    return "`" + ident.replace("`", "``") + "`"


def quote_qualified(name: str) -> str:
    """`ods.orders` → `` `ods`.`orders` ``；裸名 `orders` → `` `orders` ``。"""
    schema, base = split_name(name)
    return f"{quote_identifier(schema)}.{quote_identifier(base)}" if schema else quote_identifier(base)


def effective_columns(
    table: TableDef, all_tables: dict[str, TableDef]
) -> list[ColumnDef]:
    """考虑 derives_from + column_overrides，返回最终列列表（已应用 rename）。

    column type 不变 —— transform 改值不改 schema。
    """
    if not table.derives_from:
        return list(table.columns)
    parent = all_tables.get(table.derives_from)
    if not parent:
        return list(table.columns)
    rename_map = {ov.from_: ov.rename for ov in table.column_overrides if ov.rename}
    out: list[ColumnDef] = []
    for c in parent.columns:
        new_name = rename_map.get(c.name) or c.name
        out.append(c if new_name == c.name else c.model_copy(update={"name": new_name}))
    return out


def _build_indexes(
    dialect: MaterializeDialect,
    qfull: str,
    base: str,
    indexes: list[IndexDef],
) -> list[str]:
    """生成本表的 CREATE INDEX 列表（skip=True 的过滤掉）。

    索引名按 `idx_<base>_<i>` 命名 + 走方言 quote；多列索引按 yml columns 顺序。
    """
    out: list[str] = []
    for i, idx in enumerate(indexes):
        if idx.skip:
            continue
        idx_name = dialect.quote_identifier(f"idx_{base}_{i}")
        out.append(dialect.create_index_sql(
            idx_name, qfull, idx.columns, unique=idx.unique,
        ))
    return out


def _chunked(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


# ─── Phase 14 P0-2: streaming materialize ──────────────────────────────────


def materialize_streaming(
    scenario: Scenario,
    executor: SqlExecutor,
    *,
    drop_first: bool = True,
    batch_size: int = 1000,
    analyze: bool = True,
) -> dict[str, Any]:
    """端到端 streaming materialize —— 不在 Python 持完整 dataset。

    跟 `build_materialize_plan + apply_plan` 老路径**结果等价**(同 schema /
    同行数 / 同 anomaly),但内存峰值是 O(batch_size × col_width) 恒定,千万
    行规模也不爆。Phase 14 P0-2 加。

    流程(单 transaction,caller 在 connection 层 commit):
      1. CREATE DATABASE IF NOT EXISTS ...
      2. 对每张表(按 derives_from 拓扑序):
         a. DROP / CREATE / CREATE INDEX
         b. 源表:`iter_table_rows_streaming(...)` 逐 batch executemany INSERT
         c. 派生表:`INSERT INTO derived (cols) SELECT (transform_exprs) FROM source`
            (SQL 端复制 + 改名 + transform,零 Python 内存)

    派生表当前支持的 transform:
    - `DATE($)` → `DATE(source_col)`(MySQL/Oracle/DM 都 native)
    - 其它 → 直传(rename only)
    """
    from app.scenarios.generator import (
        _FKPool,
        estimate_total_rows,
        iter_table_rows_streaming,
    )

    dialect = get_dialect(scenario.dialect)
    all_tables = {t.name: t for t in scenario.tables}

    # 顺序:源表先,派生表后(派生表 SQL 依赖源表已存在)
    # Phase 14 #3 Round 6:同步含 FK references 拓扑序,被引用表先生成
    ordered = _resolve_table_order(scenario.tables)

    # Phase 14 #3 Round 6 — FK pool 跨表共享值
    # 计算"哪些列被引用了" — 只这些列入 pool,省内存(大表 1500w 行 ×10 列 = 15GB,
    # 但只有 1-2 列(PK / branch_code)真正被 FK 引用,只入这俩 ~3GB,仍大但可控
    fk_referenced_cols: dict[str, set[str]] = {}   # table_name → set[col_name]
    for t in scenario.tables:
        for c in t.columns:
            if c.gen == "foreign_key" and c.references:
                ref_table_path, ref_col = c.references.rsplit(".", 1)
                # 同时记录精确名 + simple name 防 schema 前缀差异
                fk_referenced_cols.setdefault(ref_table_path, set()).add(ref_col)
                if "." in ref_table_path:
                    simple = ref_table_path.rsplit(".", 1)[-1]
                    fk_referenced_cols.setdefault(simple, set()).add(ref_col)
    pool = _FKPool()

    schemas_seen: list[str] = []
    schemas_set: set[str] = set()
    for t in ordered:
        s, _ = split_name(t.name)
        if s and s not in schemas_set:
            schemas_set.add(s)
            schemas_seen.append(s)
    schema_sqls = [dialect.schema_create_sql(s) for s in schemas_seen]
    schema_sqls = [sql for sql in schema_sqls if sql is not None]

    summary: dict[str, Any] = {
        "dialect": dialect.name,
        "schemas_created": list(schema_sqls),
        "tables": [],
        "warnings": [],
        "streaming": True,
        "batch_size": batch_size,
    }

    for sql in schema_sqls:
        executor.execute(sql)

    for table_def in ordered:
        eff_columns = effective_columns(table_def, all_tables)
        if not eff_columns:
            summary["warnings"].append(f"table {table_def.name} has no columns; skipped")
            continue
        _, base = split_name(table_def.name)
        qfull = dialect.quote_qualified(table_def.name)
        col_names = [c.name for c in eff_columns]
        # DDL
        if drop_first:
            executor.execute(dialect.drop_table_sql(qfull))
        executor.execute(dialect.create_table_sql(qfull, eff_columns))
        for idx_sql in _build_indexes(dialect, qfull, base, table_def.indexes):
            executor.execute(idx_sql)
        # INSERT
        rows_inserted = 0
        if table_def.derives_from and table_def.derives_from in all_tables:
            # SQL-side INSERT INTO derived SELECT FROM source(零 Python 内存)
            parent = all_tables[table_def.derives_from]
            qparent = dialect.quote_qualified(parent.name)
            rename_map = {ov.from_: ov.rename for ov in table_def.column_overrides if ov.rename}
            transform_map = {ov.from_: ov.transform for ov in table_def.column_overrides if ov.transform}
            select_exprs = []
            for c in parent.columns:
                src_col = dialect.quote_identifier(c.name)
                t = (transform_map.get(c.name) or "").strip().upper()
                if t.startswith("DATE(") and "$" in t:
                    select_exprs.append(f"DATE({src_col})")
                else:
                    select_exprs.append(src_col)
            target_cols = [
                dialect.quote_identifier(rename_map.get(c.name, c.name))
                for c in parent.columns
            ]
            limit_clause = f" LIMIT {table_def.rows}" if table_def.rows else ""
            sql = (
                f"INSERT INTO {qfull} ({', '.join(target_cols)}) "
                f"SELECT {', '.join(select_exprs)} FROM {qparent}{limit_clause}"
            )
            executor.execute(sql)
            rows_inserted = estimate_total_rows(table_def, scenario)
        else:
            # 流式 batch INSERT
            insert_sql = dialect.insert_sql(qfull, col_names)
            # Phase 14 #3 Round 6 — 此表是否被 FK 引用?如是,边 stream 边累计
            simple_name = table_def.name.rsplit(".", 1)[-1] if "." in table_def.name else table_def.name
            ref_cols_for_this_table = (
                fk_referenced_cols.get(table_def.name, set())
                | fk_referenced_cols.get(simple_name, set())
            )
            # per-column 累积 list(只累积被引用的列)
            stream_values: dict[str, list[Any]] = {c: [] for c in ref_cols_for_this_table}

            for batch in iter_table_rows_streaming(
                table_def, all_tables, scenario, batch_size=batch_size,
                pool=pool,
            ):
                if not batch:
                    continue
                param_rows = [tuple(r.get(c) for c in col_names) for r in batch]
                executor.executemany(insert_sql, param_rows)
                rows_inserted += len(batch)
                # 累积被引用列的值到本地 list(等表跑完入 pool)
                for ref_col in ref_cols_for_this_table:
                    stream_values[ref_col].extend(row.get(ref_col) for row in batch)

            # 表跑完,被引用列的值入 pool 给后续引用表用
            for ref_col, values in stream_values.items():
                pool.add(table_def.name, ref_col, values)
                # 也按 simple name 注册一份(让 references="schema.table.col" 跟
                # references="table.col" 都能找到)
                if simple_name != table_def.name:
                    pool.add(simple_name, ref_col, values)
        # Phase 14 P0-3:materialize 完跑 ANALYZE,让优化器拿到真实统计 ——
        # SQL 优化用途下 EXPLAIN cardinality 必须接近真实,否则 plan 决策跟生产
        # 对不上。失败 best-effort 吞掉,不阻塞 materialize 主流程。
        analyzed = False
        if analyze:
            analyze_sql = dialect.analyze_table_sql(qfull)
            if analyze_sql is not None:
                try:
                    executor.execute(analyze_sql)
                    analyzed = True
                except Exception as exc:  # noqa: BLE001
                    summary["warnings"].append(
                        f"ANALYZE {table_def.name} failed (skipped): {exc}"
                    )
        summary["tables"].append({
            "name": table_def.name,
            "rows_inserted": rows_inserted,
            "rows_generated": estimate_total_rows(table_def, scenario),
            "derived": bool(table_def.derives_from),
            "analyzed": analyzed,
        })
    return summary


def _resolve_table_order(tables: list[TableDef]) -> list[TableDef]:
    """跟 generator._resolve_order 同算法,放这里避免 materializer → generator
    依赖 +更清晰职责(materializer 自己也要知道 derives_from 顺序)。

    Phase 14 #3 Round 6:除 derives_from 外,也按 FK references 排序 — 被
    引用表先生成 → INSERT → pool 入值 → 引用表才能从 pool 抽。
    """
    by_name = {t.name: t for t in tables}
    by_simple: dict[str, TableDef] = {}
    for t in tables:
        simple = t.name.rsplit(".", 1)[-1] if "." in t.name else t.name
        by_simple.setdefault(simple, t)

    def find_table(ref: str) -> TableDef | None:
        if ref in by_name:
            return by_name[ref]
        simple = ref.rsplit(".", 1)[-1] if "." in ref else ref
        return by_simple.get(simple)

    seen: set[str] = set()
    order: list[TableDef] = []

    def visit(t: TableDef) -> None:
        if t.name in seen:
            return
        if t.derives_from:
            parent = find_table(t.derives_from)
            if parent and parent.name != t.name:
                visit(parent)
        for col in t.columns:
            if col.gen == "foreign_key" and col.references:
                ref_table_path = col.references.rsplit(".", 1)[0]
                parent = find_table(ref_table_path)
                if parent and parent.name != t.name:
                    visit(parent)
        seen.add(t.name)
        order.append(t)

    for t in tables:
        visit(t)
    return order
