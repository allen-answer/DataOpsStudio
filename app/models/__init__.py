"""app.models 包：按领域拆分的 Pydantic schema。

以前是 `app/models.py` 单文件 540 行，加几个新字段就要在大文件里翻半天。
现在按领域拆成 5 个子模块。这个 __init__.py re-export 全部老符号，所有
`from app.models import X` 的代码不用动。

新增 schema：写到对应领域子模块（common / datasource / compare / workflow
/ responses），然后在这里加一行 import。
"""
from __future__ import annotations

# --- 共用枚举 ---
from app.models.common import (
    DatabaseType,
    SourceKind,
    SqlMode,
)

# --- 数据源 ---
from app.models.datasource import (
    DataSource,
    DataSourceCreate,
)

# --- 对比任务 + 结果 ---
from app.models.compare import (
    CompareResult,
    CompareRules,
    CompareSummary,
    CompareTask,
    CompareTaskCreate,
    HistoryItem,
    RunLimits,
)

# --- 作业流（节点 / 资产 / Workflow / 运行 / Artifact / 摘要） ---
from app.models.workflow import (
    Artifact,
    ArtifactType,
    AssetKind,
    AssetRef,
    JobInfo,
    NodeRunStatus,
    Workflow,
    WorkflowCreate,
    WorkflowNode,
    WorkflowNodeRun,
    WorkflowNodeType,
    WorkflowRun,
    WorkflowRunStatus,
    WorkflowRunSummary,
    WorkflowStatus,
    WorkflowTemplate,
    WorkflowTemplateCreate,
    WorkflowTemplateInstantiate,
)

# --- API 响应 schema ---
from app.models.responses import (
    BootstrapResponse,
    ConnectionTestResult,
    DriverInfo,
    ExcelUploadResponse,
    LineageAnalyzeResult,
    LineageBatchAnalyzeResponse,
    LineageBatchExports,
    LineageBatchResult,
    LineageBatchSummary,
    OkResponse,
    PreviewColumnsResponse,
    PreviewRowsResponse,
    SqlAssistResponse,
)


__all__ = [
    # common
    "DatabaseType", "SourceKind", "SqlMode",
    # datasource
    "DataSource", "DataSourceCreate",
    # compare
    "CompareResult", "CompareRules", "CompareSummary", "CompareTask",
    "CompareTaskCreate", "HistoryItem", "RunLimits",
    # workflow
    "Artifact", "ArtifactType", "AssetKind", "AssetRef", "JobInfo",
    "NodeRunStatus", "Workflow", "WorkflowCreate", "WorkflowNode",
    "WorkflowNodeRun", "WorkflowNodeType", "WorkflowRun", "WorkflowRunStatus",
    "WorkflowRunSummary", "WorkflowStatus", "WorkflowTemplate",
    "WorkflowTemplateCreate", "WorkflowTemplateInstantiate",
    # responses
    "BootstrapResponse", "ConnectionTestResult", "DriverInfo",
    "ExcelUploadResponse", "LineageAnalyzeResult",
    "LineageBatchAnalyzeResponse", "LineageBatchExports",
    "LineageBatchResult", "LineageBatchSummary", "OkResponse",
    "PreviewColumnsResponse", "PreviewRowsResponse", "SqlAssistResponse",
]
