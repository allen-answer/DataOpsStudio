"""SQL Workbench Pydantic schemas。

- Console:tab 标签,绑一个 datasource_id + 可保存的 sql 草稿。
- HistoryEntry:每次 execute 追加,append-only(后端做 ring buffer 防文件膨胀)。
- ExecuteRequest / Response:跑 SQL 的请求 / 应答 envelope。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ─── Console ───────────────────────────────────────────────────────────────

class Console(BaseModel):
    """前端的一个 SQL Tab。跨刷新由本对象 + sql 字段恢复。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str = Field(..., min_length=1, max_length=120)
    datasource_id: str = ""           # 空表示用户还没选数据源
    sql: str = ""                      # 编辑器内当前 SQL(草稿)
    project_id: str = ""               # 跟 datasource 同 project 时一致;否则归用户当前 project
    owner_user_id: str = ""            # console 所有者(隔离不同用户)
    created_at: str = ""               # ISO 8601 UTC
    updated_at: str = ""


class ConsoleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=120)
    datasource_id: str = ""
    sql: str = ""
    project_id: str = ""


class ConsoleUpdate(BaseModel):
    """PUT 用 partial update,只允许改下面这几个字段。"""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    datasource_id: str | None = None
    sql: str | None = None
    project_id: str | None = None


# ─── Execute ───────────────────────────────────────────────────────────────

class ExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    datasource_id: str = Field(..., min_length=1)
    sql: str = Field(..., min_length=1)
    # 上限保护,服务端再 clamp 一次防绕过
    max_rows: int = Field(default=1000, ge=1, le=10_000)
    # 可选:绑哪个 console(让 history 关联回 console 视角)
    console_id: str = ""


class ExecuteResponse(BaseModel):
    """成功/失败统一返 200,success 字段标记;error 在失败时填。

    rows 用 list[list[Any]](列 array)而非 list[dict] —— 紧凑、保列顺序;
    truncated=True 表示原始结果 > max_rows,只截了前 max_rows 行。
    """

    model_config = ConfigDict(extra="forbid")

    success: bool
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = 0
    elapsed_ms: int = 0
    truncated: bool = False
    error: str | None = None


# ─── History ───────────────────────────────────────────────────────────────

class HistoryEntry(BaseModel):
    """append-only 历史。store 内做 ring buffer cap 防膨胀。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    datasource_id: str
    datasource_name: str = ""
    sql: str
    executed_by: str               # 执行者 user.username
    project_id: str = ""
    executed_at: str               # ISO 8601 UTC
    success: bool
    elapsed_ms: int = 0
    row_count: int = 0
    truncated: bool = False
    error: str | None = None
