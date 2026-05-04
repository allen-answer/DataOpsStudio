from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pandas as pd


# Excel 单元格 32767 字符上限。openpyxl 超过会自动 truncate + UserWarning。
# 我们留 200 字符余量给 "...[truncated N chars]" 后缀，硬切到 32500。
_EXCEL_CELL_LIMIT = 32500
_EXCEL_TRUNCATE_SUFFIX = "...[已截断 {} 字符]"

# 每个文件子结构里"明细列表"字段：拆到独立 sheet，主表只保留计数。
# 每条记录会按 file_name 注入到独立 sheet 里，便于按文件跳转。
_PER_FILE_DETAIL_FIELDS = [
    ("procedure_segments", "过程段明细"),
    ("statements", "语句明细"),
    ("parse_errors", "解析失败明细"),
    ("dynamic_sql_segments", "动态SQL明细"),
    ("warnings", "脚本警告"),
]


def write_lineage_json(path: Path, result: dict[str, Any]) -> None:
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def write_lineage_excel(path: Path, result: dict[str, Any]) -> None:
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        pd.DataFrame(_excel_safe_rows(result.get("tables", []))).to_excel(writer, sheet_name="来源表", index=False)
        pd.DataFrame(_excel_safe_rows(result.get("variables", []))).to_excel(writer, sheet_name="脚本变量", index=False)
        pd.DataFrame(_flatten_list_fields(result.get("columns", []))).to_excel(
            writer, sheet_name="字段血缘", index=False
        )
        pd.DataFrame(_flatten_list_fields(result.get("insert_mappings", []))).to_excel(
            writer, sheet_name="落表字段映射", index=False
        )
        pd.DataFrame(_excel_safe_rows(result.get("joins", []))).to_excel(writer, sheet_name="JOIN", index=False)
        pd.DataFrame(_excel_safe_rows(result.get("filters", []))).to_excel(writer, sheet_name="WHERE", index=False)
        pd.DataFrame(_excel_safe_rows(result.get("group_by", []))).to_excel(writer, sheet_name="GROUP_BY", index=False)
        pd.DataFrame(_excel_safe_rows(result.get("unions", []))).to_excel(writer, sheet_name="UNION", index=False)
        # 单脚本的解析失败 / 动态 SQL 也单独成 sheet 便于查看
        if result.get("parse_errors"):
            pd.DataFrame(_excel_safe_rows(result.get("parse_errors", []))).to_excel(
                writer, sheet_name="解析失败明细", index=False
            )
        if result.get("dynamic_sql_segments"):
            pd.DataFrame(_excel_safe_rows(result.get("dynamic_sql_segments", []))).to_excel(
                writer, sheet_name="动态SQL明细", index=False
            )


def write_lineage_batch_excel(path: Path, result: dict[str, Any]) -> None:
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        pd.DataFrame(_excel_safe_rows([result.get("summary", {})])).to_excel(writer, sheet_name="流程总览", index=False)

        files = result.get("files", []) or []
        # 主"脚本清单" sheet：每文件大数组字段（procedure_segments / statements /
        # parse_errors / dynamic_sql_segments / warnings）转成数量字段，原始内容
        # 全部移到对应的独立 sheet（每行带 file_name 区分）
        files_summary, detail_groups = _split_per_file_details(files)
        pd.DataFrame(_flatten_list_fields(files_summary)).to_excel(
            writer, sheet_name="脚本清单", index=False
        )
        for sheet_name, rows in detail_groups.items():
            if rows:
                pd.DataFrame(_flatten_list_fields(rows)).to_excel(
                    writer, sheet_name=sheet_name, index=False
                )

        pd.DataFrame(_flatten_list_fields(result.get("table_groups", []))).to_excel(
            writer, sheet_name="流程图分组", index=False
        )
        pd.DataFrame(_excel_safe_rows(result.get("table_edges", []))).to_excel(writer, sheet_name="表级数据流", index=False)
        pd.DataFrame(_excel_safe_rows(result.get("script_edges", []))).to_excel(writer, sheet_name="跨脚本依赖", index=False)
        pd.DataFrame(_flatten_list_fields(result.get("field_mappings", []))).to_excel(
            writer, sheet_name="字段映射", index=False
        )
        pd.DataFrame(_excel_safe_rows(result.get("warnings", []))).to_excel(writer, sheet_name="风险提示", index=False)
        # 顶层 AI inference 结果（如有）单独 sheet
        ai_inferred = result.get("ai_inferred") or {}
        if ai_inferred.get("edges"):
            pd.DataFrame(_flatten_list_fields(ai_inferred.get("edges", []))).to_excel(
                writer, sheet_name="AI兜底推断", index=False
            )


def _split_per_file_details(
    files: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """把 files 列表拆成（主表 summary, {sheet_name: [rows...]}）。

    主表把每个明细字段 X 替换成 X_count（int），不再拼成超长字符串。
    每个明细字段的内容拆到独立 sheet：每行附 file_name 列方便跳回主表。
    """
    detail_groups: dict[str, list[dict[str, Any]]] = {sheet: [] for _, sheet in _PER_FILE_DETAIL_FIELDS}
    summary_rows: list[dict[str, Any]] = []
    for f in files:
        if not isinstance(f, dict):
            continue
        file_name = f.get("file_name") or ""
        summary = dict(f)
        for field, sheet in _PER_FILE_DETAIL_FIELDS:
            value = summary.get(field)
            if isinstance(value, list):
                summary[f"{field}_count"] = len(value)
                # 拆到独立 sheet
                for item in value:
                    if isinstance(item, dict):
                        detail_groups[sheet].append({"file_name": file_name, **item})
                    else:
                        detail_groups[sheet].append({"file_name": file_name, "value": str(item)})
                # 主表里这个字段去掉（避免再被 _flatten_list_fields join 成超长 str）
                summary.pop(field, None)
        summary_rows.append(summary)
    return summary_rows, detail_groups


def _flatten_list_fields(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flattened = []
    for row in rows:
        item = dict(row)
        for key, value in list(item.items()):
            if isinstance(value, list):
                item[key] = ", ".join(str(part) for part in value)
            item[key] = _excel_safe_value(item[key])
        flattened.append(item)
    return flattened


def _excel_safe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{key: _excel_safe_value(value) for key, value in row.items()} for row in rows]


def _excel_safe_value(value: Any) -> Any:
    if isinstance(value, str):
        # 控制字符 / ANSI 转义
        value = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value)
        value = re.sub(r"[\x00-\x08\x0b-\x0c\x0e-\x1f]", "", value)
        # Cell 级超长兜底：硬切到 _EXCEL_CELL_LIMIT，附"已截断 N 字符"提示。
        # 防止漏到 openpyxl 抛 IllegalCharacterError 或自动 truncate 警告刷屏。
        if len(value) > _EXCEL_CELL_LIMIT:
            cut = len(value) - _EXCEL_CELL_LIMIT
            value = value[:_EXCEL_CELL_LIMIT] + _EXCEL_TRUNCATE_SUFFIX.format(cut)
        return value
    return value
