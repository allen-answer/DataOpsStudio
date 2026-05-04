"""Excel 导出溢出处理 / 明细 sheet 拆分。"""
from __future__ import annotations

import warnings
from pathlib import Path

import pandas as pd
import pytest

from app.services.lineage_exporter import (
    _EXCEL_CELL_LIMIT,
    _excel_safe_value,
    _split_per_file_details,
    write_lineage_batch_excel,
    write_lineage_excel,
)


# ─── _excel_safe_value：超长 cell 兜底 ────────────────────────────────────────


def test_excel_safe_value_truncates_long_string():
    huge = "x" * 50000
    out = _excel_safe_value(huge)
    assert len(out) <= 32767  # 在 Excel 上限内
    assert out.startswith("x" * 1000)
    assert "已截断" in out
    assert "字符" in out


def test_excel_safe_value_short_string_unchanged():
    out = _excel_safe_value("hello world")
    assert out == "hello world"


def test_excel_safe_value_strips_control_chars():
    out = _excel_safe_value("ab\x00cd\x1bef")
    assert "\x00" not in out
    assert "\x1b" not in out
    assert "ab" in out and "cd" in out and "ef" in out


def test_excel_safe_value_passes_through_non_string():
    assert _excel_safe_value(123) == 123
    assert _excel_safe_value(None) is None
    assert _excel_safe_value(True) is True


# ─── _split_per_file_details：每文件大数组拆 sheet ────────────────────────────


def test_split_per_file_details_moves_lists_to_detail_sheets():
    files = [
        {
            "file_name": "a.sql",
            "status": "成功",
            "procedure_segments": [
                {"sql": "INSERT 1", "procedure_name": "p1"},
                {"sql": "INSERT 2", "procedure_name": "p1"},
            ],
            "parse_errors": [{"sql": "BAD", "error": "boom"}],
            "dynamic_sql_segments": [],
            "statements": [{"type": "INSERT", "title": "t1"}],
            "warnings": [],
        },
    ]
    summary, details = _split_per_file_details(files)
    # 主表：list 字段被替换成 _count
    assert summary[0]["procedure_segments_count"] == 2
    assert summary[0]["parse_errors_count"] == 1
    assert summary[0]["statements_count"] == 1
    assert "procedure_segments" not in summary[0]
    assert "parse_errors" not in summary[0]
    # 明细 sheet：每条带 file_name
    assert len(details["过程段明细"]) == 2
    assert all(r["file_name"] == "a.sql" for r in details["过程段明细"])
    assert len(details["解析失败明细"]) == 1
    assert details["解析失败明细"][0]["error"] == "boom"
    assert len(details["语句明细"]) == 1


def test_split_per_file_details_handles_missing_fields():
    """少几个 list 字段不该报错。"""
    files = [{"file_name": "x.sql", "status": "失败"}]  # 完全没有 list 字段
    summary, details = _split_per_file_details(files)
    assert summary[0]["file_name"] == "x.sql"
    # 所有明细 sheet 都为空
    for v in details.values():
        assert v == []


def test_split_per_file_details_preserves_non_list_fields():
    """status / error / read_tables 这些非"明细"字段保留在主表。"""
    files = [
        {
            "file_name": "a.sql",
            "status": "成功",
            "error": "",
            "read_tables": ["t1", "t2"],
            "procedure_segments": [{"sql": "INSERT"}],
        },
    ]
    summary, _ = _split_per_file_details(files)
    assert summary[0]["status"] == "成功"
    assert summary[0]["read_tables"] == ["t1", "t2"]  # 不动


# ─── 端到端：写文件不抛 UserWarning ───────────────────────────────────────────


def test_batch_excel_no_truncation_warning(tmp_path: Path):
    """模拟超长 procedure_segments，验证拆 sheet 后导出不再触发 32767 截断警告。"""
    # 单个 segment 50KB > Excel 上限；之前会被压成一个 cell 超长警告
    huge_sql = "INSERT /* " + ("x" * 50000) + " */ INTO t SELECT 1"
    result = {
        "summary": {"files": 1},
        "files": [
            {
                "file_name": "big.sql",
                "status": "成功",
                "read_tables": [],
                "write_tables": [],
                "procedure_segments": [
                    {"sql": huge_sql, "procedure_name": "p1", "line_start": 1},
                ],
                "parse_errors": [],
                "dynamic_sql_segments": [],
                "statements": [],
                "warnings": [],
            },
        ],
        "table_edges": [],
        "table_groups": [],
        "script_edges": [],
        "field_mappings": [],
        "warnings": [],
    }
    out = tmp_path / "lineage.xlsx"
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        write_lineage_batch_excel(out, result)
    # openpyxl 32767 截断警告（"too long"）应不再触发
    truncation = [str(w.message) for w in caught if "too long" in str(w.message).lower()]
    assert truncation == [], f"仍有截断警告：{truncation}"
    assert out.exists()
    # 验证拆出来的 sheet 存在
    sheets = pd.ExcelFile(out).sheet_names
    assert "脚本清单" in sheets
    assert "过程段明细" in sheets


def test_single_excel_includes_parse_errors_sheet(tmp_path: Path):
    result = {
        "tables": [],
        "variables": [],
        "columns": [],
        "insert_mappings": [],
        "joins": [],
        "filters": [],
        "group_by": [],
        "unions": [],
        "parse_errors": [{"sql": "BAD", "error": "boom"}],
        "dynamic_sql_segments": [
            {"sql": "EXECUTE IMMEDIATE p_var", "source": "execute_var_unresolved", "confidence": "unresolved"},
        ],
    }
    out = tmp_path / "single.xlsx"
    write_lineage_excel(out, result)
    sheets = pd.ExcelFile(out).sheet_names
    assert "解析失败明细" in sheets
    assert "动态SQL明细" in sheets


def test_batch_excel_includes_ai_inferred_sheet_when_present(tmp_path: Path):
    result = {
        "summary": {"files": 1},
        "files": [],
        "table_edges": [],
        "table_groups": [],
        "script_edges": [],
        "field_mappings": [],
        "warnings": [],
        "ai_inferred": {
            "edges": [
                {"source_table": "src", "target_table": "tgt", "dml_type": "INSERT", "confidence": "low",
                 "reason": "AI 推断", "evidence": "INSERT INTO tgt SELECT FROM src"},
            ],
            "warnings": [],
            "trigger_count": 1,
            "filtered_count": 0,
        },
    }
    out = tmp_path / "batch.xlsx"
    write_lineage_batch_excel(out, result)
    sheets = pd.ExcelFile(out).sheet_names
    assert "AI兜底推断" in sheets
