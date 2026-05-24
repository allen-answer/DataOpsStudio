"""S1.C：datasource introspection —— 从活的 db 连接拉表的真实字段列表。

跟 lineage-based `services/assets.get_table_columns()` 互补：
- get_table_columns：从 workflow_run 反查"哪些字段被读 / 写过"，覆盖范围 = 历史
  跑过的 lineage 任务，**不需要活的 DB 连接**
- introspect_columns：从 information_schema 直接拉，覆盖范围 = 表当前的真实字段，
  **需要可访问的 DB 连接 + 用户挑哪个 datasource**

两边交叉对比让用户找到"从来没被动过"的字段（可能是死数据 / 未利用列）。

支持的方言：
- MySQL：information_schema.COLUMNS（dev / 测试已验证）
- Oracle / DM：all_tab_columns（结构相同，DM 兼容 Oracle 大部分语法 → 共用）
- DB2：SYSIBM.SYSCOLUMNS（待验证；先放着，遇到 bug 再调）

简单 in-memory 缓存：(datasource_id, table_name) → result，TTL 300s。同表频
繁拉不重复打 DB；admin 改 schema 后等 5min 自动失效或 restart。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from app.dbclients import factory as dbclients_factory
from app.dbclients.dialects import get_dialect
from app.models import DatabaseType
from app.services.repositories import datasource_store


logger = logging.getLogger(__name__)


_CACHE_TTL = 300.0
_cache: dict[tuple[str, str], tuple[float, list[dict[str, Any]]]] = {}
_cache_lock = threading.Lock()


def _split_schema_table(name: str) -> tuple[str, str]:
    """`ods.t_users` → ('ods', 't_users')；`t_users` → ('', 't_users')。
    Oracle DBLink `tab@dblink` 先剥 dblink。"""
    bare = name.split("@")[0].strip()
    if "." in bare:
        schema, table = bare.rsplit(".", 1)
        return schema.strip(), table.strip()
    return "", bare


def _columns_sql(db_type: DatabaseType, schema: str, table: str) -> tuple[str, list[Any]]:
    """根据方言返回 (SQL, params)。

    实际上 dbclients/factory 的 fetch_rows 不接受 params —— 全部通过字符串拼接 SQL。
    所以这里 params 永远空（保持 tuple 形状向后兼容），SQL 里 schema / table 已 inline。
    schema / table 经过 strict allowlist 校验防注入，dialect 子类可放心 inline。

    实际 SQL 生成委托给 `app.dbclients.dialects.get_dialect(db_type)`。
    """
    _validate_identifier(table)
    if schema:
        _validate_identifier(schema)

    try:
        dialect = get_dialect(db_type)
    except ValueError as exc:
        # 兼容旧错误消息：introspection not supported for db_type=...
        raise ValueError(f"introspection not supported for db_type={db_type.value}") from exc
    return dialect.introspect_columns_sql(schema, table), []


def _validate_identifier(s: str) -> None:
    """schema / table 名只允许字母 + 数字 + $ + _ + .。防 SQL 注入。"""
    if not s:
        raise ValueError("identifier cannot be empty")
    for ch in s:
        if not (ch.isalnum() or ch in "_$."):
            raise ValueError(
                f"identifier {s!r} contains invalid character {ch!r}; "
                "only alphanumeric / _ / $ / . allowed"
            )


def _normalize_nullable(raw: Any) -> bool:
    """各方言 nullable 列形态不同，归一化成 bool（True = 可空）。"""
    if isinstance(raw, bool):
        return raw
    s = str(raw).strip().upper()
    return s in {"YES", "Y", "1", "TRUE", "T"}


def introspect_columns(
    datasource_id: str,
    table_name: str,
    *,
    use_cache: bool = True,
) -> list[dict[str, Any]]:
    """连 datasource，从 information_schema / all_tab_columns 拉表的字段定义。

    返回 `[{name, data_type, nullable: bool, comment, ordinal}]`，按 ordinal 升序。

    raises:
    - ValueError：datasource 不存在 / table_name 非法 / db_type 不支持
    - DbClientError：连接 / SQL 执行失败（来自 dbclients 包装）
    """
    if not datasource_id:
        raise ValueError("datasource_id is required")
    if not table_name or not table_name.strip():
        raise ValueError("table_name is required")
    table_name = table_name.strip()

    cache_key = (datasource_id, table_name)
    if use_cache:
        with _cache_lock:
            entry = _cache.get(cache_key)
            if entry and (time.time() - entry[0]) < _CACHE_TTL:
                return [dict(r) for r in entry[1]]  # 防 caller 改

    source = datasource_store.get(datasource_id)
    if source is None:
        raise ValueError(f"datasource {datasource_id} not found")

    schema, table = _split_schema_table(table_name)
    sql, _params = _columns_sql(source.db_type, schema, table)
    logger.info(
        "introspect columns: datasource=%s db_type=%s schema=%s table=%s",
        source.name, source.db_type.value, schema, table,
    )

    rows = dbclients_factory.fetch_rows(source, sql, max_rows=2000)
    out: list[dict[str, Any]] = []
    for r in rows:
        # column 名都是大写，跟 cursor 实际拿到的 lower / upper 不一致，做容错
        def pick(key_options: list[str]) -> Any:
            for k in key_options:
                if k in r:
                    return r[k]
            return None
        name = pick(["name", "NAME", "Name"])
        if not name:
            continue
        out.append({
            "name": str(name),
            "data_type": str(pick(["data_type", "DATA_TYPE"]) or ""),
            "nullable": _normalize_nullable(pick(["nullable", "NULLABLE"])),
            "comment": str(pick(["comment", "COMMENT", "comments", "COMMENTS"]) or ""),
            "ordinal": int(pick(["ordinal", "ORDINAL"]) or 0),
        })
    out.sort(key=lambda c: c["ordinal"])

    if use_cache:
        with _cache_lock:
            _cache[cache_key] = (time.time(), [dict(r) for r in out])

    return out


def introspect_indexes(
    datasource_id: str, table_name: str,
) -> list[dict[str, Any]]:
    """拉表的索引定义,Phase 14 P1-1 给 yml_importer 反向生成 IndexDef 用。

    返回 `[{name, columns: [...], unique: bool, is_pk: bool}]`,按 (is_pk, name) 排序。

    各方言走自身系统表:
    - MySQL: `SHOW INDEX FROM table`,行级 (index, seq, column)
    - Oracle / DM: `USER_INDEXES` + `USER_IND_COLUMNS` join,DM 兼容 Oracle 数据字典
    - DB2: `SYSCAT.INDEXES` + `SYSCAT.INDEXCOLUSE` join

    任一方言查询失败 / 表不存在 → 返 [](best-effort,不阻塞 yml_importer)。
    """
    if not table_name or not datasource_id:
        return []
    source = datasource_store.get(datasource_id)
    if source is None:
        raise ValueError(f"datasource {datasource_id} not found")
    schema, table = _split_schema_table(table_name)
    _validate_identifier(table)
    if schema:
        _validate_identifier(schema)
    db_type = source.db_type
    try:
        if db_type == DatabaseType.MYSQL:
            return _introspect_indexes_mysql(source, schema, table)
        if db_type in (DatabaseType.ORACLE, DatabaseType.DM):
            return _introspect_indexes_oracle(source, schema, table)
        if db_type == DatabaseType.DB2:
            return _introspect_indexes_db2(source, schema, table)
    except Exception as exc:
        logger.warning("introspect_indexes failed for %s: %s", table_name, exc)
        return []
    return []


def _introspect_indexes_mysql(source: Any, schema: str, table: str) -> list[dict[str, Any]]:
    """MySQL `SHOW INDEX FROM ...`,按 index 名 group + 按 seq 排列。"""
    qfull = f"`{schema}`.`{table}`" if schema else f"`{table}`"
    rows = dbclients_factory.fetch_rows(source, f"SHOW INDEX FROM {qfull}", max_rows=500)
    by_name: dict[str, dict[str, Any]] = {}
    for r in rows:
        idx_name = r.get("Key_name") or r.get("KEY_NAME") or ""
        if not idx_name:
            continue
        entry = by_name.setdefault(idx_name, {
            "name": idx_name,
            "columns": [],
            "unique": int(r.get("Non_unique", 1)) == 0,
            "is_pk": idx_name.upper() == "PRIMARY",
            "_seqs": [],
        })
        col = r.get("Column_name") or r.get("COLUMN_NAME") or ""
        seq = int(r.get("Seq_in_index", 0) or 0)
        if col:
            entry["_seqs"].append((seq, col))
    return _finalize_index_entries(by_name)


def _introspect_indexes_oracle(source: Any, schema: str, table: str) -> list[dict[str, Any]]:
    """Oracle / DM 走 ALL_INDEXES + ALL_IND_COLUMNS join。

    `index_type`:'NORMAL' / 'FUNCTION-BASED NORMAL' / 'BITMAP' 等,只保 NORMAL +
    FUNCTION-BASED(其它如 LOB / DOMAIN 不属"列上的 B-tree"语义)。

    is_pk 判断:ALL_CONSTRAINTS 里 constraint_type='P' 对应同名索引(Oracle 自动建)。
    避免再 query 一次,用 `index_name = constraint_name` 启发(常见但不绝对)。
    保守做法:走 ALL_CONSTRAINTS 二次 query 确认。
    """
    where = ["i.table_name = UPPER(:table)"]
    if schema:
        # Oracle 的 OWNER 是 schema 概念;DM 兼容
        where.append("i.owner = UPPER(:schema)")
    # 主索引列查询:不用 placeholder,因为不同 driver 占位符语法差异大,inline 安全的
    # 字面值(table/schema 已过 _validate_identifier 白名单)
    t = table.upper().replace("'", "''")
    s = schema.upper().replace("'", "''")
    schema_cond_idx = f" AND i.owner = '{s}'" if schema else ""
    schema_cond_col = f" AND c.table_owner = '{s}'" if schema else ""
    sql = (
        "SELECT i.index_name AS idx_name, i.uniqueness AS uniqueness, "
        "  c.column_name AS col_name, c.column_position AS col_pos "
        "FROM ALL_INDEXES i "
        "JOIN ALL_IND_COLUMNS c ON c.index_name = i.index_name "
        f" AND c.table_name = i.table_name {schema_cond_col} "
        f"WHERE i.table_name = '{t}'{schema_cond_idx} "
        "AND i.index_type IN ('NORMAL', 'FUNCTION-BASED NORMAL') "
        "ORDER BY i.index_name, c.column_position"
    )
    rows = dbclients_factory.fetch_rows(source, sql, max_rows=500)
    by_name: dict[str, dict[str, Any]] = {}
    for r in rows:
        idx_name = r.get("IDX_NAME") or r.get("idx_name") or ""
        if not idx_name:
            continue
        unique = str(r.get("UNIQUENESS") or r.get("uniqueness") or "").upper() == "UNIQUE"
        entry = by_name.setdefault(idx_name, {
            "name": idx_name,
            "columns": [],
            "unique": unique,
            "is_pk": False,  # 二次 query 标记
            "_seqs": [],
        })
        col = r.get("COL_NAME") or r.get("col_name") or ""
        pos = int(r.get("COL_POS") or r.get("col_pos") or 0)
        if col:
            entry["_seqs"].append((pos, col))
    # is_pk 标记:ALL_CONSTRAINTS 里 constraint_type='P' 的 index_name
    pk_sql = (
        "SELECT index_name FROM ALL_CONSTRAINTS "
        f"WHERE table_name = '{t}'{schema_cond_idx.replace('i.owner', 'owner')} "
        "AND constraint_type = 'P' AND index_name IS NOT NULL"
    )
    try:
        pk_rows = dbclients_factory.fetch_rows(source, pk_sql, max_rows=10)
        pk_names = {
            (r.get("INDEX_NAME") or r.get("index_name") or "") for r in pk_rows
        }
        for idx_name in pk_names:
            if idx_name in by_name:
                by_name[idx_name]["is_pk"] = True
    except Exception as exc:
        logger.warning("oracle PK introspection failed (skipped): %s", exc)
    return _finalize_index_entries(by_name)


def _introspect_indexes_db2(source: Any, schema: str, table: str) -> list[dict[str, Any]]:
    """DB2 走 SYSCAT.INDEXES + SYSCAT.INDEXCOLUSE join。

    `uniquerule`:'U' = unique 索引,'P' = primary key 索引,'D' = duplicate(普通)。
    `colorder`:'A' / 'D'(asc / desc),拿来排 column position 不关心方向,只用 colseq。
    """
    t = table.upper().replace("'", "''")
    s = schema.upper().replace("'", "''")
    schema_cond = f" AND i.tabschema = '{s}'" if schema else ""
    sql = (
        "SELECT i.indname AS idx_name, i.uniquerule AS uniquerule, "
        "  c.colname AS col_name, c.colseq AS col_pos "
        "FROM SYSCAT.INDEXES i "
        "JOIN SYSCAT.INDEXCOLUSE c ON c.indname = i.indname AND c.indschema = i.indschema "
        f"WHERE i.tabname = '{t}'{schema_cond} "
        "ORDER BY i.indname, c.colseq"
    )
    rows = dbclients_factory.fetch_rows(source, sql, max_rows=500)
    by_name: dict[str, dict[str, Any]] = {}
    for r in rows:
        idx_name = r.get("IDX_NAME") or r.get("idx_name") or ""
        if not idx_name:
            continue
        rule = str(r.get("UNIQUERULE") or r.get("uniquerule") or "").upper()
        entry = by_name.setdefault(idx_name, {
            "name": idx_name,
            "columns": [],
            "unique": rule in ("U", "P"),  # P 也是 unique(PK)
            "is_pk": rule == "P",
            "_seqs": [],
        })
        col = r.get("COL_NAME") or r.get("col_name") or ""
        pos = int(r.get("COL_POS") or r.get("col_pos") or 0)
        if col:
            entry["_seqs"].append((pos, col))
    return _finalize_index_entries(by_name)


def _finalize_index_entries(by_name: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """共用收尾:按 seq 排好 columns,PK 优先排在前面。"""
    out: list[dict[str, Any]] = []
    for entry in by_name.values():
        entry["columns"] = [c for _, c in sorted(entry["_seqs"])]
        entry.pop("_seqs", None)
        out.append(entry)
    return sorted(out, key=lambda x: (not x["is_pk"], x["name"]))


def introspect_row_count(
    datasource_id: str, table_name: str,
) -> int | None:
    """拉表的近似行数 —— yml_importer 给 scenario.tables[].rows 当默认。

    MySQL: `information_schema.TABLES.TABLE_ROWS`(InnoDB 估算,可能差几十%)
    Oracle / DM: `USER_TABLES.NUM_ROWS`(需要 ANALYZE 过)
    DB2: `SYSCAT.TABLES.CARD`(需要 RUNSTATS 过)

    返 None 表示拿不到(表不存在 / 没统计信息 / 不支持的方言)。
    """
    source = datasource_store.get(datasource_id)
    if source is None:
        return None
    schema, table = _split_schema_table(table_name)
    _validate_identifier(table)
    if schema:
        _validate_identifier(schema)
    if source.db_type == DatabaseType.MYSQL:
        cond = f"TABLE_NAME = '{table}'"
        if schema:
            cond += f" AND TABLE_SCHEMA = '{schema}'"
        sql = f"SELECT TABLE_ROWS AS n FROM information_schema.TABLES WHERE {cond} LIMIT 1"
    elif source.db_type in (DatabaseType.ORACLE, DatabaseType.DM):
        cond = f"TABLE_NAME = UPPER('{table}')"
        if schema:
            cond += f" AND OWNER = UPPER('{schema}')"
        sql = f"SELECT NUM_ROWS AS n FROM ALL_TABLES WHERE {cond}"
    elif source.db_type == DatabaseType.DB2:
        cond = f"TABNAME = UPPER('{table}')"
        if schema:
            cond += f" AND TABSCHEMA = UPPER('{schema}')"
        sql = f"SELECT CARD AS n FROM SYSCAT.TABLES WHERE {cond}"
    else:
        return None
    try:
        rows = dbclients_factory.fetch_rows(source, sql, max_rows=1)
    except Exception as exc:
        logger.warning("introspect_row_count failed for %s: %s", table_name, exc)
        return None
    if not rows:
        return None
    n = rows[0].get("n") or rows[0].get("N") or rows[0].get("NUM_ROWS")
    try:
        return int(n) if n is not None else None
    except (TypeError, ValueError):
        return None


def invalidate_cache(datasource_id: str = "", table_name: str = "") -> None:
    """显式失效缓存。任一参数为空 = 全部失效。"""
    with _cache_lock:
        if not datasource_id and not table_name:
            _cache.clear()
            return
        keys_to_del = [
            k for k in _cache.keys()
            if (not datasource_id or k[0] == datasource_id)
            and (not table_name or k[1] == table_name)
        ]
        for k in keys_to_del:
            _cache.pop(k, None)


__all__ = ["introspect_columns", "invalidate_cache"]
