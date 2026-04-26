from __future__ import annotations

import re


FORBIDDEN_SQL_KEYWORDS = {
    "alter",
    "call",
    "create",
    "delete",
    "drop",
    "execute",
    "grant",
    "insert",
    "lock",
    "merge",
    "replace",
    "revoke",
    "truncate",
    "update",
}


def validate_readonly_sql(sql: str) -> None:
    normalized = _strip_leading_comments(sql).strip()
    if not normalized:
        raise ValueError("SQL is empty")

    sanitized = _sanitize_sql_for_guard(normalized).strip()
    phrase_sanitized = _sanitize_sql_for_guard(normalized, comment_replacement=" ").strip()
    first_word = _first_word(sanitized)
    if first_word not in {"select", "with"}:
        raise ValueError("Only SELECT/WITH query SQL is allowed")

    if _has_multiple_statements(sanitized):
        raise ValueError("Multiple SQL statements are not allowed")

    lowered = phrase_sanitized.lower()
    if re.search(r"\bfor\s+update\b", lowered):
        raise ValueError("SELECT FOR UPDATE is not allowed")

    tokens = set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", sanitized.lower()))
    blocked = sorted(tokens & FORBIDDEN_SQL_KEYWORDS)
    if blocked:
        raise ValueError(f"Forbidden SQL keyword found: {', '.join(blocked)}")


def _first_word(sql: str) -> str:
    match = re.match(r"([a-zA-Z_][a-zA-Z0-9_]*)", sql)
    return match.group(1).lower() if match else ""


def _strip_leading_comments(sql: str) -> str:
    rest = sql.lstrip()
    while True:
        if rest.startswith("--"):
            _, _, rest = rest.partition("\n")
            rest = rest.lstrip()
            continue
        if rest.startswith("/*"):
            end = rest.find("*/")
            if end == -1:
                return ""
            rest = rest[end + 2 :].lstrip()
            continue
        return rest


def _sanitize_sql_for_guard(sql: str, comment_replacement: str = "") -> str:
    """Remove comments and string bodies before keyword checks.

    Comments are removed without inserting a separator so mixed forms such as
    SEL/**/ECT normalize back to SELECT for first-word validation.
    """
    result: list[str] = []
    index = 0
    length = len(sql)
    while index < length:
        char = sql[index]
        next_char = sql[index + 1] if index + 1 < length else ""
        if char == "-" and next_char == "-":
            index += 2
            while index < length and sql[index] not in "\r\n":
                index += 1
            result.append(comment_replacement)
            continue
        if char == "/" and next_char == "*":
            index += 2
            while index + 1 < length and not (sql[index] == "*" and sql[index + 1] == "/"):
                index += 1
            index = length if index + 1 >= length else index + 2
            result.append(comment_replacement)
            continue
        if char in {"'", '"'}:
            quote = char
            result.append(" ")
            index += 1
            while index < length:
                current = sql[index]
                if current == quote:
                    if index + 1 < length and sql[index + 1] == quote:
                        index += 2
                        continue
                    index += 1
                    break
                if quote == "'" and current == "\\" and index + 1 < length:
                    index += 2
                    continue
                index += 1
            result.append(" ")
            continue
        result.append(char)
        index += 1
    return "".join(result)


def _has_multiple_statements(sql: str) -> bool:
    stripped = sql.strip()
    if not stripped:
        return False
    return ";" in stripped.rstrip(";").strip()
