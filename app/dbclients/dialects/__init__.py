"""Dialect registry：`get_dialect(db_type)` 单一入口。

Phase 11 spike 的核心：`if db_type == DatabaseType.MYSQL: ...` 改成
`get_dialect(db_type).introspect_columns_sql(...)`。

注册策略：lazy import + 模块级单例。子类模块在 import 时调 `register("mysql", MysqlDialect())`
落 _REGISTRY，`get_dialect` 第一次被调时强制 import 全部子类模块。
"""
from __future__ import annotations

from app.dbclients.dialects.base import Dialect
from app.models import DatabaseType


_REGISTRY: dict[DatabaseType, Dialect] = {}
_LOADED = False


def register(db_type: DatabaseType, dialect: Dialect) -> None:
    _REGISTRY[db_type] = dialect


def _ensure_loaded() -> None:
    """首次调用时强制 import 全部子类模块，让 register() 落 _REGISTRY。

    用独立 flag 而不是 `if _REGISTRY:` —— 避免「test 先 import 单个 dialect 子模块
    （只 register 一个）→ _REGISTRY 非空 → _ensure_loaded 早退 → 其余子类没加载」。
    """
    global _LOADED
    if _LOADED:
        return
    # import 顺序无所谓，每个模块自己调 register()
    from app.dbclients.dialects import mysql as _mysql  # noqa: F401
    from app.dbclients.dialects import oracle as _oracle  # noqa: F401
    from app.dbclients.dialects import dm as _dm  # noqa: F401
    from app.dbclients.dialects import db2 as _db2  # noqa: F401
    _LOADED = True


def get_dialect(db_type: DatabaseType) -> Dialect:
    """按 DatabaseType 拿对应 Dialect 实例。未注册抛 ValueError。"""
    _ensure_loaded()
    if db_type not in _REGISTRY:
        raise ValueError(f"no dialect registered for db_type={db_type.value}")
    return _REGISTRY[db_type]


__all__ = ["Dialect", "get_dialect", "register"]
