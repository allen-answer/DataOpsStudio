"""一次性种子脚本：注入若干含 lineage 节点的 workflow_run，让 AssetDetailView
有字段表 + ✨溯源 chip 可点。

跑法（容器内）：
    docker exec dataops-studio python scripts/seed_demo_lineage.py

跑完后访问 http://localhost:8010/spa/#/assets/table/dws.user_metric_daily 应该
能看到 4 行字段，每行带紫色「✨溯源」chip（需 admin/admin 登录）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models import (
    NodeRunStatus, WorkflowNodeRun, WorkflowNodeType,
    WorkflowRun, WorkflowRunStatus,
)
from app.services import workflow_history


# 三跳血缘链：ods.t_users → dwd.user_clean → dws.user_metric_daily
DEMO_RUNS = [
    {
        "run_id": "demo-trace-run-1",
        "workflow_id": "demo-wf-1",
        "workflow_name": "demo: 用户事实表 ETL",
        "insert_mappings": [
            # ods.t_users → dwd.user_clean
            {"target_table": "dwd.user_clean", "target_column": "user_id",
             "source_tables": ["ods.t_users"], "source_columns": ["user_id"],
             "transforms": []},
            {"target_table": "dwd.user_clean", "target_column": "username",
             "source_tables": ["ods.t_users"], "source_columns": ["username"],
             "transforms": []},
            {"target_table": "dwd.user_clean", "target_column": "register_dt",
             "source_tables": ["ods.t_users"], "source_columns": ["create_time"],
             "transforms": ["DATE_TRUNC"]},
            {"target_table": "dwd.user_clean", "target_column": "is_active",
             "source_tables": ["ods.t_users"], "source_columns": ["status"],
             "transforms": ["CASE_WHEN"]},
        ],
    },
    {
        "run_id": "demo-trace-run-2",
        "workflow_id": "demo-wf-2",
        "workflow_name": "demo: 日活指标聚合",
        "insert_mappings": [
            # dwd.user_clean → dws.user_metric_daily
            {"target_table": "dws.user_metric_daily", "target_column": "user_id",
             "source_tables": ["dwd.user_clean"], "source_columns": ["user_id"],
             "transforms": []},
            {"target_table": "dws.user_metric_daily", "target_column": "active_flag",
             "source_tables": ["dwd.user_clean"], "source_columns": ["is_active"],
             "transforms": []},
            {"target_table": "dws.user_metric_daily", "target_column": "register_dt",
             "source_tables": ["dwd.user_clean"], "source_columns": ["register_dt"],
             "transforms": []},
            {"target_table": "dws.user_metric_daily", "target_column": "last_login_dt",
             "source_tables": ["dwd.user_clean"], "source_columns": ["register_dt"],
             "transforms": []},
        ],
    },
]


def main() -> None:
    workflow_history.invalidate_run_payloads_cache()
    for spec in DEMO_RUNS:
        run = WorkflowRun(
            run_id=spec["run_id"],
            workflow_id=spec["workflow_id"],
            workflow_name=spec["workflow_name"],
            status=WorkflowRunStatus.SUCCESS,
            nodes=[
                WorkflowNodeRun(
                    node_id="lineage_n1",
                    type=WorkflowNodeType.LINEAGE,
                    name="解析血缘",
                    status=NodeRunStatus.SUCCESS,
                    output={"insert_mappings": spec["insert_mappings"]},
                    elapsed_seconds=0.5,
                ),
            ],
            started_at="2026-05-11T09:00:00",
            finished_at="2026-05-11T09:00:01",
            elapsed_seconds=1.0,
        )
        path = workflow_history.persist_workflow_run(run)
        print(f"  ✓ wrote {path}")
    workflow_history.invalidate_run_payloads_cache()
    print("\n种子完成。访问 http://localhost:8010/spa/#/assets/table/dws.user_metric_daily")
    print("或 http://localhost:8010/spa/#/assets/table/dwd.user_clean 查看字段表。")


if __name__ == "__main__":
    main()
