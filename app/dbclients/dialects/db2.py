"""DB2 Dialect。"""
from __future__ import annotations

import importlib
from typing import Any

from app.dbclients.dialects import register
from app.dbclients.dialects.base import CONNECT_TIMEOUT_SECONDS, Dialect
from app.dbclients.drivers import add_db2_dll_directories
from app.models import DataSource, DatabaseType


class Db2Dialect(Dialect):
    name = "db2"

    def connection_test_sql(self) -> str:
        return "select 1 as ok from sysibm.sysdummy1"

    def estimate_rows_from_explain(self, conn: Any, sql: str) -> int | None:
        # DB2 走 `EXPLAIN PLAN FOR <sql>` 写 EXPLAIN_STATEMENT/EXPLAIN_OPERATOR
        # 表(默认 schema 同当前用户),取 EXPLAIN_OPERATOR.TOTAL_COST 估算行数;
        # 但 TOTAL_COST 是优化器单位(timerons)不是行数 —— 用 STREAM_COUNT
        # (operator 间输出 stream 行数估算)更接近 MySQL/Oracle 的 "rows" 语义。
        #
        # 需要 EXPLAIN tables 已建(`db2 -tvf $DB2HOME/sqllib/misc/EXPLAIN.DDL`)。
        # 没建会 SQL0204N → return None 安全降级,不阻塞真查询。
        #
        # ibm_db 不在 build 默认装 → 直接走 cursor.execute 路径(driver 模块由
        # caller pool.borrow 时 import 好,这里只用 cursor)。
        import uuid
        token = f"DATAOPS_PF_{uuid.uuid4().hex[:16].upper()}"
        cursor = None
        try:
            cursor = conn.cursor()
            # SET CURRENT EXPLAIN MODE 旧 EXPLAIN tables 直接 EXPLAIN PLAN FOR
            cursor.execute(f"EXPLAIN PLAN FOR {sql}")
            # EXPLAIN_OPERATOR.STREAM_COUNT 是 operator 间传输的预估行数
            # 取整个 plan 中 max stream count(同 MySQL/Oracle 的 max-row-step 逻辑)
            cursor.execute(
                "SELECT MAX(STREAM_COUNT) FROM EXPLAIN_STREAM "
                "WHERE EXPLAIN_REQUESTER = CURRENT_USER "
                "AND EXPLAIN_TIME = (SELECT MAX(EXPLAIN_TIME) FROM EXPLAIN_STREAM "
                "WHERE EXPLAIN_REQUESTER = CURRENT_USER)"
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
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:
                    pass

    def apply_call_timeout(self, conn: Any, seconds: float) -> bool:
        # DB2 走 `ibm_db.set_option(conn_handle, {SQL_ATTR_QUERY_TIMEOUT: sec}, 1)`
        # —— 1 表示连接级选项,影响该连接所有后续 cursor.execute。
        # ibm_db_dbi.Connection 暴露底层 handle 在 `.conn_handler`(老版本)/
        # `.conn_handle`(新版本)—— 两个名都试。SQL_ATTR_QUERY_TIMEOUT 单位
        # 是秒,跟我们 caller 参数一致,不必换算。
        # build 不装 ibm_db 时 import 直接抛 ImportError,返 False 让 factory 知
        # 道没生效(也没 SQL fallback,行为退化为「不超时」与本切片前一致)。
        try:
            import ibm_db  # type: ignore
        except ImportError:
            return False
        handle = getattr(conn, "conn_handler", None) or getattr(conn, "conn_handle", None)
        if handle is None:
            return False
        try:
            ibm_db.set_option(
                handle,
                {ibm_db.SQL_ATTR_QUERY_TIMEOUT: int(seconds)},
                1,  # 1 = connection-level option
            )
            return True
        except Exception:
            return False

    def connect(self, source: DataSource, module_name: str) -> Any:
        # ibm_db_dbi 是 DB-API wrapper（包了 ibm_db），ibm_db 直接走 C 扩展。
        # 我们要的是 cursor()/execute() 兼容 DB-API，所以无论 first_available_module
        # 选了哪个，最终都用 ibm_db_dbi。
        add_db2_dll_directories()
        if module_name == "ibm_db_dbi":
            driver = importlib.import_module(module_name)
        else:
            import ibm_db_dbi as driver  # type: ignore
        conn_str = source.extra.get("conn_str") or (
            f"DATABASE={source.database};HOSTNAME={source.host};PORT={source.port};"
            f"PROTOCOL=TCPIP;UID={source.username};PWD={source.password};"
        )
        if "CONNECTTIMEOUT" not in conn_str.upper():
            conn_str += f"CONNECTTIMEOUT={int(source.extra.get('connect_timeout', CONNECT_TIMEOUT_SECONDS))};"
        return driver.connect(conn_str, "", "")

    def introspect_columns_sql(self, schema: str, table: str) -> str:
        if schema:
            return (
                "SELECT NAME AS name, COLTYPE AS data_type, NULLS AS nullable, "
                "REMARKS AS comment, COLNO AS ordinal "
                "FROM SYSIBM.SYSCOLUMNS "
                f"WHERE TBCREATOR = UPPER('{schema}') AND TBNAME = UPPER('{table}') "
                "ORDER BY COLNO"
            )
        return (
            "SELECT NAME AS name, COLTYPE AS data_type, NULLS AS nullable, "
            "REMARKS AS comment, COLNO AS ordinal "
            "FROM SYSIBM.SYSCOLUMNS "
            f"WHERE TBNAME = UPPER('{table}') "
            "ORDER BY COLNO"
        )

    def bulk_columns_sql(self, schema: str) -> str | None:
        if not schema:
            return None
        return (
            "SELECT TBNAME AS table_name, NAME AS name, COLTYPE AS data_type, "
            "NULLS AS nullable, REMARKS AS comment, COLNO AS ordinal "
            "FROM SYSIBM.SYSCOLUMNS "
            f"WHERE TBCREATOR = UPPER('{schema}') "
            "ORDER BY TBNAME, COLNO"
        )


register(DatabaseType.DB2, Db2Dialect())
