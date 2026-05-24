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

    def analyze_table_sql(self, qfull: str) -> str | None:
        # Oracle / DM:DBMS_STATS.GATHER_TABLE_STATS 收集表统计 + 直方图,
        # 等价 MySQL ANALYZE TABLE。schema 跟 table 名要分开传(不是 qfull),
        # 但 qfull 形如 `"USER"."TBL"` —— 解析出来传给 GATHER_TABLE_STATS。
        # 失败兜底:如果 qfull 不含 schema(裸表名),用 USER 当前 schema。
        parts = qfull.split(".")
        if len(parts) == 2:
            schema = parts[0].strip('"').replace("'", "''")
            table = parts[1].strip('"').replace("'", "''")
        else:
            # 裸名:让 DBMS_STATS 用 USER 推
            return (
                f"BEGIN DBMS_STATS.GATHER_TABLE_STATS(USER, "
                f"'{parts[0].strip(chr(34))}'); END;"
            )
        return (
            f"BEGIN DBMS_STATS.GATHER_TABLE_STATS('{schema}', '{table}'); END;"
        )
