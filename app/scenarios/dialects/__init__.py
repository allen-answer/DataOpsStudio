"""Scenario materialize dialects —— SQL plan 生成的方言抽象。

跟 `app/dbclients/dialects/` 不同：那一套是 DB 连接 / introspection 相关；
这里只关心 DDL + INSERT 怎么写（标识符引用 / 占位符 / CREATE DATABASE 是否
适用 / DROP TABLE IF EXISTS 是否原生支持）。

注册：每个新方言加一个子类 + 在 `_REGISTRY` 写一行 mapping。DM 跟 Oracle
高度兼容，直接复用 Oracle 实现。
"""
from __future__ import annotations

from app.scenarios.dialects.base import MaterializeDialect
from app.scenarios.dialects.mysql import MysqlMaterializeDialect
from app.scenarios.dialects.oracle import OracleMaterializeDialect


_REGISTRY: dict[str, MaterializeDialect] = {
    "mysql": MysqlMaterializeDialect(),
    "oracle": OracleMaterializeDialect(),
    "dm": OracleMaterializeDialect(),  # 达梦兼容 Oracle，整套 DDL 形态一致
}


def get_dialect(name: str) -> MaterializeDialect:
    d = _REGISTRY.get((name or "").lower())
    if d is None:
        raise NotImplementedError(
            f"materializer 不支持方言 {name!r}；已知方言：{', '.join(sorted(_REGISTRY))}"
        )
    return d


__all__ = ["MaterializeDialect", "get_dialect"]
