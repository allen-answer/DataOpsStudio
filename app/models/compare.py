"""对比任务 schema：CompareRules / RunLimits / CompareTask(Create) +
执行结果 CompareSummary / CompareResult / HistoryItem。"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.common import SourceKind, SqlMode


class CompareRules(BaseModel):
    ignore_columns: list[str] = Field(default_factory=list)
    column_mappings: dict[str, str] = Field(default_factory=dict)
    numeric_tolerance: float | None = Field(default=None, ge=0)
    trim_strings: bool = False
    case_insensitive: bool = False
    empty_as_null: bool = False


class RunLimits(BaseModel):
    # Hard ceilings prevent runaway memory use. 10M rows × ~1KB ≈ 10 GB —
    # already above what we want a single compare task to load. Users who
    # need more should switch to stream_compare mode.
    max_rows: int = Field(default=100000, gt=0, le=10_000_000)
    export_max_rows: int = Field(default=50000, gt=0, le=5_000_000)
    fetch_chunk_size: int = Field(default=5000, gt=0, le=200_000)
    stream_compare: bool = False


class CompareTask(BaseModel):
    id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    source_kind: SourceKind = SourceKind.SQL
    target_kind: SourceKind = SourceKind.SQL
    source_id: str = ""
    target_id: str = ""
    sql_mode: SqlMode = SqlMode.SINGLE
    source_sql: str = ""
    target_sql: str = ""
    source_excel_path: str = ""
    source_sheet: str = ""
    source_header_row: int = 1
    target_excel_path: str = ""
    target_sheet: str = ""
    target_header_row: int = 1
    key_columns: list[str] = Field(default_factory=list)
    rules: CompareRules = Field(default_factory=CompareRules)
    limits: RunLimits = Field(default_factory=RunLimits)

    @model_validator(mode="after")
    def validate_inputs(self) -> "CompareTask":
        # 持久化 schema 跑 lenient 校验：允许加载历史任务（即使违反新加的混合规则），
        # 否则一条非法 task 会让 /api/bootstrap 整个 500，老用户配置全锁死。
        # 创建/更新走 CompareTaskCreate 仍是 strict。
        _validate_compare_inputs(self, strict=False)
        return self


class CompareTaskCreate(BaseModel):
    name: str = Field(..., min_length=1)
    source_kind: SourceKind = SourceKind.SQL
    target_kind: SourceKind = SourceKind.SQL
    source_id: str = ""
    target_id: str = ""
    sql_mode: SqlMode = SqlMode.SINGLE
    source_sql: str = ""
    target_sql: str = ""
    source_excel_path: str = ""
    source_sheet: str = ""
    source_header_row: int = 1
    target_excel_path: str = ""
    target_sheet: str = ""
    target_header_row: int = 1
    key_columns: list[str] = Field(default_factory=list)
    rules: CompareRules = Field(default_factory=CompareRules)
    limits: RunLimits = Field(default_factory=RunLimits)

    @model_validator(mode="after")
    def validate_inputs(self) -> "CompareTaskCreate":
        _validate_compare_inputs(self)
        return self


def _validate_compare_inputs(task: Any, *, strict: bool = True) -> None:
    """Per-side input validation. SQL kind requires datasource + SQL;
    Excel kind requires an uploaded file path. key_columns always required.

    `strict=True` adds 2 cross-field rules used only on create/update:
    - single SQL mode requires both sides to be SQL (the same SELECT runs on
      both datasources). Mixing Excel into single-mode makes no sense.
    - stream_compare requires both sides to be ordered SQL streams. Excel
      readers don't guarantee key ordering, so disable stream_compare when
      either side is Excel.

    `strict=False` is used by `CompareTask` (the persisted shape) so that
    legacy task JSON on disk that violates the new cross-field rules still
    loads. The user can re-save it through the UI; that path runs strict
    validation on `CompareTaskCreate`.
    """
    if task.source_kind == SourceKind.SQL:
        if not task.source_id.strip():
            raise ValueError("source_id is required for SQL source")
        if not task.source_sql.strip():
            raise ValueError("source_sql is required for SQL source")
    elif task.source_kind == SourceKind.EXCEL:
        if not task.source_excel_path.strip():
            raise ValueError("source_excel_path is required for Excel source")

    if task.target_kind == SourceKind.SQL:
        # In single SQL mode the source SQL is reused on target side, so target_id alone is enough.
        if not task.target_id.strip():
            raise ValueError("target_id is required for SQL target")
        if task.sql_mode == SqlMode.DOUBLE and not task.target_sql.strip():
            raise ValueError("target_sql is required in double SQL mode")
    elif task.target_kind == SourceKind.EXCEL:
        if not task.target_excel_path.strip():
            raise ValueError("target_excel_path is required for Excel target")

    if strict:
        # single SQL mode + Excel side is contradictory: there's no shared SELECT
        # to reuse on the Excel reader. Force the user to pick double mode.
        if task.sql_mode == SqlMode.SINGLE and (
            task.source_kind == SourceKind.EXCEL or task.target_kind == SourceKind.EXCEL
        ):
            raise ValueError(
                "single SQL mode does not support Excel inputs; switch to double SQL mode"
            )

        # stream_compare requires both sides to be SQL ordered by primary key.
        # Excel readers buffer the whole sheet and don't preserve key ordering.
        if task.limits.stream_compare and (
            task.source_kind == SourceKind.EXCEL or task.target_kind == SourceKind.EXCEL
        ):
            raise ValueError(
                "stream_compare requires SQL on both sides; Excel inputs cannot be streamed"
            )

    if not task.key_columns:
        raise ValueError("key_columns is required")


class CompareSummary(BaseModel):
    only_source: int
    only_target: int
    diff: int
    same: int


class CompareResult(BaseModel):
    run_id: str
    task_id: str
    summary: CompareSummary
    result_path: str
    result_filename: str
    excel_path: str
    excel_filename: str
    task_name: str = ""
    started_at: str = ""
    elapsed_seconds: float = 0
    source_rows: int = 0
    target_rows: int = 0
    samples: dict[Literal["only_source", "only_target", "diff", "same"], list[dict[str, Any]]]


class HistoryItem(BaseModel):
    """历史结果项（来自 /api/history）。compare 与 lineage 类型共用同一接口
    返回，字段集略有差异（lineage 形态多 read_tables / write_tables / 等
    汇总字段），所以放宽 extra=allow 而不是把全部 lineage 字段都列出来。"""
    model_config = ConfigDict(extra="allow")

    run_id: str
    task_id: str = ""
    task_name: str = ""
    started_at: str = ""
    elapsed_seconds: float = 0
    source_rows: int = 0
    target_rows: int = 0
    summary: dict[str, Any] = Field(default_factory=dict)
    result_filename: str = ""
    excel_filename: str = ""
    sort_time: str = ""
    type: str = "compare"   # "compare" | "lineage"
