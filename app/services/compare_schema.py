from __future__ import annotations

from typing import Any

from app.models.compare import CompareRules


def normalize_column_name(column: str) -> str:
    return str(column or "").strip().lower()


def uniquify_columns(columns: list[Any]) -> list[str]:
    """Return stable, case-insensitive-unique column names.

    DB-API rows are represented as dicts in the compare engine. Duplicate
    cursor.description names would otherwise overwrite earlier values during
    dict(zip(columns, row)). Keep the first name unchanged and suffix later
    occurrences with __2 / __3.
    """
    used: set[str] = set()
    counts: dict[str, int] = {}
    result: list[str] = []
    for index, raw in enumerate(columns, start=1):
        base = str(raw or "").strip() or f"column_{index}"
        norm = normalize_column_name(base)
        counts[norm] = counts.get(norm, 0) + 1
        if norm not in used:
            candidate = base
        else:
            occurrence = counts[norm]
            candidate = f"{base}__{occurrence}"
            while normalize_column_name(candidate) in used:
                occurrence += 1
                candidate = f"{base}__{occurrence}"
            counts[norm] = occurrence
        used.add(normalize_column_name(candidate))
        result.append(candidate)
    return result


def column_warnings(raw_columns: list[Any], columns: list[str] | None = None, *, side: str = "") -> list[dict[str, Any]]:
    columns = columns or uniquify_columns(raw_columns)
    groups: dict[str, dict[str, Any]] = {}
    for raw, unique in zip(raw_columns, columns, strict=False):
        name = str(raw or "").strip()
        norm = normalize_column_name(name)
        if not norm:
            continue
        group = groups.setdefault(norm, {"name": name, "columns": []})
        group["columns"].append(unique)
    duplicates = [group for group in groups.values() if len(group["columns"]) > 1]
    if not duplicates:
        return []
    label = f"{side} " if side else ""
    return [
        {
            "type": "duplicate_columns",
            "level": "warning",
            "side": side,
            "message": f"{label}结果集存在重复字段，已自动加 __2 / __3 后缀避免覆盖。",
            "columns": duplicates,
        }
    ]


def build_schema_report(
    source_columns: list[str],
    target_columns: list[str],
    key_columns: list[str],
    rules: CompareRules,
) -> dict[str, Any]:
    if not rules.column_mappings:
        return _build_positional_schema_report(source_columns, target_columns, key_columns, rules)

    ignored_norms = {normalize_column_name(column) for column in rules.ignore_columns}
    key_norms = {normalize_column_name(column) for column in key_columns}
    target_by_norm = {normalize_column_name(column): column for column in target_columns}
    source_by_norm = {normalize_column_name(column): column for column in source_columns}
    mapping_by_source_norm = {
        normalize_column_name(source): target
        for source, target in rules.column_mappings.items()
        if normalize_column_name(source) and str(target or "").strip()
    }

    compared: list[dict[str, str]] = []
    used_target_norms: set[str] = set()
    ignored_columns: list[str] = []
    key_target_norms: set[str] = set()
    for key in key_columns:
        target_key = _mapped_or_same_target(key, target_columns, mapping_by_source_norm)
        if target_key:
            key_target_norms.add(normalize_column_name(target_key))

    for source_column in source_columns:
        source_norm = normalize_column_name(source_column)
        if source_norm in key_norms:
            continue
        if source_norm in ignored_norms:
            ignored_columns.append(source_column)
            continue
        mapped_target = mapping_by_source_norm.get(source_norm)
        target_column = _resolve_column(target_columns, mapped_target) if mapped_target else target_by_norm.get(source_norm)
        if target_column:
            used_target_norms.add(normalize_column_name(target_column))
            compared.append(
                {
                    "source": source_column,
                    "target": target_column,
                    "mode": "mapped" if mapped_target else "same_name",
                }
            )
        else:
            compared.append({"source": source_column, "target": "", "mode": "source_only"})

    for target_column in target_columns:
        target_norm = normalize_column_name(target_column)
        if target_norm in key_target_norms or target_norm in used_target_norms:
            continue
        if target_norm in ignored_norms:
            ignored_columns.append(target_column)
            continue
        if target_norm in source_by_norm and target_norm not in key_norms:
            continue
        compared.append({"source": "", "target": target_column, "mode": "target_only"})

    source_only = [item["source"] for item in compared if item["mode"] == "source_only"]
    target_only = [item["target"] for item in compared if item["mode"] == "target_only"]
    warnings = _schema_warnings(source_columns, target_columns, source_only, target_only, compared)
    return {
        "source_columns": source_columns,
        "target_columns": target_columns,
        "source_count": len(source_columns),
        "target_count": len(target_columns),
        "key_columns": key_columns,
        "ignored_columns": _unique_strings(ignored_columns),
        "compared_columns": compared,
        "compared_count": len(compared),
        "source_only_columns": source_only,
        "target_only_columns": target_only,
        "count_mismatch": len(source_columns) != len(target_columns),
        "mapping_mode": "manual",
        "has_schema_mismatch": bool(source_only or target_only or len(source_columns) != len(target_columns)),
        "warnings": warnings,
    }


def _build_positional_schema_report(
    source_columns: list[str],
    target_columns: list[str],
    key_columns: list[str],
    rules: CompareRules,
) -> dict[str, Any]:
    ignored_norms = {normalize_column_name(column) for column in rules.ignore_columns}
    key_norms = {normalize_column_name(column) for column in key_columns}
    compared: list[dict[str, str]] = []
    ignored_columns: list[str] = []

    for index, source_column in enumerate(source_columns):
        source_norm = normalize_column_name(source_column)
        target_column = target_columns[index] if index < len(target_columns) else ""
        if source_norm in key_norms:
            continue
        if source_norm in ignored_norms:
            ignored_columns.append(source_column)
            continue
        if target_column:
            compared.append({"source": source_column, "target": target_column, "mode": "position"})
        else:
            compared.append({"source": source_column, "target": "", "mode": "source_only"})

    if len(target_columns) > len(source_columns):
        for target_column in target_columns[len(source_columns):]:
            if normalize_column_name(target_column) in ignored_norms:
                ignored_columns.append(target_column)
                continue
            compared.append({"source": "", "target": target_column, "mode": "target_only"})

    source_only = [item["source"] for item in compared if item["mode"] == "source_only"]
    target_only = [item["target"] for item in compared if item["mode"] == "target_only"]
    warnings = _schema_warnings(source_columns, target_columns, source_only, target_only, compared)
    if compared:
        warnings.insert(
            0,
            {
                "type": "position_mapping",
                "level": "info",
                "message": "未配置字段映射，当前按左右字段顺序进行位置映射；数量不一致时多出的字段会标为仅单侧。",
            },
        )
    return {
        "source_columns": source_columns,
        "target_columns": target_columns,
        "source_count": len(source_columns),
        "target_count": len(target_columns),
        "key_columns": key_columns,
        "ignored_columns": _unique_strings(ignored_columns),
        "compared_columns": compared,
        "compared_count": len(compared),
        "source_only_columns": source_only,
        "target_only_columns": target_only,
        "count_mismatch": len(source_columns) != len(target_columns),
        "mapping_mode": "position",
        "has_schema_mismatch": bool(source_only or target_only or len(source_columns) != len(target_columns)),
        "warnings": warnings,
    }


def _schema_warnings(
    source_columns: list[str],
    target_columns: list[str],
    source_only: list[str],
    target_only: list[str],
    compared: list[dict[str, str]],
) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    if len(source_columns) != len(target_columns):
        warnings.append(
            {
                "type": "schema_count_mismatch",
                "level": "warning",
                "message": f"源/目标字段数量不一致：source={len(source_columns)}，target={len(target_columns)}；已按位置映射到较短一侧。",
            }
        )
    if source_only or target_only:
        warnings.append(
            {
                "type": "one_sided_columns",
                "level": "warning",
                "message": "存在仅单侧出现的字段；当前会按缺失值参与对比，或可加入忽略字段。",
                "source_only": source_only,
                "target_only": target_only,
            }
        )
    if not compared:
        warnings.append(
            {
                "type": "no_value_columns",
                "level": "error",
                "message": "除主键/忽略字段外没有可比较字段。",
            }
        )
    return warnings


def _mapped_or_same_target(
    source_column: str,
    target_columns: list[str],
    mapping_by_source_norm: dict[str, str],
) -> str:
    mapped = mapping_by_source_norm.get(normalize_column_name(source_column))
    return _resolve_column(target_columns, mapped) or _resolve_column(target_columns, source_column) or ""


def _resolve_column(columns: list[str], wanted: str | None) -> str:
    if not wanted:
        return ""
    if wanted in columns:
        return wanted
    wanted_norm = normalize_column_name(wanted)
    for column in columns:
        if normalize_column_name(column) == wanted_norm:
            return column
    return ""


def _unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        norm = normalize_column_name(value)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        result.append(value)
    return result
