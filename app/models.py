from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    # 局部重跑时，沿用上次 run 该节点的 output 而不重执行。值为 True 时
    # status 仍是 success，前端可据此打"复用上次结果"badge。
    reused: bool = False


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
    # 局部重跑：记录起源。None = 全量执行；否则指向上一次 run + 起跑节点。
    resumed_from: dict[str, str] | None = None


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
    """每个 DatabaseType 的驱动可用性。`installed_modules` 是当前 Python
    环境真装上的，`candidate_modules` 是该 db_type 支持的全部备选——前端
    可据此提示"装其中一个就能用"。"""
    available: bool
    installed_modules: list[str] = Field(default_factory=list)
    candidate_modules: list[str] = Field(default_factory=list)


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


# --- 杂项 API 响应（response_model 第三波）---
# 都是早期 dict 落地的轻量响应，加 schema 主要是让 /docs 给前端 / 第三方
# 一份准确契约。结构对齐底层服务返回，改字段时要两处一起改。

class OkResponse(BaseModel):
    """删除 / 取消等无返回数据的接口的统一形状。"""
    ok: bool = True


class ConnectionTestResult(BaseModel):
    """POST /api/datasources/{id}/test 返回。失败时直接 4xx，所以这里只
    描述成功形态。`sample` 是探测 SQL（select 1）拿到的第一行，主要给
    用户一个"驱动通了"的可视化反馈。"""
    ok: bool = True
    message: str = ""
    datasource: str = ""
    db_type: str = ""
    elapsed_seconds: float = 0
    sample: list[dict[str, Any]] = Field(default_factory=list)


class PreviewRowsResponse(BaseModel):
    """POST /api/tasks/{task_id}/preview 返回。`truncated=True` 表示行数
    达到 `limit` 上限，可能还有更多——前端据此显示 "已截断" 提示。"""
    side: str
    limit: int
    truncated: bool
    rows: list[dict[str, Any]] = Field(default_factory=list)


class PreviewColumnsResponse(BaseModel):
    """POST /api/preview/columns 返回。SQL kind 与 Excel kind 共用同一
    形状——只输出列名列表，元数据由调用方自行决定怎么用。"""
    columns: list[str] = Field(default_factory=list)


class SqlAssistResponse(BaseModel):
    """POST /api/sql/assist 返回。`readonly_ok` 反映 sql_guard 的判定，
    便于前端给只读保护的 SQL 标个绿勾；`converted_sql` 仅当请求带
    `target_dialect` 时才填，否则空串。"""
    readonly_ok: bool = True
    readonly_error: str = ""
    formatted_sql: str = ""
    converted_sql: str = ""
    output_columns: list[str] = Field(default_factory=list)
    key_candidates: list[str] = Field(default_factory=list)


class ExcelUploadResponse(BaseModel):
    """POST /api/uploads/excel 返回。`path` 是相对仓库根的存储路径，前端
    回填到任务表单后下次执行时以此 resolve。`columns_by_sheet` 是每个
    sheet 第 1 行作为 header 抽出来的列名，让用户立刻能选 key columns。"""
    path: str
    filename: str
    sheets: list[str] = Field(default_factory=list)
    columns_by_sheet: dict[str, list[str]] = Field(default_factory=dict)


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
