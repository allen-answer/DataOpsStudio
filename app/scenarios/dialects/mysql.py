"""MySQL materialize dialect。"""
from __future__ import annotations

from app.scenarios.dialects.base import MaterializeDialect


class MysqlMaterializeDialect(MaterializeDialect):
    name = "mysql"

    # MySQL 8 server default = utf8mb4_0900_ai_ci,旧库 / init 脚本常用 unicode_ci。
    # 同一 scenario 内多个 schema 如果 collation 不一致,JOIN 字符串字段会炸
    # `Illegal mix of collations` (1267)。统一显式 unicode_ci:跨 5.7 / 8.0 都支持,
    # 与历史 demo-db / init_db 预建 schema 自然对齐。
    DEFAULT_COLLATION = "utf8mb4_unicode_ci"

    def quote_identifier(self, ident: str) -> str:
        # 反引号包裹，反引号自身 double 转义
        return "`" + ident.replace("`", "``") + "`"

    def schema_create_sql(self, schema: str) -> str | None:
        return (
            f"CREATE DATABASE IF NOT EXISTS {self.quote_identifier(schema)}"
            f" CHARACTER SET utf8mb4 COLLATE {self.DEFAULT_COLLATION}"
        )

    def create_table_sql(self, qfull, columns):
        # 兜底:即使 schema 已被外部用其它 collation 预建过,新建的表也强制
        # CHARSET=utf8mb4 + COLLATE=unicode_ci,保证同一 scenario 内 JOIN 字符串
        # 字段 collation 一致(1267 防线)。
        base = super().create_table_sql(qfull, columns)
        return base + f" DEFAULT CHARSET=utf8mb4 COLLATE={self.DEFAULT_COLLATION}"

    def drop_table_sql(self, qfull: str) -> str:
        return f"DROP TABLE IF EXISTS {qfull}"

    def placeholder(self, index: int) -> str:
        return "%s"

    def analyze_table_sql(self, qfull: str) -> str | None:
        # MySQL `ANALYZE TABLE` 更新 InnoDB 统计信息(随机采样,默认 20 页)。
        # 5.6+ 加 persistent stats 后,统计落到 mysql.innodb_table_stats /
        # innodb_index_stats,优化器估算才接近真实。
        return f"ANALYZE TABLE {qfull}"
