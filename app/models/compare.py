"""对比任务 schema：CompareRules / RunLimits / CompareTask(Create) +
执行结果 CompareSummary / CompareResult / HistoryItem。"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.common import SourceKind, SqlMode


class CompareRules(BaseModel):
    ignore_columns: list[str] = Field(default_factory=list)
    column_mappings: dict[str, str] = Field(default_factory=dict)
    schema_policy: Literal["warn", "strict"] = "warn"
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
    # 结果落盘格式（详见 docs/COMPARE_RESULT_STORAGE.md）：
    # - "json"（默认）：单文件 results/<run_id>.json + xlsx —— 老格式向后兼容
    # - "parquet"：目录 results/<run_id>/{meta.json, *.parquet}，配合 reader
    #   API（PR2 提供）按桶分页读。大结果场景 opt-in。
    result_format: Literal["json", "parquet"] = "json"
    # 仅在 result_format="parquet" 时生效：
    # - False（默认）：same 桶不落 parquet，只在 meta.json 记 count + sample
    # - True：same 桶跟其它三桶一样全量落 parquet
    persist_same_bucket: bool = False
    # 仅在 result_format="parquet" 时生效：meta.json 里每桶 sample 行数
    same_sample_rows: int = Field(default=100, ge=0, le=10_000)


# 字段惯例：
# - SQL 端：source_id + source_sql / target_id + target_sql
# - Excel 端：source_excel_path + source_sheet + source_header_row（Excel 特有 sheet）
# - CSV / Parquet 端：source_file_path（通用文件路径）
#   * CSV 还用 source_file_encoding + source_csv_delimiter + source_header_row
#   * Parquet 不用 encoding/delimiter/header_row（自描述）
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
    # CSV / Parquet 通用文件路径 + CSV 特有 encoding/delimiter
    source_file_path: str = ""
    source_file_encoding: str = "utf-8-sig"
    source_csv_delimiter: str = ","
    target_file_path: str = ""
    target_file_encoding: str = "utf-8-sig"
    target_csv_delimiter: str = ","
    key_columns: list[str] = Field(default_factory=list)
    rules: CompareRules = Field(default_factory=CompareRules)
    limits: RunLimits = Field(default_factory=RunLimits)
    project_id: str = ""

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
    source_file_path: str = ""
    source_file_encoding: str = "utf-8-sig"
    source_csv_delimiter: str = ","
    target_file_path: str = ""
    target_file_encoding: str = "utf-8-sig"
    target_csv_delimiter: str = ","
    key_columns: list[str] = Field(default_factory=list)
    rules: CompareRules = Field(default_factory=CompareRules)
    limits: RunLimits = Field(default_factory=RunLimits)
    project_id: str = ""

    @model_validator(mode="after")
    def validate_inputs(self) -> "CompareTaskCreate":
        _validate_compare_inputs(self)
        return self


# 文件型 source kind —— 跟 SQL 互斥时统一引用
_FILE_KINDS = frozenset({SourceKind.EXCEL, SourceKind.CSV, SourceKind.PARQUET})


def _validate_compare_inputs(task: Any, *, strict: bool = True) -> None:
    """Per-side input validation. SQL kind requires datasource + SQL;
    Excel kind requires an uploaded file path. key_columns always required.

    `strict=True` adds 2 cross-field rules used only on create/update:
    - single SQL mode requires both sides to be SQL (the same SELECT runs on
      both datasources). Mixing file sources into single-mode makes no sense.
    - stream_compare requires both sides to be ordered SQL streams.

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
    elif task.source_kind in (SourceKind.CSV, SourceKind.PARQUET):
        if not task.source_file_path.strip():
            raise ValueError(f"source_file_path is required for {task.source_kind.value} source")

    if task.target_kind == SourceKind.SQL:
        # In single SQL mode the source SQL is reused on target side, so target_id alone is enough.
        if not task.target_id.strip():
            raise ValueError("target_id is required for SQL target")
        if task.sql_mode == SqlMode.DOUBLE and not task.target_sql.strip():
            raise ValueError("target_sql is required in double SQL mode")
    elif task.target_kind == SourceKind.EXCEL:
        if not task.target_excel_path.strip():
            raise ValueError("target_excel_path is required for Excel target")
    elif task.target_kind in (SourceKind.CSV, SourceKind.PARQUET):
        if not task.target_file_path.strip():
            raise ValueError(f"target_file_path is required for {task.target_kind.value} target")

    if strict:
        # single SQL mode + 任一侧是文件源（Excel / CSV / Parquet）：互斥 ——
        # 单 SQL 是"同一段 SELECT 在源/目标都跑一遍"，文件没法跑 SQL。
        if task.sql_mode == SqlMode.SINGLE and (
            task.source_kind in _FILE_KINDS or task.target_kind in _FILE_KINDS
        ):
            raise ValueError(
                "single SQL mode does not support file inputs (Excel/CSV/Parquet); "
                "switch to double SQL mode"
            )

        # stream_compare 要求两边都是 SQL 按主键有序流；文件 reader 都不保证。
        if task.limits.stream_compare and (
            task.source_kind in _FILE_KINDS or task.target_kind in _FILE_KINDS
        ):
            raise ValueError(
                "stream_compare requires SQL on both sides; file inputs cannot be streamed"
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
    schema_report: dict[str, Any] = Field(default_factory=dict)
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
