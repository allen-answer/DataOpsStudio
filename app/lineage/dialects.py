from __future__ import annotations


# Maps user-facing dialect names (incl. DM / OceanBase modes) to sqlglot dialects.
# DM 默认走 oracle 语法兼容；OB 区分 mysql / oracle 模式。
_DIALECT_ALIASES = {
    "dm": "oracle",
    "dameng": "oracle",
    "ob": "mysql",
    "ob_mysql": "mysql",
    "obmysql": "mysql",
    "oceanbase": "mysql",
    "oceanbase_mysql": "mysql",
    "ob_oracle": "oracle",
    "oboracle": "oracle",
    "oceanbase_oracle": "oracle",
}


def resolve_dialect(dialect: str | None) -> str | None:
    if not dialect:
        return None
    key = dialect.strip().lower()
    if not key:
        return None
    return _DIALECT_ALIASES.get(key, key)
