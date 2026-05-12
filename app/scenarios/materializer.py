"""Scenario materializer —— 把 generator 的 dict 落到真实 DB（Phase 12 切片 3）。

切片范围：
- 只支持 dialect=mysql（oracle/dm 留下切片）
- `build_materialize_plan(scenario, data)` 纯函数产 DDL + INSERT 计划
- `apply_plan(plan, executor)` 最小 `SqlExecutor` 协议跑计划 —— pymysql/oracle
  cursor 都能包，mock 也能包
- schema.table 名拆解：CREATE DATABASE IF NOT EXISTS `schema` + CREATE TABLE
  `schema`.`table`
- drop_first 默认 True：DROP TABLE IF EXISTS 再 CREATE（每次重做干净数据）

调用方式（caller 拿着真连接）：
    plan = build_materialize_plan(scenario, data)
    apply_plan(plan, CursorExecutor(conn.cursor()))
    conn.commit()

下切片：`/api/scenarios/{id}/materialize` 端点拿 datasource_id 查连接池，
UI 一键 generate→materialize→落库。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence

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
    if scenario.dialect != "mysql":
        raise NotImplementedError(
            f"materializer 当前只支持 mysql，{scenario.dialect} 留下个切片"
        )
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
        qfull = quote_qualified(table_def.name)
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
            drop_sql=f"DROP TABLE IF EXISTS {qfull}" if drop_first else None,
            create_sql=_build_create_table(qfull, eff_columns),
            index_sqls=_build_indexes(qfull, base, table_def.indexes),
            insert_sql=_build_insert(qfull, col_names),
            rows=param_rows,
        ))
    schema_sqls = [f"CREATE DATABASE IF NOT EXISTS {quote_identifier(s)}" for s in schemas_seen]
    return MaterializePlan(
        dialect="mysql", schemas=schema_sqls, tables=tables, warnings=warnings,
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


def _build_create_table(qfull: str, columns: list[ColumnDef]) -> str:
    parts: list[str] = []
    pks: list[str] = []
    for c in columns:
        bit = f"{quote_identifier(c.name)} {c.type}"
        if not c.nullable:
            bit += " NOT NULL"
        parts.append(bit)
        if c.pk:
            pks.append(quote_identifier(c.name))
    if pks:
        parts.append(f"PRIMARY KEY ({', '.join(pks)})")
    return f"CREATE TABLE {qfull} (\n  " + ",\n  ".join(parts) + "\n)"


def _build_indexes(qfull: str, base: str, indexes: list[IndexDef]) -> list[str]:
    out: list[str] = []
    for i, idx in enumerate(indexes):
        if idx.skip:
            continue
        idx_name = quote_identifier(f"idx_{base}_{i}")
        unique = "UNIQUE " if idx.unique else ""
        cols = ", ".join(quote_identifier(c) for c in idx.columns)
        out.append(f"CREATE {unique}INDEX {idx_name} ON {qfull} ({cols})")
    return out


def _build_insert(qfull: str, col_names: list[str]) -> str:
    cols = ", ".join(quote_identifier(c) for c in col_names)
    placeholders = ", ".join(["%s"] * len(col_names))
    return f"INSERT INTO {qfull} ({cols}) VALUES ({placeholders})"


def _chunked(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]
