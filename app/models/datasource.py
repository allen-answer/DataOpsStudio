"""数据源 schema：DataSource(含 id)+ DataSourceCreate(无 id 创建用)。

Phase 14 #3 fail-safe 改造:
- environment 默认从 sandbox 改 unknown
  旧 datasources.json 缺字段时 pydantic 自动赋 unknown,所有高风险操作默认禁止。
  admin 必须手动到数据源管理页确认 environment 后才能解锁。
- 新增 8 个 allow_* 显式开关,默认全 False(允许只读 SELECT 是默认 True)。
  fail-safe:加新 ds 默认连 EXPLAIN 都跑不动,逼用户走 admin 显式打 sandbox/staging/
  prod 标签 + 开对应 allow_* 才能进一步操作。
- demo init_db 创建的样例 ds 必须显式把 sandbox + allow_* 全开。

策略矩阵详见 app/services/operation_policy.py:assert_operation_allowed。
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.common import DatabaseType


# 数据源环境标签。fail-safe:旧 ds / 新建 ds 默认 unknown,所有高风险操作拒。
# admin 改成 sandbox/staging/prod 后才能开对应能力。
DatasourceEnvironment = Literal["unknown", "sandbox", "staging", "prod"]


class _AllowFlagsMixin(BaseModel):
    """8 个 allow_* 标志的共同基础(DataSource + DataSourceCreate 都用)。

    fail-safe 设计:除 allow_select 默认 True(纯 SELECT 是数据源基本用途) 外,
    其余高风险能力默认 False。生产 ds 必须管理员显式翻开对应 flag。

    - allow_select:                普通业务 SELECT(数据对比 / 字段预览 / fetch_rows)
    - allow_explain:               MySQL EXPLAIN(纯只读,不写表)
    - allow_dm_explain:            DM EXPLAIN(纯只读)
    - allow_oracle_plan_table:     Oracle EXPLAIN PLAN FOR + PLAN_TABLE
                                   ⚠ 会写诊断 PLAN_TABLE(非业务表)
    - allow_schema_import:         读 information_schema / all_tab_columns 反向
                                   生成 yml(纯只读)
    - allow_schema_save:           save=True 时落到 config/scenarios/*.yml
                                   (落盘在 DataOps 侧,不写数据库)
    - allow_scenario_write:        materialize / run-all 写表数据(造数据)
    - allow_record_task:           record:把 workload 翻成 CompareTask 落库
                                   (写 DataOps 配置,不写业务表)
    """
    environment_verified: bool = False  # admin 是否已确认过环境标签
    allow_select: bool = True            # 默认允许纯 SELECT(否则 ds 没意义)
    allow_explain: bool = False
    allow_dm_explain: bool = False
    allow_oracle_plan_table: bool = False
    allow_schema_import: bool = False
    allow_schema_save: bool = False
    allow_scenario_write: bool = False
    allow_record_task: bool = False


class DataSource(_AllowFlagsMixin):
    id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    db_type: DatabaseType
    host: str = Field(..., min_length=1)
    port: int = Field(..., gt=0, le=65535)
    database: str = ""
    username: str = ""
    password: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)
    project_id: str = ""   # 关联 Project.id；空 = 全局可见
    environment: DatasourceEnvironment = "unknown"


class DataSourceCreate(_AllowFlagsMixin):
    name: str = Field(..., min_length=1)
    db_type: DatabaseType
    host: str = Field(..., min_length=1)
    port: int = Field(..., gt=0, le=65535)
    database: str = ""
    username: str = ""
    password: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)
    project_id: str = ""
    environment: DatasourceEnvironment = "unknown"


def make_sandbox_datasource_kwargs() -> dict[str, Any]:
    """demo init_db / 测试 fixture 用的 sandbox 全开模板。

    把所有 allow_* 翻 True + environment=sandbox + environment_verified=True。
    避免每个 caller 都手抄 8 个字段(漏一个就掉坑里)。
    """
    return {
        "environment": "sandbox",
        "environment_verified": True,
        "allow_select": True,
        "allow_explain": True,
        "allow_dm_explain": True,
        "allow_oracle_plan_table": True,
        "allow_schema_import": True,
        "allow_schema_save": True,
        "allow_scenario_write": True,
        "allow_record_task": True,
    }
