from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class DatabaseType(str, Enum):
    DM = "DM"
    MYSQL = "MySQL"
    ORACLE = "Oracle"
    DB2 = "DB2"


class SqlMode(str, Enum):
    SINGLE = "single"
    DOUBLE = "double"


class SourceKind(str, Enum):
    SQL = "sql"
    EXCEL = "excel"


class CompareRules(BaseModel):
    ignore_columns: list[str] = Field(default_factory=list)
    column_mappings: dict[str, str] = Field(default_factory=dict)
    numeric_tolerance: float | None = Field(default=None, ge=0)
    trim_strings: bool = False
    case_insensitive: bool = False
    empty_as_null: bool = False


class RunLimits(BaseModel):
    max_rows: int = Field(default=100000, gt=0)
    export_max_rows: int = Field(default=50000, gt=0)
    fetch_chunk_size: int = Field(default=5000, gt=0)
    stream_compare: bool = False


class DataSource(BaseModel):
    id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    db_type: DatabaseType
    host: str = Field(..., min_length=1)
    port: int = Field(..., gt=0, le=65535)
    database: str = ""
    username: str = ""
    password: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)


class DataSourceCreate(BaseModel):
    name: str = Field(..., min_length=1)
    db_type: DatabaseType
    host: str = Field(..., min_length=1)
    port: int = Field(..., gt=0, le=65535)
    database: str = ""
    username: str = ""
    password: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)


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
        _validate_compare_inputs(self)
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


def _validate_compare_inputs(task: Any) -> None:
    """Per-side input validation. SQL kind requires datasource + SQL;
    Excel kind requires an uploaded file path. key_columns always required."""
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
