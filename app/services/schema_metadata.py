from __future__ import annotations

import json
import re
from typing import Any


def parse_schema_metadata(text: str) -> dict[str, list[str]]:
    if not text.strip():
        return {}
    stripped = text.strip()
    if stripped.startswith(("{", "[")):
        return _parse_json_schema(stripped)
    schema = _parse_create_table_schema(stripped)
    if schema:
        return schema
    schema = _parse_text_schema(stripped)
    if schema:
        return schema
    raise ValueError("schema metadata must be JSON, CREATE TABLE SQL, or supported table/column text")


def merge_schema_metadata(*items: dict[str, list[str]] | None) -> dict[str, list[str]]:
    merged: dict[str, list[str]] = {}
    for item in items:
        if not item:
            continue
        for table, columns in item.items():
            existing = merged.setdefault(table, [])
            seen = set(existing)
            for column in columns:
                if column and column not in seen:
                    existing.append(column)
                    seen.add(column)
    return merged


def _parse_json_schema(text: str) -> dict[str, list[str]]:
    data = json.loads(text)
    if isinstance(data, dict):
        if "tables" in data and isinstance(data["tables"], list):
            return _schema_from_table_list(data["tables"])
        return {
            str(table): _column_names(columns)
            for table, columns in data.items()
            if _column_names(columns)
        }
    if isinstance(data, list):
        return _schema_from_table_list(data)
    raise ValueError("schema metadata must be a JSON object or array")


def _parse_create_table_schema(text: str) -> dict[str, list[str]]:
    cleaned = _strip_sql_comments(text)
    schema: dict[str, list[str]] = {}
    pattern = re.compile(
        r"\bcreate\s+(?:global\s+temporary\s+|temporary\s+|temp\s+|volatile\s+|multiset\s+|set\s+)?table\s+"
        r"(?:if\s+not\s+exists\s+)?(?P<table>[\"`\[\]\w.$#]+)\s*\(",
        flags=re.IGNORECASE,
    )
    for match in pattern.finditer(cleaned):
        body_start = match.end() - 1
        body_end = _matching_paren(cleaned, body_start)
        if body_end == -1:
            continue
        table = _clean_identifier(match.group("table"))
        columns = _columns_from_create_body(cleaned[body_start + 1 : body_end])
        if table and columns:
            schema[table] = columns
    return schema


def _columns_from_create_body(body: str) -> list[str]:
    columns: list[str] = []
    for item in _split_top_level_commas(body):
        item = item.strip()
        if not item:
            continue
        first_token = item.split(None, 1)[0].strip()
        normalized = _clean_identifier(first_token).lower()
        if not normalized or normalized in {
            "constraint",
            "primary",
            "foreign",
            "unique",
            "check",
            "key",
            "index",
            "partition",
        }:
            continue
        columns.append(_clean_identifier(first_token))
    return _unique_strings(columns)


def _parse_text_schema(text: str) -> dict[str, list[str]]:
    schema: dict[str, list[str]] = {}
    current_table = ""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", "--")):
            continue
        table_match = re.match(r"^(?:table|表名)\s*[:：]\s*([\"`\[\]\w.$#]+)\s*$", line, flags=re.IGNORECASE)
        if table_match:
            current_table = _clean_identifier(table_match.group(1))
            schema.setdefault(current_table, [])
            continue
        if "," in line:
            parts = [part.strip() for part in line.split(",")]
            if len(parts) >= 2 and _looks_like_table_name(parts[0]):
                table = _clean_identifier(parts[0])
                column = _clean_identifier(parts[1])
                if table and column:
                    schema.setdefault(table, []).append(column)
                continue
        if current_table:
            column = _clean_identifier(line.split()[0])
            if column:
                schema[current_table].append(column)
    return {table: _unique_strings(columns) for table, columns in schema.items() if table and columns}


def _schema_from_table_list(items: list[Any]) -> dict[str, list[str]]:
    schema: dict[str, list[str]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        table = str(item.get("table") or item.get("table_name") or "").strip()
        columns = _column_names(item.get("columns") or [])
        if table and columns:
            schema[table] = columns
    return schema


def _column_names(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str):
            name = item.strip()
        elif isinstance(item, dict):
            name = str(item.get("name") or item.get("column") or item.get("column_name") or "").strip()
        else:
            name = ""
        if name:
            result.append(name)
    return result


def _strip_sql_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"--.*?$", "", text, flags=re.MULTILINE)


def _matching_paren(text: str, start: int) -> int:
    depth = 0
    quote = ""
    for index in range(start, len(text)):
        char = text[index]
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
    return -1


def _split_top_level_commas(text: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    quote = ""
    for index, char in enumerate(text):
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            parts.append(text[start:index])
            start = index + 1
    parts.append(text[start:])
    return parts


def _clean_identifier(identifier: str) -> str:
    parts = [part.strip().strip('"`[]') for part in identifier.strip().split(".")]
    return ".".join(part for part in parts if part)


def _looks_like_table_name(value: str) -> bool:
    return bool(re.match(r"^[\"`\[\]\w$#]+(?:\.[\"`\[\]\w$#]+)+$", value.strip()))


def _unique_strings(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result
