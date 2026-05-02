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
    # Hard ceilings prevent runaway memory use. 10M rows × ~1KB ≈ 10 GB —
    # already above what we want a single compare task to load. Users who
    # need more should switch to stream_compare mode.
    max_rows: int = Field(default=100000, gt=0, le=10_000_000)
    export_max_rows: int = Field(default=50000, gt=0, le=5_000_000)
    fetch_chunk_size: int = Field(default=5000, gt=0, le=200_000)
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


# --- Workflow (Phase 3 first slice) ---
# A workflow is an ordered list of nodes. Each node has a type that maps to
# a runner function (see app.services.workflow_nodes). String values inside
# node.config can reference workflow variables via ${var} placeholders.

class WorkflowNodeType(str, Enum):
    PARAMS        = "params"         # resolves typed parameters; emits resolved dict as output
    COMPARE       = "compare"        # runs an existing CompareTask by id
    LINEAGE       = "lineage"        # analyzes a SQL string via lineage_service
    HTTP          = "http"           # GET/POST a URL, useful for webhooks / notifications
    EXCEL_EXPORT  = "excel_export"   # multi-sheet Excel report from upstream node outputs


class WorkflowNode(BaseModel):
    id: str = Field(..., min_length=1)  # unique within the workflow
    type: WorkflowNodeType
    name: str = ""
    config: dict[str, Any] = Field(default_factory=dict)
    # Ids of upstream nodes that must succeed before this one runs. Empty
    # = no dependencies (root of the DAG). GitHub Actions `needs:` style.
    depends_on: list[str] = Field(default_factory=list)
    # Optional boolean expression. Empty = always run when upstream OK.
    # Evaluated AFTER variable interpolation; ${nodes.x.y} / ${var} resolve
    # to typed Python literals. False → node SKIPPED. See workflow_engine.
    when: str = ""


class Workflow(BaseModel):
    id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    nodes: list[WorkflowNode] = Field(default_factory=list)
    default_variables: dict[str, str] = Field(default_factory=dict)
    # 元数据字段：UI 展示用，不影响执行。老数据缺这些字段会回落到默认值，
    # JsonStore 自动兼容。资产 / 告警 / 调度状态等"派生"信息暂未下沉，
    # 待血缘 + 调度系统接入。
    description: str = ""
    owner: str = ""
    tags: list[str] = Field(default_factory=list)
    schedule_cron: str = ""   # 留空 = 仅手动触发


class WorkflowCreate(BaseModel):
    name: str = Field(..., min_length=1)
    nodes: list[WorkflowNode] = Field(default_factory=list)
    default_variables: dict[str, str] = Field(default_factory=dict)
    description: str = ""
    owner: str = ""
    tags: list[str] = Field(default_factory=list)
    schedule_cron: str = ""


class NodeRunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class WorkflowNodeRun(BaseModel):
    node_id: str
    type: WorkflowNodeType
    name: str = ""
    status: NodeRunStatus = NodeRunStatus.PENDING
    started_at: str = ""
    finished_at: str = ""
    elapsed_seconds: float = 0
    output: dict[str, Any] = Field(default_factory=dict)
    error: str = ""


class WorkflowRunStatus(str, Enum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class WorkflowRun(BaseModel):
    run_id: str
    workflow_id: str
    workflow_name: str = ""
    status: WorkflowRunStatus = WorkflowRunStatus.RUNNING
    variables: dict[str, str] = Field(default_factory=dict)
    nodes: list[WorkflowNodeRun] = Field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""
    elapsed_seconds: float = 0
    error: str = ""


# --- API response schemas ---
# 这些 schema 主要给 FastAPI response_model 用，让 /docs 给前端 / 第三方
# 一份准确的 API 契约。运行时仍然是 dict（jobs / workflow-runs 早期落盘
# 实现），FastAPI 会按 model_dump 顺序序列化校验。

class JobInfo(BaseModel):
    """异步任务（compare 或 workflow run）的运行时状态。和 jobs.py 内部
    dict 结构保持对齐 —— 改字段记得两处一起改。"""
    job_id: str
    kind: Literal["task", "workflow"]
    task_id: str = ""
    workflow_id: str = ""
    variables: dict[str, str] = Field(default_factory=dict)
    status: Literal["running", "success", "failed", "cancelled"]
    message: str = ""
    created_at: str = ""
    updated_at: str = ""
    result: dict[str, Any] | None = None
    error: str = ""
    cancel_requested: bool = False


class WorkflowRunSummary(BaseModel):
    """list_workflow_runs 摘要项，比完整 WorkflowRun 轻 —— 不含
    nodes[*].output 和 variables，只够列表展示用。"""
    run_id: str
    workflow_id: str = ""
    workflow_name: str = ""
    status: str = ""
    started_at: str = ""
    finished_at: str = ""
    elapsed_seconds: float = 0
    error: str = ""
    node_count: int = 0
    node_status_counts: dict[str, int] = Field(default_factory=dict)


class DriverInfo(BaseModel):
    available: bool
    modules: list[str] = Field(default_factory=list)


class BootstrapResponse(BaseModel):
    """单次首屏拉取：列出数据源 / 对比任务 / 作业流 / 历史 + 当前可用的 DB
    驱动 + 字典常量（db_types / sql_modes / history_sheets）。前端启动时调一次。"""
    datasources: list[DataSource] = Field(default_factory=list)
    tasks: list[CompareTask] = Field(default_factory=list)
    workflows: list[Workflow] = Field(default_factory=list)
    drivers: dict[str, DriverInfo] = Field(default_factory=dict)
    db_types: list[str] = Field(default_factory=list)
    sql_modes: list[str] = Field(default_factory=list)
    history: list[dict[str, Any]] = Field(default_factory=list)
    history_sheets: tuple[str, ...] | list[str] = Field(default_factory=list)


class HistoryItem(BaseModel):
    """compare 任务的历史结果项（来自 /api/history）。"""
    run_id: str
    task_id: str = ""
    task_name: str = ""
    started_at: str = ""
    elapsed_seconds: float = 0
    summary: CompareSummary | None = None
    result_filename: str = ""
    excel_filename: str = ""


# --- Lineage analyze response ---
# 顶层契约先固化；每条 list[dict] 项的字段太散（~80 个键，按 dialect / 输入
# 形态变化），暂保留 dict[str, Any]。后续如果哪个具体字段被前端深度依赖，
# 单独提取成 model。

class LineageAnalyzeResult(BaseModel):
    """单 SQL 血缘分析的响应（/api/lineage/analyze 和 analyze-form）。"""
    statement_count: int = 0
    tables: list[dict[str, Any]] = Field(default_factory=list)
    columns: list[dict[str, Any]] = Field(default_factory=list)
    insert_mappings: list[dict[str, Any]] = Field(default_factory=list)
    joins: list[dict[str, Any]] = Field(default_factory=list)
    filters: list[dict[str, Any]] = Field(default_factory=list)
    group_by: list[dict[str, Any]] = Field(default_factory=list)
    unions: list[dict[str, Any]] = Field(default_factory=list)
    variables: list[dict[str, Any]] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    dynamic_sql_count: int = 0
    dynamic_sql_segments: list[dict[str, Any]] = Field(default_factory=list)
    procedure_segments: list[dict[str, Any]] = Field(default_factory=list)
    graph_edges: list[dict[str, Any]] = Field(default_factory=list)
    graph_groups: list[dict[str, Any]] = Field(default_factory=list)
    parse_errors: list[dict[str, str]] = Field(default_factory=list)
    warnings: list[Any] = Field(default_factory=list)
    statements: list[dict[str, Any]] = Field(default_factory=list)


class LineageBatchSummary(BaseModel):
    """批量血缘分析的汇总卡片，前端 stats 区直接读这一段。"""
    files: int = 0
    success_files: int = 0
    failed_files: int = 0
    read_tables: int = 0
    write_tables: int = 0
    table_edges: int = 0
    script_edges: int = 0
    dag_cycles: int = 0
    write_conflicts: int = 0
    warnings: int = 0


class LineageBatchResult(BaseModel):
    """批量血缘分析的核心结果 (analyze_lineage_batch 直接返回)。"""
    file_count: int = 0
    files: list[dict[str, Any]] = Field(default_factory=list)
    table_edges: list[dict[str, Any]] = Field(default_factory=list)
    table_groups: list[dict[str, Any]] = Field(default_factory=list)
    script_edges: list[dict[str, Any]] = Field(default_factory=list)
    field_mappings: list[dict[str, Any]] = Field(default_factory=list)
    impact_analysis: dict[str, list[str]] = Field(default_factory=dict)
    dag: dict[str, Any] = Field(default_factory=dict)
    warnings: list[Any] = Field(default_factory=list)
    summary: LineageBatchSummary = Field(default_factory=LineageBatchSummary)


class LineageBatchExports(BaseModel):
    """批量分析落盘的 JSON / Excel 文件名（在 results/ 下，可直接走
    /results/{filename} 下载）。"""
    json_filename: str = ""
    excel_filename: str = ""


class LineageBatchAnalyzeResponse(BaseModel):
    """/api/lineage/batch/analyze 的完整响应：核心结果 + 落盘导出文件。"""
    result: LineageBatchResult
    exports: LineageBatchExports = Field(default_factory=LineageBatchExports)
