"""数据源 schema：DataSource（含 id）+ DataSourceCreate（无 id 创建用）。"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.common import DatabaseType


# 数据源环境标签。沙盒写入端点(scenarios materialize / run-all / record)只允许
# sandbox;prod / staging 拒绝(合规防御:防 admin 误连生产库灌假数据)。
# 旧 datasource 没此字段时默认 sandbox(兼容现有 datasources.json,保持已有
# 流程不变;用户自己升级到 prod 再开 prod 保护)。
DatasourceEnvironment = Literal["sandbox", "staging", "prod"]


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
    project_id: str = ""   # 关联 Project.id；空 = 全局可见
    environment: DatasourceEnvironment = "sandbox"


class DataSourceCreate(BaseModel):
    name: str = Field(..., min_length=1)
    db_type: DatabaseType
    host: str = Field(..., min_length=1)
    port: int = Field(..., gt=0, le=65535)
    database: str = ""
    username: str = ""
    password: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)
    project_id: str = ""
    environment: DatasourceEnvironment = "sandbox"
