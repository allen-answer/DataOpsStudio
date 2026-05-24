"""Phase 14 P1-1: 从真实 datasource 反向生成 scenario yml。

SQL 优化场景下,把生产 schema 翻成沙盒 yml 是高频动作 —— 手工抄 SHOW CREATE
TABLE 输出 + 翻译成 yml columns 又慢又错。这模块走 introspect 接口拉表的字段
+ 索引 + 行数,翻译成 Scenario 模型 + yml 字符串。

调用模式:
    from app.scenarios.yml_importer import import_tables_from_datasource
    scenario, yml_text = import_tables_from_datasource(
        datasource_id="ds-1",
        table_names=["orders", "users"],
        scenario_id="orders-perf",
        scenario_name="Orders 性能场景",
    )

后续 UI 接 `POST /api/scenarios/import-from-datasource` 拿 yml 直接下载或贴
到 scenario 编辑器(下个切片做)。
"""
from __future__ import annotations

import logging
import re
from typing import Any

import yaml

from app.scenarios.models import ColumnDef, IndexDef, Scenario, TableDef
from app.services.datasource_introspect import (
    introspect_columns,
    introspect_indexes,
    introspect_row_count,
)
from app.services.repositories import datasource_store


logger = logging.getLogger(__name__)


_DEFAULT_ROWS = 1000


def import_tables_from_datasource(
    datasource_id: str,
    table_names: list[str],
    *,
    scenario_id: str,
    scenario_name: str = "",
    dialect: str = "",
    default_rows: int = _DEFAULT_ROWS,
) -> tuple[Scenario, str]:
    """对每张 table_names 走 introspect → 翻 TableDef → 装 Scenario → dump yml。

    `default_rows`:当 introspect_row_count 返 None(无统计信息)时用的兜底行数。
    `dialect`:留空走 datasource.db_type 推断("mysql" / "oracle" / "dm")。

    raises:
    - ValueError:datasource 不存在 / table_names 空 / 任一表 introspect 全空
    """
    if not table_names:
        raise ValueError("table_names is required")
    source = datasource_store.get(datasource_id)
    if source is None:
        raise ValueError(f"datasource {datasource_id} not found")
    if not dialect:
        dialect = _db_type_to_dialect(source.db_type.value)
    tables: list[TableDef] = []
    warnings: list[str] = []
    for tn in table_names:
        td, warn = _import_one_table(datasource_id, tn, default_rows)
        if td is None:
            warnings.append(f"{tn}: {warn}")
            continue
        tables.append(td)
        if warn:
            warnings.append(f"{tn}: {warn}")
    if not tables:
        raise ValueError(
            f"failed to import any table; warnings: {'; '.join(warnings) or '(none)'}"
        )
    scenario = Scenario.model_validate({
        "id": scenario_id,
        "name": scenario_name or scenario_id,
        "dialect": dialect,
        "seed": 42,
        "tables": [t.model_dump(exclude_defaults=True, by_alias=True) for t in tables],
        "anomalies": [],
        "workloads": [{
            "kind": "slow_query",
            "name": "TODO-rename-me",
            "sql": "-- TODO: 把生产慢 SQL 贴到这里\n-- SELECT ... FROM <table> WHERE ...",
            "expected_optimizations": ["TODO: 你预期的优化点(让 AI 复核对照)"],
        }],
    })
    return scenario, _dump_yml(scenario, warnings)


def _import_one_table(
    datasource_id: str, table_name: str, default_rows: int,
) -> tuple[TableDef | None, str]:
    """单表 introspect → TableDef。返 (TableDef | None, warning_msg)。"""
    try:
        cols = introspect_columns(datasource_id, table_name)
    except Exception as exc:  # noqa: BLE001
        return None, f"introspect_columns failed: {exc}"
    if not cols:
        return None, "no columns returned (table missing or empty schema)"
    try:
        indexes = introspect_indexes(datasource_id, table_name)
    except Exception as exc:  # noqa: BLE001
        indexes = []
        warning = f"introspect_indexes failed (skipped indexes): {exc}"
    else:
        warning = ""
    pk_cols: set[str] = set()
    for idx in indexes:
        if idx.get("is_pk"):
            pk_cols.update(idx.get("columns", []))
    column_defs: list[ColumnDef] = []
    for c in cols:
        gen, range_, values = _infer_gen(c["data_type"], c["name"], c["name"] in pk_cols)
        col_dict: dict[str, Any] = {
            "name": c["name"],
            "type": c["data_type"].upper() if c["data_type"] else "VARCHAR(255)",
            "gen": gen,
            "nullable": bool(c.get("nullable", True)),
            "pk": c["name"] in pk_cols,
        }
        if range_ is not None:
            col_dict["range"] = range_
        if values is not None:
            col_dict["values"] = values
        if c.get("comment"):
            col_dict["description"] = str(c["comment"])
        column_defs.append(ColumnDef.model_validate(col_dict))
    index_defs: list[IndexDef] = []
    for idx in indexes:
        if idx.get("is_pk"):
            continue  # PK 已经通过 col.pk 表达,不重复 CREATE INDEX
        index_defs.append(IndexDef.model_validate({
            "columns": idx["columns"],
            "unique": bool(idx.get("unique", False)),
        }))
    rows = introspect_row_count(datasource_id, table_name) or default_rows
    # cap 100 万行(yml_importer 默认值);用户要更大手工改 yml
    if rows > 1_000_000:
        warning = (warning + " | " if warning else "") + (
            f"row count {rows} capped to 1_000_000 in yml (sandbox 默认上限);"
            f"手工改大需更新 scenario.tables[].rows"
        )
        rows = 1_000_000
    return TableDef.model_validate({
        "name": table_name,
        "role": "source",
        "rows": rows,
        "columns": [c.model_dump(exclude_defaults=True, by_alias=True) for c in column_defs],
        "indexes": [i.model_dump(exclude_defaults=True, by_alias=True) for i in index_defs],
    }), warning


def _infer_gen(
    data_type: str, col_name: str, is_pk: bool,
) -> tuple[str, list[Any] | None, list[Any] | None]:
    """从 SQL 列类型 + 列名 + PK 标志推断 generator + range / values 默认。

    PK 列优先 sequence(让生成的数据 PK 单调递增,索引选择性高,符合生产形态)。
    非 PK 数值列走 random_int + 合理 range。字符串列走 realistic 让 Faker/AI 填。
    日期列走 timestamp + 默认 1 年区间。

    返 (gen, range, values) —— range / values 可能 None(表示走 column.gen 默认)。
    """
    raw = data_type or ""
    t = raw.upper()
    name_lower = col_name.lower()
    if is_pk and ("INT" in t or "BIGINT" in t or "SERIAL" in t):
        return ("sequence", None, None)
    if is_pk and ("CHAR" in t or "TEXT" in t):
        # PK 字符串(UUID 类)走 uuid_short
        return ("uuid_short", None, None)
    if any(k in t for k in ("BIGINT", "INT", "SMALLINT", "TINYINT")):
        # 数值列:量级按列名启发(amount/price → 1-10000,count → 0-1000,默认 1-10000)
        if any(k in name_lower for k in ("amount", "price", "fee", "cost")):
            return ("random_int", [1, 10000], None)
        if any(k in name_lower for k in ("count", "num", "qty")):
            return ("random_int", [0, 1000], None)
        return ("random_int", [1, 10000], None)
    if any(k in t for k in ("DECIMAL", "NUMERIC", "FLOAT", "DOUBLE")):
        return ("realistic", None, None)
    if any(k in t for k in ("DATETIME", "TIMESTAMP")):
        return ("timestamp", ["2026-01-01", "2026-12-31"], None)
    if "DATE" in t:
        return ("timestamp", ["2026-01-01", "2026-12-31"], None)
    if "ENUM" in t:
        # 解析 ENUM('a','b','c') 字面值 —— 用 raw(保大小写),avoid upper 把 'paid' 变 'PAID'
        m = re.search(r"ENUM\s*\((.*?)\)", raw, re.IGNORECASE)
        if m:
            vals = [v.strip().strip("'\"") for v in m.group(1).split(",")]
            return ("enum", None, vals)
        return ("realistic", None, None)
    # 默认字符串走 realistic(让 Faker/AI 填)
    return ("realistic", None, None)


def _db_type_to_dialect(db_type_value: str) -> str:
    """DatabaseType.value → scenario.dialect 字符串。"""
    m = {"MySQL": "mysql", "Oracle": "oracle", "DM": "dm", "DB2": "db2"}
    return m.get(db_type_value, db_type_value.lower())


def _dump_yml(scenario: Scenario, warnings: list[str]) -> str:
    """Scenario → yml 文本。顶部加注释 + 警告。"""
    body = yaml.safe_dump(
        scenario.model_dump(exclude_defaults=True, by_alias=True, exclude_none=True),
        default_flow_style=False, sort_keys=False, allow_unicode=True,
    )
    header_lines = [
        f"# Scenario auto-imported from datasource",
        f"# Edit then save to config/scenarios/{scenario.id}.yml",
        f"#",
        f"# TODO 1: 把 workloads[].sql 改成你要分析的真实慢 SQL",
        f"# TODO 2: 必要时调整 tables[].rows(默认从 information_schema 估算)",
        f"# TODO 3: 考虑加 anomalies 块模拟生产数据偏斜",
    ]
    if warnings:
        header_lines.append("#")
        header_lines.append("# Import warnings:")
        for w in warnings:
            header_lines.append(f"#   - {w}")
    return "\n".join(header_lines) + "\n\n" + body
