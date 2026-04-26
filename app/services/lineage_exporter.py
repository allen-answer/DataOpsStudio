from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pandas as pd


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


def write_lineage_batch_excel(path: Path, result: dict[str, Any]) -> None:
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        pd.DataFrame(_excel_safe_rows([result.get("summary", {})])).to_excel(writer, sheet_name="流程总览", index=False)
        pd.DataFrame(_flatten_list_fields(result.get("files", []))).to_excel(
            writer, sheet_name="脚本清单", index=False
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
        value = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value)
        return re.sub(r"[\x00-\x08\x0b-\x0c\x0e-\x1f]", "", value)
    return value
