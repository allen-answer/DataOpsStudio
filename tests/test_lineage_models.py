"""Phase 9 Day 1：app/models/lineage.py schema 收口测试。

覆盖：
- dict round-trip 等价（现有 dict 形态喂进去再 dump 出来不变）
- Literal 闭集（refresh_mode / dml_type / parse_status / source_kind）
- AIInferredEdge / AIColumnHint confidence 拦截 high → medium 静默降级
- ConfigDict(populate_by_name=True, extra="ignore")：alias 双向接收 + 多余字段忽略
- LineageReport 默认值 + 嵌套 TargetSummary 自动转换
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.lineage import (
    AIColumnHint,
    AIInferenceResult,
    AIInferredEdge,
    ColumnEdge,
    LineageReport,
    ProcessStep,
    TableRef,
    TargetOperation,
    TargetSummary,
)


# ─── TargetSummary：dict round-trip + Literal + extra ignore ──────────────────


def test_target_summary_round_trip_full_fields() -> None:
    """喂完整 aggregation.py 形态 dict，model_dump 等价。"""
    payload = {
        "target_table": "dwd.t_orders",
        "insert_count": 3,
        "update_count": 1,
        "merge_count": 0,
        "delete_count": 1,
        "truncate_count": 0,
        "delete_before_insert": True,
        "truncate_before_insert": False,
        "refresh_mode": "delete_insert",
        "titles": ["集中交易", "订单回写"],
        "procedure_origins": ["pkg.refresh_daily"],
    }
    model = TargetSummary.model_validate(payload)
    dumped = model.model_dump()
    assert dumped == payload


def test_target_summary_invalid_refresh_mode_raises() -> None:
    with pytest.raises(ValidationError):
        TargetSummary.model_validate({
            "target_table": "t",
            "refresh_mode": "wipeout",  # 不在 RefreshMode 枚举
        })


def test_target_summary_extra_field_ignored() -> None:
    """extra='ignore'：未声明字段静默丢掉，不抛错。"""
    model = TargetSummary.model_validate({
        "target_table": "t",
        "insert_count": 1,
        "experimental_field": "future_use",
    })
    dumped = model.model_dump()
    assert "experimental_field" not in dumped
    assert dumped["target_table"] == "t"


# ─── TargetOperation：DML Literal ─────────────────────────────────────────────


def test_target_operation_invalid_dml_type_raises() -> None:
    with pytest.raises(ValidationError):
        TargetOperation.model_validate({
            "order": 1,
            "target_table": "t",
            "dml_type": "DROP",  # 不在 DmlType 枚举
        })


def test_target_operation_round_trip() -> None:
    payload = {
        "order": 5,
        "target_table": "dwd.t_etl_jy",
        "dml_type": "INSERT",
        "has_where": False,
        "title": "集中交易",
    }
    assert TargetOperation.model_validate(payload).model_dump() == payload


# ─── ProcessStep：parse_status Literal ────────────────────────────────────────


def test_process_step_parse_status_literal() -> None:
    """parsed / unsupported / unknown 三个值都接受，其它拒绝。"""
    base = {"segment_index": "1", "line_start": 10, "line_end": 20}
    for status in ("parsed", "unsupported", "unknown"):
        ProcessStep.model_validate({**base, "parse_status": status})

    with pytest.raises(ValidationError):
        ProcessStep.model_validate({**base, "parse_status": "broken"})


# ─── AIInferredEdge：confidence 拦截 high ─────────────────────────────────────


def test_ai_inferred_edge_high_confidence_coerced_to_low() -> None:
    """6 不变量第 3 条：AI 推断永远不能 high；非 {low, medium} 一律降为 low。
    匹配现有 _validate_and_filter_edges 行为（_VALID_CONFIDENCE = {low, medium}）。
    """
    edge = AIInferredEdge.model_validate({
        "target_table": "dwd.t",
        "source_table": "ods.s",
        "confidence": "high",
    })
    assert edge.confidence == "low"


def test_ai_inferred_edge_invalid_confidence_coerced_to_low() -> None:
    """None / 非合法字符串 → low（防御式，不让坏 AI 输出拖崩主流程）。"""
    edge_none = AIInferredEdge.model_validate({
        "target_table": "dwd.t",
        "confidence": None,
    })
    assert edge_none.confidence == "low"
    edge_bogus = AIInferredEdge.model_validate({
        "target_table": "dwd.t",
        "confidence": "absolute",
    })
    assert edge_bogus.confidence == "low"


def test_ai_inferred_edge_invalid_dml_type_raises() -> None:
    with pytest.raises(ValidationError):
        AIInferredEdge.model_validate({
            "target_table": "dwd.t",
            "dml_type": "DROP",
        })


def test_ai_inferred_edge_round_trip_real_shape() -> None:
    """喂 _validate_and_filter_edges 真实输出，dump 等价。"""
    payload = {
        "source_table": "ods.t1",
        "target_table": "dwd.t2",
        "dml_type": "INSERT",
        "source_columns": ["t1.id", "t1.name"],
        "target_columns": ["id", "name"],
        "confidence": "low",
        "reason": "AI 推断（无附加说明）",
        "evidence": "EXECUTE IMMEDIATE 'INSERT INTO ...'",
        "fragment_index": 2,
        "source_kind": "dynamic_sql",
        "is_ai_inferred": True,
    }
    assert AIInferredEdge.model_validate(payload).model_dump() == payload


# ─── AIColumnHint：confidence 拦截 + 真实形态 ─────────────────────────────────


def test_ai_column_hint_high_confidence_coerced() -> None:
    hint = AIColumnHint.model_validate({
        "column": "user_id",
        "suggested_table": "dim.user",
        "confidence": "HIGH",  # 大写也拦
    })
    assert hint.confidence == "low"


# ─── AIInferenceResult：信封 round-trip ───────────────────────────────────────


def test_ai_inference_result_envelope_round_trip() -> None:
    """完整 result["ai_inferred"] 形态喂进去 → dump 还原。"""
    payload = {
        "edges": [{
            "source_table": "ods.s",
            "target_table": "dwd.t",
            "dml_type": "INSERT",
            "source_columns": [],
            "target_columns": [],
            "confidence": "medium",
            "reason": "动态 SQL 拼出来",
            "evidence": "EXECUTE IMMEDIATE v_sql",
            "fragment_index": 0,
            "source_kind": "dynamic_sql",
            "is_ai_inferred": True,
        }],
        "column_hints": [{
            "column": "trade_dt",
            "suggested_table": "dim.calendar",
            "confidence": "low",
            "reason": "命名匹配",
            "evidence": "WHERE trade_dt = ...",
            "source_kind": "column_attribution",
            "is_ai_inferred": True,
        }],
        "warnings": [{"type": "ai_inference_skipped", "message": "no provider"}],
        "trigger_count": 1,
        "filtered_count": 0,
    }
    model = AIInferenceResult.model_validate(payload)
    dumped = model.model_dump()
    assert dumped == payload


# ─── populate_by_name：alias 双向 ─────────────────────────────────────────────


def test_table_ref_schema_alias_both_directions() -> None:
    """schema 是 Python 关键字，字段名 schema_ + alias='schema'。
    populate_by_name=True：两个名字都能进。
    """
    by_alias = TableRef.model_validate({"name": "t", "schema": "ods"})
    by_field = TableRef.model_validate({"name": "t", "schema_": "ods"})
    assert by_alias.schema_ == "ods"
    assert by_field.schema_ == "ods"
    # by_alias=True 时 dump 用 alias key
    assert by_alias.model_dump(by_alias=True)["schema"] == "ods"


# ─── LineageReport：默认值 + 嵌套自动转换 ─────────────────────────────────────


def test_lineage_report_empty_defaults() -> None:
    """空 dict 喂进去，所有字段都有合理默认。"""
    report = LineageReport.model_validate({})
    assert report.statement_count == 0
    assert report.target_summary == []
    assert report.semantic_lineage == {}
    # ai_inferred / ai_enrichment 不在 LineageReport 字段表 —— 由
    # _attach_ai_inference 单独注入；这里 dump 不会自动引入 None 键。
    assert "ai_inferred" not in report.model_dump()


def test_lineage_report_target_summary_auto_coerced() -> None:
    """target_summary list[dict] 自动转 list[TargetSummary]。"""
    report = LineageReport.model_validate({
        "target_summary": [{
            "target_table": "dwd.t",
            "insert_count": 2,
            "refresh_mode": "append",
        }],
    })
    assert len(report.target_summary) == 1
    item = report.target_summary[0]
    assert isinstance(item, TargetSummary)
    assert item.refresh_mode == "append"


def test_lineage_report_passes_through_ai_inferred_extra_field() -> None:
    """ai_inferred / ai_enrichment 不是 typed 字段，通过 extra='allow' 透传。"""
    inferred_dict = {"edges": [], "trigger_count": 0}
    report = LineageReport.model_validate({"ai_inferred": inferred_dict})
    dumped = report.model_dump()
    assert dumped["ai_inferred"] == inferred_dict


def test_ai_inference_result_validates_at_attach_site() -> None:
    """envelope 字段不再 typed，但 _attach_ai_inference 在写入前会用
    AIInferenceResult.model_validate 单独校验，所以 confidence 拦截 high
    依然生效（在该处而非 LineageReport 处）。
    """
    payload = {"edges": [{
        "target_table": "dwd.t",
        "confidence": "high",  # 拦截到 low（最保守）
    }]}
    coerced = AIInferenceResult.model_validate(payload).model_dump()
    assert coerced["edges"][0]["confidence"] == "low"


# ─── ColumnEdge：confidence 默认值 ─────────────────────────────────────────────


def test_column_edge_defaults_and_round_trip() -> None:
    payload = {
        "target_column": "amount",
        "source_columns": ["s.amount"],
        "transform": "类型转换",
        "confidence": "high",
        "reason": "CAST",
    }
    edge = ColumnEdge.model_validate(payload)
    assert edge.confidence == "high"
    assert edge.model_dump() == payload
