from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterator

from app.models import CompareRules


CompareBuckets = dict[str, list[dict[str, Any]]]

# 切片 F.1：streaming compare event 类型 —— (bucket_name, row_payload)
CompareEvent = tuple[str, dict[str, Any]]


def compare_rows_streaming(
    source_rows: list[dict[str, Any]],
    target_rows: list[dict[str, Any]],
    key_columns: list[str],
    rules: CompareRules | None = None,
) -> Iterator[CompareEvent]:
    """切片 F.1：把 compare_rows 拆成事件流，逐行 yield ('bucket_name', row_payload)。

    分类逻辑跟 compare_rows 100% 等价，只是不在内存里攒 4 桶 list。caller
    可以直接喂给 streaming writer（ParquetResultWriter batch flush），让
    runner 内存不依赖 result 总行数。

    产出顺序跟 compare_rows 的 list append 顺序一致（兼容样本采集与 fixture
    断言）：先按 source_index 顺序 yield only_source/diff/same，再按
    target_index 顺序 yield only_target。

    详见 docs/STREAMING_COMPARE_WRITER.md。
    """
    rules = rules or CompareRules()
    rules = _rules_with_positional_mappings(source_rows, target_rows, key_columns, rules)
    source_index = _index_rows(source_rows, key_columns, {}, "source")
    target_index = _index_rows(target_rows, key_columns, rules.column_mappings, "target")

    for key, source_row in source_index.items():
        target_row = target_index.get(key)
        if target_row is None:
            yield ("only_source", {"key": list(key), "source": source_row})
            continue
        changes = _row_changes(source_row, target_row, key_columns, rules)
        if changes:
            yield ("diff", {
                "key": list(key), "source": source_row, "target": target_row,
                "changes": changes,
            })
        else:
            yield ("same", {"key": list(key), "source": source_row, "target": target_row})

    for key, target_row in target_index.items():
        if key not in source_index:
            yield ("only_target", {"key": list(key), "target": target_row})


def compare_rows(
    source_rows: list[dict[str, Any]],
    target_rows: list[dict[str, Any]],
    key_columns: list[str],
    rules: CompareRules | None = None,
) -> CompareBuckets:
    """老 API：返回完整 CompareBuckets dict。

    切片 F.1 后内部走 streaming 收口 —— 所有调用方行为零回归（含 inline
    preview、json runner、scenarios 验证）。需要"不持完整 dict"的场景请直
    接用 `compare_rows_streaming`。
    """
    out: CompareBuckets = {
        "only_source": [], "only_target": [], "diff": [], "same": [],
    }
    for bucket, row in compare_rows_streaming(source_rows, target_rows, key_columns, rules):
        out[bucket].append(row)
    return out


def compare_sorted_row_events(
    source_rows: Any,
    target_rows: Any,
    key_columns: list[str],
    rules: CompareRules | None = None,
) -> Iterator[CompareEvent]:
    """切片 G：stream_compare 模式的 streaming 版本，yield ('bucket', row)。

    输入是 source / target 已按 key 排序的 iterator（跟 compare_sorted_row_iterators
    输入约束完全一致）。归并算法跟 iterators 等价，但**不再在内存里攒 4 桶 list**——
    yield 后由 caller（runner streaming 路径）直接喂给 writer。

    保留所有规则：
    - column_mappings / ignore_columns / numeric_tolerance
    - trim_strings / case_insensitive / empty_as_null
    - 未排序检测（_ensure_sorted_key 抛 ValueError）
    - 重复 key 检测（_ensure_sorted_key 同 key 二度出现抛 ValueError）

    详见 docs/STREAMING_COMPARE_WRITER.md。
    """
    rules = rules or CompareRules()
    source_iter = iter(source_rows)
    target_iter = iter(target_rows)
    source_row = next(source_iter, None)
    target_row = next(target_iter, None)
    if source_row is not None and target_row is not None:
        rules = _rules_with_positional_mappings([source_row], [target_row], key_columns, rules)

    last_source_key: tuple[Any, ...] | None = None
    last_target_key: tuple[Any, ...] | None = None
    source_key: tuple[Any, ...] | None = None
    target_key: tuple[Any, ...] | None = None

    def _compute_source_key() -> tuple[Any, ...] | None:
        nonlocal last_source_key
        if source_row is None:
            return None
        key = _row_key(source_row, key_columns, {}, "source")
        _ensure_sorted_key(last_source_key, key, "source")
        last_source_key = key
        return key

    def _compute_target_key() -> tuple[Any, ...] | None:
        nonlocal last_target_key
        if target_row is None:
            return None
        key = _row_key(target_row, key_columns, rules.column_mappings, "target")
        _ensure_sorted_key(last_target_key, key, "target")
        last_target_key = key
        return key

    source_key = _compute_source_key()
    target_key = _compute_target_key()

    while source_row is not None or target_row is not None:
        if target_row is None or (source_key is not None and target_key is not None and source_key < target_key):
            yield ("only_source", {"key": list(source_key), "source": source_row})
            source_row = next(source_iter, None)
            source_key = _compute_source_key()
            continue
        if source_row is None or (source_key is not None and target_key is not None and target_key < source_key):
            yield ("only_target", {"key": list(target_key), "target": target_row})
            target_row = next(target_iter, None)
            target_key = _compute_target_key()
            continue

        changes = _row_changes(source_row, target_row, key_columns, rules)
        if changes:
            yield ("diff", {
                "key": list(source_key), "source": source_row, "target": target_row,
                "changes": changes,
            })
        else:
            yield ("same", {"key": list(source_key), "source": source_row, "target": target_row})
        source_row = next(source_iter, None)
        target_row = next(target_iter, None)
        source_key = _compute_source_key()
        target_key = _compute_target_key()


def compare_sorted_row_iterators(
    source_rows: Any,
    target_rows: Any,
    key_columns: list[str],
    rules: CompareRules | None = None,
) -> CompareBuckets:
    """老 API：返完整 CompareBuckets。

    切片 G 起内部走 `compare_sorted_row_events` 收口 —— 所有调用方（runner
    json 模式 / 测试 / scenarios）行为零回归。需要"不持完整 dict"的 stream
    模式场景请直接用 `compare_sorted_row_events`。
    """
    out: CompareBuckets = {
        "only_source": [], "only_target": [], "diff": [], "same": [],
    }
    for bucket, row in compare_sorted_row_events(source_rows, target_rows, key_columns, rules):
        out[bucket].append(row)
    return out


def _rules_with_positional_mappings(
    source_rows: list[dict[str, Any]],
    target_rows: list[dict[str, Any]],
    key_columns: list[str],
    rules: CompareRules,
) -> CompareRules:
    if rules.column_mappings or not source_rows or not target_rows:
        return rules
    source_columns = list(source_rows[0])
    target_columns = list(target_rows[0])
    if rules.schema_policy == "strict" and [_normalize_column_name(c) for c in source_columns] != [
        _normalize_column_name(c) for c in target_columns
    ]:
        raise ValueError("Schema mismatch: source and target columns differ")
    key_names = {_normalize_column_name(column) for column in key_columns}
    ignored = {_normalize_column_name(column) for column in rules.ignore_columns}
    mappings: dict[str, str] = {}
    key_target_names: set[str] = set(key_names)
    target_norms = {_normalize_column_name(column) for column in target_columns}
    for key in key_columns:
        key_norm = _normalize_column_name(key)
        if key_norm in target_norms:
            continue
        source_index = next(
            (index for index, column in enumerate(source_columns) if _normalize_column_name(column) == key_norm),
            -1,
        )
        if 0 <= source_index < len(target_columns):
            mappings[key] = target_columns[source_index]
            key_target_names.add(_normalize_column_name(target_columns[source_index]))
    source_value_columns = [
        (index, column) for index, column in enumerate(source_columns, start=1)
        if _normalize_column_name(column) not in key_names
        and _normalize_column_name(column) not in ignored
    ]
    target_value_columns = [
        column for column in target_columns
        if _normalize_column_name(column) not in key_target_names
        and _normalize_column_name(column) not in ignored
    ]
    for value_index, (source_index, source_column) in enumerate(source_value_columns):
        if value_index < len(target_value_columns):
            mappings[source_column] = target_value_columns[value_index]
        else:
            mappings[source_column] = f"__missing_target_column_{source_index}_{source_column}"
    return rules.model_copy(update={"column_mappings": mappings})


def _index_rows(
    rows: list[dict[str, Any]],
    key_columns: list[str],
    column_mappings: dict[str, str],
    side: str,
) -> dict[tuple[Any, ...], dict[str, Any]]:
    indexed: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row_number, row in enumerate(rows, start=1):
        side_keys = [_target_column_name(column, row, column_mappings) for column in key_columns]
        key = _row_key(row, side_keys, {}, side)
        if key in indexed:
            raise ValueError(f"{side} has duplicate key: {key}")
        indexed[key] = row
    return indexed


def _row_key(
    row: dict[str, Any],
    key_columns: list[str],
    column_mappings: dict[str, str],
    side: str,
) -> tuple[Any, ...]:
    side_keys = [_target_column_name(column, row, column_mappings) for column in key_columns]
    missing = [column for column in side_keys if _resolve_column(row, column) is None]
    if missing:
        raise ValueError(f"{side} row missing key columns: {', '.join(missing)}")
    return tuple(_normalize_key_value(row[_resolve_column(row, column)], rules=None) for column in side_keys)


def _ensure_sorted_key(last_key: tuple[Any, ...] | None, key: tuple[Any, ...], side: str) -> None:
    if last_key is None:
        return
    if key == last_key:
        raise ValueError(f"{side} has duplicate key: {key}")
    if key < last_key:
        raise ValueError(f"{side} rows must be sorted by key for stream_compare")


def _row_changes(
    source_row: dict[str, Any],
    target_row: dict[str, Any],
    key_columns: list[str],
    rules: CompareRules,
) -> dict[str, dict[str, Any]]:
    ignored = {_normalize_column_name(column) for column in rules.ignore_columns}
    key_names = {_normalize_column_name(column) for column in key_columns}
    mapping_targets = {_normalize_column_name(target) for target in rules.column_mappings.values()}
    reverse_mappings = {_normalize_column_name(target): source for source, target in rules.column_mappings.items()}
    target_only_columns = {
        column
        for column in target_row
        if _normalize_column_name(column) not in reverse_mappings
        and _normalize_column_name(column) not in mapping_targets
    }
    columns = {
        column
        for column in (set(source_row) | target_only_columns)
        if _normalize_column_name(column) not in key_names and _normalize_column_name(column) not in ignored
    }
    changes: dict[str, dict[str, Any]] = {}
    for column in sorted(columns):
        target_column = _target_column_name(column, target_row, rules.column_mappings)
        source_column = _resolve_column(source_row, column)
        resolved_target_column = _resolve_column(target_row, target_column)
        source_value = source_row.get(source_column) if source_column else None
        target_value = target_row.get(resolved_target_column) if resolved_target_column else None
        if not _values_equal(source_value, target_value, rules):
            changes[column] = {
                "source": source_value,
                "target": target_value,
                "target_column": resolved_target_column or target_column,
            }
    return changes


def _values_equal(source_value: Any, target_value: Any, rules: CompareRules) -> bool:
    source_value = _normalize_value(source_value, rules)
    target_value = _normalize_value(target_value, rules)
    if source_value == target_value:
        return True
    if rules.numeric_tolerance is None:
        return False
    source_number = _decimal_or_none(source_value)
    target_number = _decimal_or_none(target_value)
    if source_number is None or target_number is None:
        return False
    return abs(source_number - target_number) <= Decimal(str(rules.numeric_tolerance))


def _normalize_value(value: Any, rules: CompareRules) -> Any:
    if isinstance(value, (datetime, date)):
        value = _temporal_to_iso(value)
    if isinstance(value, str):
        if rules.trim_strings:
            value = value.strip()
        if rules.empty_as_null and value == "":
            return None
        if rules.case_insensitive:
            value = value.lower()
    return value


def _normalize_key_value(value: Any, rules: CompareRules | None) -> Any:
    if isinstance(value, str):
        value = value.strip()
        if rules is not None and rules.empty_as_null and value == "":
            return None
        if rules is not None and rules.case_insensitive:
            return value.lower()
    if isinstance(value, (datetime, date)):
        return _temporal_to_iso(value)
    return value


def _temporal_to_iso(value: datetime | date) -> str:
    # openpyxl reads dates back as datetime(y,m,d,0,0,0); MySQL DATE columns
    # come back as date(y,m,d). Treat midnight-datetime as a plain date so the
    # two sides compare equal in Excel↔SQL workflows.
    if isinstance(value, datetime):
        if value.hour == 0 and value.minute == 0 and value.second == 0 and value.microsecond == 0:
            return value.date().isoformat()
    return value.isoformat()


def _decimal_or_none(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _target_column_name(source_column: str, target_row: dict[str, Any], column_mappings: dict[str, str]) -> str:
    mapped = _mapped_column_name(source_column, column_mappings)
    if mapped:
        resolved_mapped = _resolve_column(target_row, mapped)
        if resolved_mapped:
            return resolved_mapped
        return mapped
    resolved_source = _resolve_column(target_row, source_column)
    if resolved_source:
        return resolved_source
    return source_column


def _mapped_column_name(source_column: str, column_mappings: dict[str, str]) -> str | None:
    if source_column in column_mappings:
        return column_mappings[source_column]
    normalized_source = _normalize_column_name(source_column)
    for mapping_source, mapping_target in column_mappings.items():
        if _normalize_column_name(mapping_source) == normalized_source:
            return mapping_target
    return None


def _resolve_column(row: dict[str, Any], column: str | None) -> str | None:
    if column is None:
        return None
    if column in row:
        return column
    normalized_column = _normalize_column_name(column)
    for actual_column in row:
        if _normalize_column_name(actual_column) == normalized_column:
            return actual_column
    return None


def _normalize_column_name(column: str) -> str:
    return column.strip().lower()
