from __future__ import annotations

from typing import Any

from app.lineage._common import unique_strings


def analysis_warnings(
    analyses: list[dict[str, Any]],
    dynamic_sql_segments: list[dict[str, str]],
    parse_errors: list[dict[str, str]],
    procedure_segments: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    if dynamic_sql_segments:
        warnings.append(
            {
                "type": "动态 SQL",
                "message": f"识别到 {len(dynamic_sql_segments)} 段动态 SQL，静态分析结果可能不完整",
            }
        )
    if procedure_segments:
        proc_names = unique_strings(seg.get("procedure_name", "") for seg in procedure_segments)
        warnings.append(
            {
                "type": "存储过程",
                "message": f"识别到 {len(proc_names)} 个过程/函数中的 {len(procedure_segments)} 段 DML：{', '.join(proc_names)}",
            }
        )
    for error in parse_errors:
        warnings.append({"type": "解析失败", "message": error.get("error", "")})
    for statement_index, analysis in enumerate(analyses, start=1):
        for item in analysis.get("columns", []) + analysis.get("insert_mappings", []):
            for warning in item.get("warnings", []):
                warnings.append(
                    {
                        "type": warning.get("type", "血缘提示"),
                        "message": warning.get("message", ""),
                        "statement_index": str(statement_index),
                    }
                )
    return unique_warning_dicts(warnings)


def unique_warning_dicts(items: list[dict[str, str]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[tuple[str, str], ...]] = set()
    for item in items:
        key = tuple(sorted((str(k), str(v)) for k, v in item.items()))
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result
