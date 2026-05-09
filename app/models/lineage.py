"""Lineage 领域 schema —— Phase 9 Day 1 收口。

纯 Pydantic v2 model，**禁止** `from app.lineage.*` 反向 import（避免循环）。
所有 model `ConfigDict(populate_by_name=True, extra="ignore")` —— 让现有 dict
多余字段不抛错；alias 字段（如 `schema_`）可按 alias 或 field name 任一传入。

Day 1 范围：仅 schema 定义 + 测试，不动业务代码。Day 2 会在
`analyzer.analyze_sql_lineage` / `batch_analyzer.analyze_lineage_batch` /
`lineage_service._attach_ai_inference` 出口处 `model_validate` +
`model_dump(by_alias=True)`，让 API contract 不变但 schema 集中。
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ============================================================
# Literal 闭集 —— TS codegen 时映射成 union string 类型
# ============================================================

DmlType = Literal[
    "INSERT",
    "INSERT_OVERWRITE",
    "REPLACE",
    "CREATE_TABLE_AS",
    "CREATE_OR_REPLACE_TABLE_AS",
    "CREATE_TEMP_TABLE_AS",
    "UPDATE",
    "MERGE",
    "DELETE",
    "TRUNCATE",
]

RefreshMode = Literal[
    "truncate_insert",
    "delete_insert",
    "delete_insert_partial",
    "merge",
    "update",
    "append",
    "mixed",
]

TableRoleKind = Literal[
    "target",
    "intermediate",
    "source_fact",
    "remote_dblink",
    "config",
    "reference",
    "dimension",
    "filter",
]

ParseStatus = Literal["parsed", "unsupported", "unknown"]

RuleConfidence = Literal["high", "medium", "low"]

AIConfidence = Literal["low", "medium"]

AIInferenceDmlType = Literal[
    "INSERT", "UPDATE", "MERGE", "DELETE", "CTAS", "TRUNCATE_INSERT"
]

AIInferenceSourceKind = Literal["parse_error", "dynamic_sql", "column_attribution"]

RiskLevel = Literal["high", "medium", "low"]


_BASE_CONFIG = ConfigDict(populate_by_name=True, extra="ignore")
# 顶层 envelope（Report / BatchReport / InferenceResult）允许新字段透传，避免
# 出口包模型时悄悄丢字段。元素级模型仍然 ignore，让噪声字段不污染 dump。
_ENVELOPE_CONFIG = ConfigDict(populate_by_name=True, extra="allow")


# ============================================================
# 基础引用
# ============================================================


class TableRef(BaseModel):
    """表引用。Day 2 起逐步在出口替换 list[str] 形式的表名。"""

    model_config = _BASE_CONFIG

    name: str = Field(..., min_length=1)
    schema_: str | None = Field(default=None, alias="schema")
    database: str | None = None


class ColumnRef(BaseModel):
    """字段引用。"""

    model_config = _BASE_CONFIG

    name: str = Field(..., min_length=1)
    table: str | None = None


class ColumnEdge(BaseModel):
    """字段级血缘单条边：多 source column → 单 target column。"""

    model_config = _BASE_CONFIG

    target_column: str = Field(..., min_length=1)
    source_columns: list[str] = Field(default_factory=list)
    transform: str = ""
    confidence: RuleConfidence = "medium"
    reason: str = ""


# ============================================================
# 目标 / 过程
# ============================================================


class TargetOperation(BaseModel):
    """单条写操作 —— `aggregation.collect_target_operations()` 输出形态。"""

    model_config = _BASE_CONFIG

    order: int = Field(..., ge=1)
    target_table: str = Field(..., min_length=1)
    dml_type: DmlType
    has_where: bool = False
    title: str = ""


class TargetSummary(BaseModel):
    """按目标表聚合 DML 计数 + refresh_mode 推断。"""

    model_config = _BASE_CONFIG

    target_table: str = Field(..., min_length=1)
    insert_count: int = Field(default=0, ge=0)
    update_count: int = Field(default=0, ge=0)
    merge_count: int = Field(default=0, ge=0)
    delete_count: int = Field(default=0, ge=0)
    truncate_count: int = Field(default=0, ge=0)
    delete_before_insert: bool = False
    truncate_before_insert: bool = False
    refresh_mode: RefreshMode | None = None
    titles: list[str] = Field(default_factory=list)
    # 哪些 PROCEDURE / FUNCTION / PACKAGE BODY / TRIGGER 内部写过本目标表 —— 让 UI
    # 能展示「此表被 procX 重刷」之类的溯源信息。空 list 表示纯顶层写入。
    procedure_origins: list[str] = Field(default_factory=list)


class ProcessStep(BaseModel):
    """存储过程内单段 step —— `semantic.procedures[i].steps[j]`。"""

    model_config = _BASE_CONFIG

    segment_index: str = Field(..., min_length=1)
    dml_keyword: str | None = None
    target_table: str | None = None
    line_start: int = Field(..., ge=1)
    line_end: int = Field(..., ge=1)
    preceding_comment: str = ""
    parse_status: ParseStatus = "unknown"


# ============================================================
# AI 推断 —— 6 不变量约束
# ============================================================


def _coerce_ai_confidence(value: Any) -> Any:
    """AI confidence 永不允许 high；非 {low, medium} 一律降为 low（最保守）。
    AI 异常不能影响主流程；6 不变量第 3 条。
    """
    if not isinstance(value, str):
        return "low"
    lowered = value.lower()
    if lowered not in {"low", "medium"}:
        return "low"
    return lowered


class AIInferredEdge(BaseModel):
    """AI 兜底推断的单条边。**confidence 永不允许 high**（6 不变量第 3 条）。"""

    model_config = _BASE_CONFIG

    target_table: str = Field(..., min_length=1)
    source_table: str = ""
    dml_type: AIInferenceDmlType = "INSERT"
    source_columns: list[str] = Field(default_factory=list)
    target_columns: list[str] = Field(default_factory=list)
    confidence: AIConfidence = "low"
    reason: str = ""
    evidence: str = ""
    fragment_index: int = Field(default=0, ge=0)
    source_kind: AIInferenceSourceKind = "parse_error"
    is_ai_inferred: bool = True

    @field_validator("confidence", mode="before")
    @classmethod
    def _validate_confidence(cls, value: Any) -> Any:
        return _coerce_ai_confidence(value)


class AIColumnHint(BaseModel):
    """AI 字段归属推荐（unqualified column → suggested_table）。"""

    model_config = _BASE_CONFIG

    column: str = Field(..., min_length=1)
    suggested_table: str = Field(..., min_length=1)
    confidence: AIConfidence = "low"
    reason: str = ""
    evidence: str = ""
    source_kind: Literal["column_attribution"] = "column_attribution"
    is_ai_inferred: bool = True

    @field_validator("confidence", mode="before")
    @classmethod
    def _validate_confidence(cls, value: Any) -> Any:
        return _coerce_ai_confidence(value)


class AIInferenceResult(BaseModel):
    """`result["ai_inferred"]` 整体信封。"""

    model_config = _ENVELOPE_CONFIG

    edges: list[AIInferredEdge] = Field(default_factory=list)
    column_hints: list[AIColumnHint] = Field(default_factory=list)
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    trigger_count: int = Field(default=0, ge=0)
    filtered_count: int = Field(default=0, ge=0)


# ============================================================
# 顶层报告 —— Day 2 出口校验入口
# ============================================================


class LineageReport(BaseModel):
    """`analyze_sql_lineage()` 顶层结果。

    Day 1 内部 dict-form 字段（statements / columns / joins 等）保留
    `list[dict[str, Any]]`，避免一口气重写解析器内部。Day 2~3 再逐步把
    typed 字段（target_summary / table_roles / procedure_segments / ai_inferred）
    收紧。envelope 用 `extra="allow"`：未建模的新字段（如未来加的
    `script_variables` 等）原样透传，不被 model_dump 丢掉。
    """

    model_config = _ENVELOPE_CONFIG

    statement_count: int = Field(default=0, ge=0)
    tables: list[dict[str, Any]] = Field(default_factory=list)
    columns: list[dict[str, Any]] = Field(default_factory=list)
    insert_mappings: list[dict[str, Any]] = Field(default_factory=list)
    target_summary: list[TargetSummary] = Field(default_factory=list)
    table_roles: list[dict[str, Any]] = Field(default_factory=list)
    joins: list[dict[str, Any]] = Field(default_factory=list)
    filters: list[dict[str, Any]] = Field(default_factory=list)
    group_by: list[dict[str, Any]] = Field(default_factory=list)
    unions: list[dict[str, Any]] = Field(default_factory=list)
    variables: list[dict[str, Any]] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    dynamic_sql_count: int = Field(default=0, ge=0)
    dynamic_sql_segments: list[dict[str, Any]] = Field(default_factory=list)
    procedure_segments: list[dict[str, Any]] = Field(default_factory=list)
    graph_edges: list[dict[str, Any]] = Field(default_factory=list)
    graph_groups: list[dict[str, Any]] = Field(default_factory=list)
    parse_errors: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    statements: list[dict[str, Any]] = Field(default_factory=list)
    semantic_lineage: dict[str, Any] = Field(default_factory=dict)
    report: dict[str, Any] | None = None
    # ai_enrichment / ai_inferred 不在这里建模 —— 由 lineage_service._attach_*
    # 单独写入，AIInferenceResult 在 _attach_ai_inference 出口处独立校验。
    # envelope `extra="allow"`：被注入时原样透传，但默认 dump 不会引入 None 键。


class LineageBatchReport(BaseModel):
    """`analyze_lineage_batch()` 顶层结果。

    跟 single 形态差别大（file_count / files / table_edges / dag / summary）。
    envelope 用 `extra="allow"`：内部 sub-dict（如 dag / summary）暂保留为
    dict[str, Any]，Day 2 仅做出口校验 + `model_dump`，不动 batch 解析器。
    """

    model_config = _ENVELOPE_CONFIG

    file_count: int = Field(default=0, ge=0)
    files: list[dict[str, Any]] = Field(default_factory=list)
    table_edges: list[dict[str, Any]] = Field(default_factory=list)
    table_groups: list[dict[str, Any]] = Field(default_factory=list)
    script_edges: list[dict[str, Any]] = Field(default_factory=list)
    field_mappings: list[dict[str, Any]] = Field(default_factory=list)
    impact_analysis: dict[str, list[str]] = Field(default_factory=dict)
    dag: dict[str, Any] = Field(default_factory=dict)
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    parse_errors: list[dict[str, Any]] = Field(default_factory=list)
    dynamic_sql_segments: list[dict[str, Any]] = Field(default_factory=list)
    tables: list[dict[str, Any]] = Field(default_factory=list)
    columns: list[dict[str, Any]] = Field(default_factory=list)
    insert_mappings: list[dict[str, Any]] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    report: dict[str, Any] | None = None
    # ai_enrichment / ai_inferred 同 LineageReport：不在这里建模，extra="allow"
    # 透传。


__all__ = [
    # Literal aliases
    "DmlType",
    "RefreshMode",
    "TableRoleKind",
    "ParseStatus",
    "RuleConfidence",
    "AIConfidence",
    "AIInferenceDmlType",
    "AIInferenceSourceKind",
    "RiskLevel",
    # Models
    "TableRef",
    "ColumnRef",
    "ColumnEdge",
    "TargetOperation",
    "TargetSummary",
    "ProcessStep",
    "AIInferredEdge",
    "AIColumnHint",
    "AIInferenceResult",
    "LineageReport",
    "LineageBatchReport",
]
