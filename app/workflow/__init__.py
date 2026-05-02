"""作业流执行运行时（节点 runner + 注册表）。

引擎层（app.services.workflow_engine）调度执行；本包只关心单个节点怎么跑。
新增节点类型：
    1. app.models.workflow.WorkflowNodeType 加值
    2. app.workflow.nodes.<type>.py 写 runner
    3. app.workflow.registry.NODE_RUNNERS 注册
"""
