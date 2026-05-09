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
