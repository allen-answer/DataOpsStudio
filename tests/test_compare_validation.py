"""CompareTask / CompareTaskCreate 字段校验测试。

覆盖 4 种 source_kind / target_kind 组合 + sql_mode + stream_compare 互斥规则。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.compare import CompareRules, CompareTaskCreate, RunLimits


def _make_create(**overrides):
    """构造一个最小可保存的 SQL+SQL 单 SQL 任务，方便各测试 patch 覆盖。"""
    base = dict(
        name="t",
        source_kind="sql",
        target_kind="sql",
        source_id="ds1",
        target_id="ds2",
        sql_mode="single",
        source_sql="select 1",
        key_columns=["id"],
    )
    base.update(overrides)
    return CompareTaskCreate(**base)


# ─── 基础合法路径 ─────────────────────────────────────────────────────────────


def test_sql_vs_sql_single_mode_ok():
    task = _make_create()
    assert task.sql_mode.value == "single"
    assert task.source_kind.value == "sql"


def test_sql_vs_sql_double_mode_requires_target_sql():
    with pytest.raises(ValidationError) as exc:
        _make_create(sql_mode="double", target_sql="")
    assert "target_sql is required" in str(exc.value)


def test_excel_vs_excel_double_mode_ok():
    task = _make_create(
        source_kind="excel", target_kind="excel",
        source_excel_path="/tmp/a.xlsx", target_excel_path="/tmp/b.xlsx",
        sql_mode="double",
        source_id="", target_id="", source_sql="",
    )
    assert task.source_kind.value == "excel"


def test_sql_vs_excel_double_mode_ok():
    task = _make_create(
        target_kind="excel", target_excel_path="/tmp/b.xlsx",
        sql_mode="double", target_id="", target_sql="",
    )
    assert task.source_kind.value == "sql"
    assert task.target_kind.value == "excel"


# ─── 新加的混合校验：single + Excel 互斥 ──────────────────────────────────────


def test_single_mode_with_excel_source_rejected():
    with pytest.raises(ValidationError) as exc:
        _make_create(
            source_kind="excel",
            source_excel_path="/tmp/a.xlsx",
            source_id="", source_sql="",
            sql_mode="single",
        )
    assert "single SQL mode does not support Excel" in str(exc.value)


def test_single_mode_with_excel_target_rejected():
    with pytest.raises(ValidationError) as exc:
        _make_create(
            target_kind="excel",
            target_excel_path="/tmp/b.xlsx",
            target_id="",
            sql_mode="single",
        )
    assert "single SQL mode does not support Excel" in str(exc.value)


def test_single_mode_with_excel_on_both_sides_rejected():
    with pytest.raises(ValidationError) as exc:
        _make_create(
            source_kind="excel", target_kind="excel",
            source_excel_path="/tmp/a.xlsx", target_excel_path="/tmp/b.xlsx",
            source_id="", target_id="", source_sql="",
            sql_mode="single",
        )
    assert "single SQL mode does not support Excel" in str(exc.value)


# ─── 新加的混合校验：stream_compare + Excel 互斥 ──────────────────────────────


def test_stream_compare_with_sql_only_ok():
    """两边都是 SQL 时 stream_compare 合法。"""
    task = _make_create(
        sql_mode="double",
        target_sql="select 1",
        limits=RunLimits(stream_compare=True),
    )
    assert task.limits.stream_compare


def test_stream_compare_with_excel_source_rejected():
    with pytest.raises(ValidationError) as exc:
        _make_create(
            source_kind="excel", source_excel_path="/tmp/a.xlsx",
            source_id="", source_sql="",
            sql_mode="double", target_sql="select 1",
            limits=RunLimits(stream_compare=True),
        )
    assert "stream_compare requires SQL on both sides" in str(exc.value)


def test_stream_compare_with_excel_target_rejected():
    with pytest.raises(ValidationError) as exc:
        _make_create(
            target_kind="excel", target_excel_path="/tmp/b.xlsx",
            target_id="",
            sql_mode="double", target_sql="",
            limits=RunLimits(stream_compare=True),
        )
    assert "stream_compare requires SQL on both sides" in str(exc.value)


# ─── 既有规则的回归（确保新校验没破坏） ──────────────────────────────────────


def test_key_columns_required():
    with pytest.raises(ValidationError) as exc:
        _make_create(key_columns=[])
    assert "key_columns is required" in str(exc.value)


def test_sql_source_requires_datasource_and_sql():
    with pytest.raises(ValidationError):
        _make_create(source_id="")
    with pytest.raises(ValidationError):
        _make_create(source_sql="")


def test_excel_source_requires_path():
    with pytest.raises(ValidationError) as exc:
        _make_create(
            source_kind="excel", source_excel_path="",
            source_id="", source_sql="",
            sql_mode="double", target_sql="select 1",
        )
    assert "source_excel_path is required" in str(exc.value)
