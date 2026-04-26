from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app.compare.engine import CompareBuckets


def write_result_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def write_excel(path: Path, buckets: CompareBuckets, max_rows: int | None = None) -> None:
    export_buckets = _limit_buckets(buckets, max_rows)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        _write_summary_sheet(writer.book, export_buckets)
        for sheet_name in ("only_source", "only_target", "diff", "same"):
            rows = [_flatten_item(item) for item in export_buckets[sheet_name]]
            frame = pd.DataFrame(rows)
            frame.to_excel(writer, sheet_name=sheet_name, index=False)


def _flatten_item(item: dict[str, Any]) -> dict[str, Any]:
    row: dict[str, Any] = {"key": json.dumps(item.get("key", []), ensure_ascii=False, default=str)}
    for side in ("source", "target"):
        for column, value in item.get(side, {}).items():
            row[f"{side}.{column}"] = _excel_safe(value)
    if "changes" in item:
        row["changes"] = json.dumps(item["changes"], ensure_ascii=False, default=str)
    return row


def _limit_buckets(buckets: CompareBuckets, max_rows: int | None) -> CompareBuckets:
    if max_rows is None:
        return buckets
    remaining = max_rows
    limited: CompareBuckets = {}
    for bucket_name in ("diff", "only_source", "only_target", "same"):
        rows = buckets[bucket_name]
        limited[bucket_name] = rows[: max(remaining, 0)]
        remaining -= len(limited[bucket_name])
    return limited


def _write_summary_sheet(workbook: Any, buckets: CompareBuckets) -> None:
    sheet = workbook.create_sheet("汇总对照", 0)
    source_columns = _side_columns(buckets, "source")
    target_columns = _side_columns(buckets, "target")
    separator_column = len(source_columns) + 1
    target_start_column = separator_column + 1
    compare_start_column = target_start_column + len(target_columns)

    _write_summary_headers(
        sheet,
        source_columns,
        target_columns,
        separator_column,
        target_start_column,
        compare_start_column,
    )

    row_number = 3
    for bucket_name in ("diff", "only_source", "only_target", "same"):
        for item in buckets[bucket_name]:
            source = item.get("source", {})
            target = item.get("target", {})
            for index, column in enumerate(source_columns, start=1):
                sheet.cell(row=row_number, column=index, value=_excel_safe(source.get(column)))
            for index, column in enumerate(target_columns, start=target_start_column):
                sheet.cell(row=row_number, column=index, value=_excel_safe(target.get(column)))
            sheet.cell(row=row_number, column=compare_start_column, value=_existence_label(bucket_name))
            sheet.cell(row=row_number, column=compare_start_column + 1, value=_diff_columns(item))
            _fill_summary_row(sheet, row_number, compare_start_column + 1, bucket_name)
            row_number += 1

    _format_summary_sheet(sheet, compare_start_column + 1)


def _write_summary_headers(
    sheet: Any,
    source_columns: list[str],
    target_columns: list[str],
    separator_column: int,
    target_start_column: int,
    compare_start_column: int,
) -> None:
    source_end = max(len(source_columns), 1)
    target_end = max(target_start_column + len(target_columns) - 1, target_start_column)
    compare_end = compare_start_column + 1

    sheet.cell(row=1, column=1, value="源数据源")
    sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=source_end)
    sheet.cell(row=1, column=target_start_column, value="目标数据源")
    sheet.merge_cells(start_row=1, start_column=target_start_column, end_row=1, end_column=target_end)
    sheet.cell(row=1, column=compare_start_column, value="对比结果")
    sheet.merge_cells(start_row=1, start_column=compare_start_column, end_row=1, end_column=compare_end)

    for index, column in enumerate(source_columns, start=1):
        sheet.cell(row=2, column=index, value=column)
    sheet.cell(row=2, column=separator_column, value="")
    for index, column in enumerate(target_columns, start=target_start_column):
        sheet.cell(row=2, column=index, value=column)
    sheet.cell(row=2, column=compare_start_column, value="是否存在")
    sheet.cell(row=2, column=compare_start_column + 1, value="差异字段")


def _side_columns(buckets: CompareBuckets, side: str) -> list[str]:
    columns: list[str] = []
    seen: set[str] = set()
    for bucket_name in ("diff", "only_source", "only_target", "same"):
        for item in buckets[bucket_name]:
            for column in item.get(side, {}):
                if column not in seen:
                    seen.add(column)
                    columns.append(column)
    return columns


def _existence_label(bucket_name: str) -> str:
    if bucket_name in {"diff", "same"}:
        return "两边都有"
    if bucket_name == "only_source":
        return "仅源存在"
    return "仅目标存在"


def _diff_columns(item: dict[str, Any]) -> str:
    return ", ".join(item.get("changes", {}).keys())


def _fill_summary_row(sheet: Any, row_number: int, last_column: int, bucket_name: str) -> None:
    colors = {
        "diff": "FFF5E5",
        "only_source": "FDECEC",
        "only_target": "EAF2FF",
        "same": "FFFFFF",
    }
    fill = PatternFill("solid", fgColor=colors[bucket_name])
    for column in range(1, last_column + 1):
        sheet.cell(row=row_number, column=column).fill = fill


def _format_summary_sheet(sheet: Any, last_column: int) -> None:
    group_fill = PatternFill("solid", fgColor="DCEBFF")
    source_fill = PatternFill("solid", fgColor="EAF7EF")
    target_fill = PatternFill("solid", fgColor="FFF3D8")
    compare_fill = PatternFill("solid", fgColor="F1F5F9")
    header_font = Font(bold=True)

    for row in (1, 2):
        for cell in sheet[row]:
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.fill = group_fill if row == 1 else compare_fill

    for cell in sheet[2]:
        if cell.column < _first_blank_header_column(sheet):
            cell.fill = source_fill
        elif cell.value in {"是否存在", "差异字段"}:
            cell.fill = compare_fill
        elif cell.value:
            cell.fill = target_fill

    sheet.freeze_panes = "A3"
    sheet.auto_filter.ref = f"A2:{get_column_letter(last_column)}{sheet.max_row}"

    for column in range(1, last_column + 1):
        values = [str(sheet.cell(row=row, column=column).value) for row in range(1, sheet.max_row + 1)]
        values = [value for value in values if value and value != "None"]
        width = min(max([len(value) for value in values] + [10]) + 2, 36)
        sheet.column_dimensions[get_column_letter(column)].width = width


def _first_blank_header_column(sheet: Any) -> int:
    for cell in sheet[2]:
        if cell.value in (None, ""):
            return cell.column
    return sheet.max_column + 1


def _excel_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    return str(value)
