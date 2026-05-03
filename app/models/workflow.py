"""作业流相关 schema：节点 / 资产 / Workflow(Create) / 运行状态 + Artifact +
WorkflowRun / JobInfo / 摘要。"""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field


# --- 节点类型 ---

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


# --- 资产 ---

class AssetKind(str, Enum):
    """资产形态。`table` 是最常见的（表 / 视图 / 物化视图），`file` 给
    Excel / CSV / Parquet 输入输出留位，`stream` 给将来接 Kafka / CDC 留位。"""
    TABLE = "table"
    FILE = "file"
    STREAM = "stream"


class AssetRef(BaseModel):
    """作业流的输入 / 输出资产引用。`key` 是稳定标识（例如 schema.table 或
    存储路径），后续可以用它接血缘系统、做影响分析。`description` 给人看，
    UI 上可以展开成 hover 说明。"""
    key: str = Field(..., min_length=1)
    kind: AssetKind = AssetKind.TABLE
    description: str = ""


# --- Workflow 定义 ---

class WorkflowStatus(str, Enum):
    """作业流生命周期状态——和单次 run 状态不同，这是定义层。"""
    DRAFT = "draft"        # 编辑中，未上线
    ACTIVE = "active"      # 上线，按调度 / 手动可触发
    PAUSED = "paused"      # 暂停，调度跳过；手动可触发（运维场景常用）
    ARCHIVED = "archived"  # 归档，不再触发；保留历史和定义供查阅


class Workflow(BaseModel):
    id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    nodes: list[WorkflowNode] = Field(default_factory=list)
    default_variables: dict[str, str] = Field(default_factory=dict)
    # 元数据字段：UI 展示用，不影响执行。老数据缺这些字段会回落到默认值，
    # JsonStore 自动兼容。
    description: str = ""
    owner: str = ""
    tags: list[str] = Field(default_factory=list)
    schedule_cron: str = ""   # 留空 = 仅手动触发
    project: str = ""         # 所属项目分组（自由文本 tag），UI 列表筛选用
    project_id: str = ""      # 关联 Project.id（D-MVP 项目空间）
    status: WorkflowStatus = WorkflowStatus.DRAFT
    input_assets: list[AssetRef] = Field(default_factory=list)
    output_assets: list[AssetRef] = Field(default_factory=list)
    notifications: list[dict[str, Any]] = Field(default_factory=list)
    sensors: list[dict[str, Any]] = Field(default_factory=list)
    # Sensor 触发器：每项 {type: "file"|"workflow_success", config: {...}, enabled, name}
    # 调度器轮询 evaluate；命中即提交 workflow run
    triggers: list[dict[str, Any]] = Field(default_factory=list)


class WorkflowCreate(BaseModel):
    name: str = Field(..., min_length=1)
    nodes: list[WorkflowNode] = Field(default_factory=list)
    default_variables: dict[str, str] = Field(default_factory=dict)
    description: str = ""
    owner: str = ""
    tags: list[str] = Field(default_factory=list)
    schedule_cron: str = ""
    project: str = ""
    project_id: str = ""
    status: WorkflowStatus = WorkflowStatus.DRAFT
    input_assets: list[AssetRef] = Field(default_factory=list)
    output_assets: list[AssetRef] = Field(default_factory=list)
    notifications: list[dict[str, Any]] = Field(default_factory=list)
    sensors: list[dict[str, Any]] = Field(default_factory=list)
    triggers: list[dict[str, Any]] = Field(default_factory=list)


# --- 运行状态 ---

class WorkflowTemplateCreate(BaseModel):
    name: str = Field(..., min_length=1)
    description: str = ""
    category: str = ""
    tags: list[str] = Field(default_factory=list)
    workflow: WorkflowCreate
    created_at: str = ""
    updated_at: str = ""


class WorkflowTemplate(BaseModel):
    id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    description: str = ""
    category: str = ""
    tags: list[str] = Field(default_factory=list)
    workflow: WorkflowCreate
    created_at: str = ""
    updated_at: str = ""


class WorkflowTemplateInstantiate(BaseModel):
    name: str = ""
    project: str = ""
    owner: str = ""
    status: WorkflowStatus = WorkflowStatus.DRAFT


class NodeRunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class WorkflowRunStatus(str, Enum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


# --- Artifact ---

class ArtifactType(str, Enum):
    """节点产出文件的类型。前端据此选图标 / 决定怎么打开。"""
    EXCEL = "excel"
    JSON = "json"
    OTHER = "other"


class Artifact(BaseModel):
    """节点产出的可下载文件。`relative_path` 是相对 RESULTS_DIR 的路径，
    前端拼 /results/<relative_path> 即可下载。所有 artifacts 都归到
    results/workflow_runs/<run_id>/ 下面，删 run 时连带清理。"""
    id: str
    run_id: str
    node_id: str
    type: ArtifactType
    name: str
    relative_path: str
    size_bytes: int = 0
    created_at: str = ""
    description: str = ""


# --- 单次运行 ---

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
    # 外部集成的发送/回调结果。例：OpenLineage webhook emit result。
    integrations: dict[str, Any] = Field(default_factory=dict)

    @computed_field
    @property
    def artifacts(self) -> list[Artifact]:
        """顶层 artifacts 聚合：扁平收集所有节点 output.artifacts。
        前端可以直接读 run.artifacts 拿到下载列表，不用 re-walk nodes。
        老 run 没有 artifacts 字段时返回空列表。"""
        result: list[Artifact] = []
        for node_run in self.nodes:
            raw = (node_run.output or {}).get("artifacts")
            if not isinstance(raw, list):
                continue
            for item in raw:
                if not isinstance(item, dict):
                    continue
                try:
                    result.append(Artifact.model_validate(item))
                except Exception:
                    # 跳过形状不对的——别让一条坏数据拖垮整个 run 序列化
                    continue
        return result


# --- 异步 job + 摘要（API 响应用） ---

class JobInfo(BaseModel):
    """异步任务（compare 或 workflow run）的运行时状态。和 jobs.py 内部
    dict 结构保持对齐 —— 改字段记得两处一起改。"""
    job_id: str
    kind: Literal["compare", "task", "workflow"]
    task_id: str = ""
    workflow_id: str = ""
    variables: dict[str, str] = Field(default_factory=dict)
    status: Literal["queued", "running", "cancelling", "success", "failed", "cancelled"]
    stage: str = ""
    message: str = ""
    created_at: str = ""
    updated_at: str = ""
    expires_at: str = ""
    retry_count: int = 0
    max_retries: int = 0
    result: dict[str, Any] | None = None
    error: str = ""
    cancel_requested: bool = False
    # 局部重跑场景下记录起源（前端可据此显示 "局部重跑自 run X · from node Y"）
    resumed_from: dict[str, str] | None = None


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
