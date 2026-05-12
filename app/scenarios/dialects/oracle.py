"""Oracle materialize dialect（DM 也走这套，dialects/__init__.py 注册 DM→Oracle）。

DM 跟 Oracle 在 DDL / 数据字典 / PL/SQL 块语法都兼容，沿用同一份。如果将来
DM 出现差异（如 dmPython placeholder style）再单独建 DmMaterializeDialect 子类。

差异点跟 MySQL：
- 标识符用 `"`（双引号）包裹；空 quote 时 Oracle 默认 UPPER，所以 yml 写
  小写表名 generator + materializer 全程 quote 后才能跟 SELECT 时保持一致
- 没有 `CREATE DATABASE IF NOT EXISTS schema`（schema = user，要 admin 单独建）
- 没有 `DROP TABLE IF EXISTS`，包 PL/SQL 块吞 ORA-00942（table not found）
- INSERT 占位符用 `:1, :2, ...`（cx_Oracle / oracledb / dmPython 都支持）
"""
from __future__ import annotations

from app.scenarios.dialects.base import MaterializeDialect


class OracleMaterializeDialect(MaterializeDialect):
    name = "oracle"

    def quote_identifier(self, ident: str) -> str:
        # Oracle 双引号；引号内的双引号 double 转义
        return '"' + ident.replace('"', '""') + '"'

    def schema_create_sql(self, schema: str) -> str | None:
        # Oracle schema=user，sandbox 流程不该擅自 CREATE USER
        return None

    def drop_table_sql(self, qfull: str) -> str:
        # qfull 已经被 quote_qualified 过，里面有双引号；包进 PL/SQL 字符串时
        # 单引号 escape（虽然双引号在单引号字符串里不冲突，仍走防御 escape）
        escaped = qfull.replace("'", "''")
        return (
            "BEGIN\n"
            f"  EXECUTE IMMEDIATE 'DROP TABLE {escaped}';\n"
            "EXCEPTION WHEN OTHERS THEN\n"
            "  IF SQLCODE != -942 THEN RAISE; END IF;\n"
            "END;"
        )

    def placeholder(self, index: int) -> str:
        # Oracle 编号占位符：`:1` 起步（cx_Oracle / oracledb / dmPython 都接）
        return f":{index + 1}"
