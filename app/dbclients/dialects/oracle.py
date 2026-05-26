"""Oracle Dialect。

DM 跟 Oracle 共享 `introspect_columns_sql` / `connection_test_sql`，但
**`connect()` 不一样**：DM 用 dmPython 驱动，签名是 positional 或 kwarg，
还要落 schema option + 两次调用兜底。所以 `dm.py` 用独立子类只 override
connect，其他方法继承 OracleDialect。
"""
from __future__ import annotations

import importlib
from typing import Any

from app.dbclients.dialects import register
from app.dbclients.dialects.base import Dialect
from app.models import DataSource, DatabaseType


class OracleDialect(Dialect):
    name = "oracle"

    def connection_test_sql(self) -> str:
        return "select 1 as ok from dual"

    def apply_call_timeout(self, conn: Any, seconds: float) -> bool:
        # oracledb / cx_Oracle Connection.callTimeout —— 单位毫秒，作用于所有
        # 后续 round-trip(execute / fetch)，超过即驱动抛 DPI-1067。dmPython 也
        # 基于 DB-API，多数版本兼容；不兼容会 AttributeError，由 caller 吞掉。
        try:
            conn.callTimeout = int(seconds * 1000)
            return True
        except (AttributeError, TypeError):
            return False

    def estimate_rows_from_explain(self, conn: Any, sql: str) -> int | None:
        # Oracle/DM 走 `EXPLAIN PLAN SET STATEMENT_ID='X' FOR <sql>` 两步:
        # 1. EXPLAIN PLAN 把 plan 写进 SYS.PLAN_TABLE(全用户共享表),用 statement_id
        #    隔离并发(多个 preflight 同时跑不会污染彼此)
        # 2. SELECT MAX(cardinality) FROM PLAN_TABLE WHERE statement_id=...
        #    cardinality 是优化器估算的输出行数,跟 MySQL `rows` 列同义
        # 3. DELETE 清理本次 STATEMENT_ID 的所有行(防 PLAN_TABLE 累积膨胀)
        #
        # 失败任一步返 None,caller 安全降级。EXPLAIN PLAN 本身不消耗 DML quota,
        # 不会进 redo log。多数 Oracle DB 有默认 PLAN_TABLE_$;早版本需 DBA 建
        # —— 错那种环境就 fallback None。
        # statement_id 我们自己生成,纯 hex(uuid.uuid4().hex 只含 0-9a-f)+ 固
        # 定前缀,SQL 注入面=0 —— 直接内联,避免不同 driver(cx_Oracle/oracledb
        # vs dmPython)对 bind 语法的差异(:name / :1 / ?)
        import uuid
        statement_id = f"dataops_preflight_{uuid.uuid4().hex[:16]}"
        cursor = None
        try:
            cursor = conn.cursor()
            cursor.execute(f"EXPLAIN PLAN SET STATEMENT_ID = '{statement_id}' FOR {sql}")
            cursor.execute(
                f"SELECT MAX(cardinality) FROM PLAN_TABLE WHERE statement_id = '{statement_id}'"
            )
            row = cursor.fetchone()
            estimated = row[0] if row else None
            if estimated is None:
                return None
            try:
                return int(estimated)
            except (TypeError, ValueError):
                return None
        except Exception:
            return None
        finally:
            # cleanup: 删自己 statement_id 的 PLAN_TABLE 行(吞掉失败,避免淹没原始错)
            if cursor is not None:
                try:
                    cursor.execute(
                        f"DELETE FROM PLAN_TABLE WHERE statement_id = '{statement_id}'"
                    )
                    conn.commit()
                except Exception:
                    pass
                try:
                    cursor.close()
                except Exception:
                    pass

    def connect(self, source: DataSource, module_name: str) -> Any:
        # oracledb / cx_Oracle 都接 user/password/dsn 三参数；dsn 优先用
        # extra.dsn（让用户自己写 TNS / Easy Connect），否则按 host:port/service 拼
        driver = importlib.import_module(module_name)
        dsn = source.extra.get("dsn") or f"{source.host}:{source.port}/{source.database}"
        options: dict[str, Any] = {}
        if "tcp_connect_timeout" in source.extra:
            options["tcp_connect_timeout"] = source.extra["tcp_connect_timeout"]
        return driver.connect(user=source.username, password=source.password, dsn=dsn, **options)

    def list_indexes_sql(self, schema: str, table: str) -> str | None:
        # all_indexes + all_ind_columns 联合,一行一(索引,列)。
        # UNIQUENESS = 'UNIQUE' / 'NONUNIQUE'(原始字符串),前端转 non_unique flag。
        if schema:
            return (
                "SELECT i.INDEX_NAME AS index_name, c.COLUMN_NAME AS column_name, "
                "CASE WHEN i.UNIQUENESS = 'UNIQUE' THEN 0 ELSE 1 END AS non_unique, "
                "c.COLUMN_POSITION AS seq_in_index, i.INDEX_TYPE AS index_type "
                "FROM all_indexes i "
                "JOIN all_ind_columns c "
                "  ON c.INDEX_OWNER = i.OWNER AND c.INDEX_NAME = i.INDEX_NAME "
                f"WHERE i.TABLE_OWNER = UPPER('{schema}') "
                f"  AND i.TABLE_NAME = UPPER('{table}') "
                "ORDER BY i.INDEX_NAME, c.COLUMN_POSITION"
            )
        return (
            "SELECT i.INDEX_NAME AS index_name, c.COLUMN_NAME AS column_name, "
            "CASE WHEN i.UNIQUENESS = 'UNIQUE' THEN 0 ELSE 1 END AS non_unique, "
            "c.COLUMN_POSITION AS seq_in_index, i.INDEX_TYPE AS index_type "
            "FROM all_indexes i "
            "JOIN all_ind_columns c "
            "  ON c.INDEX_OWNER = i.OWNER AND c.INDEX_NAME = i.INDEX_NAME "
            f"WHERE i.TABLE_NAME = UPPER('{table}') "
            "ORDER BY i.TABLE_OWNER, i.INDEX_NAME, c.COLUMN_POSITION"
        )

    def list_views_sql(self, schema: str) -> str | None:
        if schema:
            return (
                "SELECT VIEW_NAME AS name FROM all_views "
                f"WHERE OWNER = UPPER('{schema}') ORDER BY VIEW_NAME"
            )
        return (
            "SELECT VIEW_NAME AS name FROM user_views ORDER BY VIEW_NAME"
        )

    # Oracle DDL 走 DBMS_METADATA.GET_DDL 返 CLOB,驱动 LOB 处理复杂(需 outputtypehandler
    # 配置),且需要 EXECUTE 权限 + SELECT_CATALOG_ROLE,生产环境失败率高。
    # 暂留 None(继承 base),前端会显示「该方言不支持 DDL,请在 DB 客户端工具查看」。
    # 若未来真要,可基于 ALL_TAB_COLUMNS / ALL_CONSTRAINTS / ALL_INDEXES 拼合成 DDL。

    def introspect_columns_sql(self, schema: str, table: str) -> str:
        # NULLABLE 是 'Y' / 'N'；comments 在 all_col_comments 里需 join
        # Oracle 标识符默认大写 → 用 UPPER() 包 schema/table
        if schema:
            return (
                "SELECT c.COLUMN_NAME AS name, c.DATA_TYPE AS data_type, "
                "c.NULLABLE AS nullable, cc.COMMENTS AS comment, "
                "c.COLUMN_ID AS ordinal "
                "FROM all_tab_columns c "
                "LEFT JOIN all_col_comments cc ON cc.OWNER = c.OWNER "
                "  AND cc.TABLE_NAME = c.TABLE_NAME "
                "  AND cc.COLUMN_NAME = c.COLUMN_NAME "
                f"WHERE c.OWNER = UPPER('{schema}') AND c.TABLE_NAME = UPPER('{table}') "
                "ORDER BY c.COLUMN_ID"
            )
        return (
            "SELECT c.COLUMN_NAME AS name, c.DATA_TYPE AS data_type, "
            "c.NULLABLE AS nullable, cc.COMMENTS AS comment, "
            "c.COLUMN_ID AS ordinal "
            "FROM all_tab_columns c "
            "LEFT JOIN all_col_comments cc ON cc.OWNER = c.OWNER "
            "  AND cc.TABLE_NAME = c.TABLE_NAME "
            "  AND cc.COLUMN_NAME = c.COLUMN_NAME "
            f"WHERE c.TABLE_NAME = UPPER('{table}') "
            "ORDER BY c.OWNER, c.COLUMN_ID"
        )


register(DatabaseType.ORACLE, OracleDialect())
