"""SQL 工作台 v0.4 — SQL 模板库 schema。

模板按 ID 唯一,内置示例(builtin=true)id 形如 `builtin:<slug>`,用户创建的
走 UUID hex。Store 层(`app/sqlide/template_store.py`)在 `list()` 时把仓库
入库的 example 文件 + 用户 `config/sql_templates.json` 合并;`update/delete`
对 builtin id 拒绝写,确保内置永远是仓库最新版,不被用户误改。

risk_level 影响 UI chip 颜色,**不**自动拦截执行 — 真正"危险 SQL"还是
要靠 `utils/sql_guard` 在执行前 validate。模板里也只允许 SELECT/WITH(同 sql_guard),
DDL/DML 不允许进模板库(避免误点直接跑 DROP TABLE)。
"""
from __future__ import annotations

from pydantic import BaseModel, Field

# 复用 app.models.lineage 已经导出的 RiskLevel = Literal["high","medium","low"],
# 避免符号冲突 + 风险闭集只在一处定义。
from app.models.lineage import RiskLevel


class _TemplateBase(BaseModel):
    """Create / Update / Stored 共享字段。"""
    name: str = Field(..., min_length=1, max_length=120)
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    # ["mysql", "oracle", "dm", ...] 或 ["all"] 表示通用
    db_types: list[str] = Field(default_factory=lambda: ["all"])
    project_id: str = ""  # "" = 全局可见
    risk_level: RiskLevel = "low"
    sql: str = Field(..., min_length=1)


class SQLTemplateCreate(_TemplateBase):
    """POST /api/sql-templates 接的 payload。"""
    pass


class SQLTemplateUpdate(_TemplateBase):
    """PUT /api/sql-templates/{id};name/sql 仍必填,其它字段全量覆盖。"""
    pass


class SQLTemplate(_TemplateBase):
    """stored shape。"""
    id: str = Field(..., min_length=1)
    created_by: str = ""        # user_id;builtin 模板填 "system"
    created_at: str = ""        # ISO 8601
    updated_at: str = ""        # ISO 8601
    builtin: bool = False       # True = 仓库内置,不可改 / 不可删
