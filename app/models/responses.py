"""杂项 API 响应 schema —— 不属于业务实体本身（DataSource / CompareTask /
Workflow），是 endpoint 响应专用契约。挂 response_model 让 /docs 给前端
一份准确的形状描述。

Lineage 分析的响应也一并放这里——血缘是工具型功能，没有持久化实体。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.models.compare import CompareTask
from app.models.datasource import DataSource
from app.models.workflow import Workflow


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
    target_summary: list[dict[str, Any]] = Field(default_factory=list)
    table_roles: list[dict[str, Any]] = Field(default_factory=list)
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
