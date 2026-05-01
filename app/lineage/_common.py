from __future__ import annotations

import re
from functools import lru_cache


@lru_cache(maxsize=8192)
def normalize_table_name(table: str) -> str:
    return table.strip().strip('"`[]').lower()


def unique_strings(items: object) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:  # type: ignore[union-attr]
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def is_alias_reference(table_name: str, aliases: set[str]) -> bool:
    return "." not in table_name and normalize_table_name(table_name) in aliases


_ALIAS_KEYWORDS = frozenset({
    "and", "connect", "cross", "full", "group", "having",
    "inner", "join", "left", "limit", "minus", "on",
    "order", "right", "union", "where",
})

_IDENTIFIER = r"[A-Za-z_][\w$#]*"
_QUALIFIED = rf"{_IDENTIFIER}(?:\s*\.\s*{_IDENTIFIER})*"
_RE_PAREN_ALIAS = re.compile(rf"\)\s+(?:AS\s+)?({_IDENTIFIER})\b", re.IGNORECASE)
_RE_FROM_JOIN_ALIAS = re.compile(rf"\b(?:FROM|JOIN)\s+{_QUALIFIED}\s+(?:AS\s+)?({_IDENTIFIER})\b", re.IGNORECASE)
_RE_WITH_CTE = re.compile(rf"\bWITH\s+({_IDENTIFIER})\s+AS\s*\(", re.IGNORECASE)


def raw_sql_aliases(sql: str) -> set[str]:
    aliases: set[str] = set()
    for match in _RE_PAREN_ALIAS.finditer(sql):
        alias = match.group(1)
        if alias.lower() not in _ALIAS_KEYWORDS:
            aliases.add(normalize_table_name(alias))
    for match in _RE_FROM_JOIN_ALIAS.finditer(sql):
        alias = match.group(1)
        if alias.lower() not in _ALIAS_KEYWORDS:
            aliases.add(normalize_table_name(alias))
    for match in _RE_WITH_CTE.finditer(sql):
        aliases.add(normalize_table_name(match.group(1)))
    return aliases
