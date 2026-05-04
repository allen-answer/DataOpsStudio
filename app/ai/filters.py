"""app.ai.filters —— AI 输出白名单过滤（Phase 9 Day 4 拆出）。

- `_validate_and_filter_edges` —— 校验 AI 返回的 edges：表名 / 字段名白名单 +
  dml_type / confidence Literal 闭集（走 AIInferredEdge field_validator）
- `_validate_and_filter_column_hints` —— 校验 column attribution hints：
  suggested_table 在白名单 + 每个 column 只保留 1 条
- `_normalize_name` —— 表 / 字段名归一化（去引号 + lowercase）
- `_filter_columns` —— 字段白名单过滤（支持 `t.col` 和 `col` 两种命中）

老路径 `from app.services.lineage_ai_inference import _validate_and_filter_edges`
仍可用 —— 那边改成 re-export。
"""
from __future__ import annotations

import re
from typing import Any

from app.ai.schemas import AIColumnHint, AIInferredEdge


# AI 推断的 dml_type 枚举（避免 hallucinated 类型）
_VALID_DML = {"INSERT", "UPDATE", "MERGE", "DELETE", "CTAS", "TRUNCATE_INSERT"}


_NAME_NORMALIZE_RE = re.compile(r'["`\[\]]')


def _normalize_name(name: str) -> str:
    """表 / 字段名归一化：去引号 + lowercase。`"ODS"."T1"` → ods.t1"""
    return _NAME_NORMALIZE_RE.sub("", str(name or "")).strip().lower()


def _filter_columns(raw: Any, whitelist: set[str]) -> list[str]:
    """白名单过滤字段名。raw 中每个字段保留原始 spelling，但要么全名命中
    要么剥点后命中，否则丢弃。空白名单 → 直接返回空。
    """
    if not isinstance(raw, list) or not whitelist:
        return []
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        normalized = _normalize_name(item)
        bare = normalized.split(".")[-1] if "." in normalized else normalized
        if normalized in whitelist or bare in whitelist:
            out.append(item.strip())
    # 去重保序
    seen: set[str] = set()
    unique: list[str] = []
    for item in out:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def _validate_and_filter_edges(
    raw_edges: Any,
    *,
    table_set: set[str],
    column_set: set[str],
    fragment_index: int,
    evidence_hint: str,
    source_kind: str = "parse_error",
) -> tuple[list[AIInferredEdge], int]:
    """逐条 edge 校验：dml_type / confidence 枚举 + 表名 / 字段名白名单。

    返回 list[AIInferredEdge]（model 实例），caller 在写入 output 时
    `model_dump()` 回 dict（外部 API 向后兼容）。
    """
    if not isinstance(raw_edges, list):
        return [], 0
    output: list[AIInferredEdge] = []
    filtered = 0
    for raw in raw_edges:
        if not isinstance(raw, dict):
            filtered += 1
            continue
        target_table = _normalize_name(raw.get("target_table") or "")
        source_table = _normalize_name(raw.get("source_table") or "")
        # 至少要有 target（INSERT/UPDATE/MERGE/DELETE 都有 target）
        if not target_table or target_table not in table_set:
            filtered += 1
            continue
        # source_table 可空（DELETE/TRUNCATE 可能没 source；但 INSERT 必须有）
        if source_table and source_table not in table_set:
            # 源不在白名单 → 整条边降级为只保留 target，source 设空
            source_table = ""
        dml_type = str(raw.get("dml_type") or "INSERT").upper()
        if dml_type not in _VALID_DML:
            dml_type = "INSERT"
        source_columns = _filter_columns(raw.get("source_columns"), column_set)
        target_columns = _filter_columns(raw.get("target_columns"), column_set)
        evidence = str(raw.get("evidence") or "").strip()[:300] or evidence_hint
        reason = str(raw.get("reason") or "AI 推断（无附加说明）").strip()[:200]
        try:
            edge = AIInferredEdge.model_validate({
                "source_table": source_table,
                "target_table": target_table,
                "dml_type": dml_type,
                "source_columns": source_columns,
                "target_columns": target_columns,
                "confidence": raw.get("confidence"),
                "reason": reason,
                "evidence": evidence,
                "fragment_index": fragment_index,
                "source_kind": source_kind,
                "is_ai_inferred": True,
            })
        except Exception:
            filtered += 1
            continue
        output.append(edge)
    return output, filtered


def _validate_and_filter_column_hints(
    raw_hints: Any,
    *,
    table_set: set[str],
    column_set: set[str],
) -> tuple[list[AIColumnHint], int]:
    """逐条 hint 校验：suggested_table 在白名单 + 每个 column 只保留 1 条。"""
    if not isinstance(raw_hints, list):
        return [], 0
    out: list[AIColumnHint] = []
    filtered = 0
    seen_columns: set[str] = set()
    for raw in raw_hints:
        if not isinstance(raw, dict):
            filtered += 1
            continue
        column = str(raw.get("column") or "").strip()
        suggested = _normalize_name(raw.get("suggested_table") or "")
        if not column or not suggested:
            filtered += 1
            continue
        if suggested not in table_set:
            filtered += 1
            continue
        column_key = _normalize_name(column).split(".")[-1]
        if column_key in seen_columns:
            filtered += 1
            continue
        seen_columns.add(column_key)
        reason = str(raw.get("reason") or "AI 推断（无附加说明）").strip()[:200]
        evidence = str(raw.get("evidence") or "").strip()[:300]
        try:
            hint = AIColumnHint.model_validate({
                "column": column,
                "suggested_table": suggested,
                "confidence": raw.get("confidence"),
                "reason": reason,
                "evidence": evidence,
                "source_kind": "column_attribution",
                "is_ai_inferred": True,
            })
        except Exception:
            filtered += 1
            continue
        out.append(hint)
    return out, filtered


__all__ = [
    "_VALID_DML",
    "_normalize_name",
    "_filter_columns",
    "_validate_and_filter_edges",
    "_validate_and_filter_column_hints",
]
