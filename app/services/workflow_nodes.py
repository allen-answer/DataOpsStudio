"""向后兼容 shim：原本 536 行的 runner 大文件已经按节点类型拆到
`app/workflow/nodes/` + `app/workflow/registry.py`。

老路径 `from app.services.workflow_nodes import NODE_RUNNERS, NodeRunner,
run_X_node` 仍然能用，所有调用方（workflow_engine + tests）零改动。

新增节点类型 → 直接去 app/workflow/，不要往这里加东西。
"""
from __future__ import annotations

from app.workflow.nodes.compare import run_compare_node
from app.workflow.nodes.excel_export import run_excel_export_node
from app.workflow.nodes.http import run_http_node
from app.workflow.nodes.lineage import run_lineage_node
from app.workflow.nodes.params import run_params_node
from app.workflow.registry import NODE_RUNNERS, NodeRunner


__all__ = [
    "NODE_RUNNERS",
    "NodeRunner",
    "run_compare_node",
    "run_excel_export_node",
    "run_http_node",
    "run_lineage_node",
    "run_params_node",
]
