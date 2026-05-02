"""节点 runner 注册表。

`NODE_RUNNERS[type] -> runner` —— workflow_engine 据此调度。新增节点类型：
    1. app/workflow/nodes/<type>.py 写 runner（签名 `(config, variables, **_) -> dict`）
    2. 在下面 import 并加进 NODE_RUNNERS 字典
    3. 加 app/models/workflow.py WorkflowNodeType 对应枚举值

runner 签名：
    (config, variables, *, outputs=None, depends_on=None, run_id=None) -> dict
其中 outputs / depends_on / run_id 是 excel_export 这类需要读上游产物的
runner 用得到的，其它 runner 通过 **_ 吃掉。
"""
from __future__ import annotations

from typing import Any, Callable

from app.models import WorkflowNodeType
from app.workflow.nodes.compare import run_compare_node
from app.workflow.nodes.excel_export import run_excel_export_node
from app.workflow.nodes.http import run_http_node
from app.workflow.nodes.lineage import run_lineage_node
from app.workflow.nodes.params import run_params_node


NodeRunner = Callable[..., dict[str, Any]]


NODE_RUNNERS: dict[WorkflowNodeType, NodeRunner] = {
    WorkflowNodeType.PARAMS:       run_params_node,
    WorkflowNodeType.COMPARE:      run_compare_node,
    WorkflowNodeType.LINEAGE:      run_lineage_node,
    WorkflowNodeType.HTTP:         run_http_node,
    WorkflowNodeType.EXCEL_EXPORT: run_excel_export_node,
}
