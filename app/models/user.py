"""User / Project / AuditLog 模型。

权限模型 MVP：
- role 三档：admin（全权 + 管理用户/项目）/ editor（在自己项目里 CRUD）/ viewer（只读）
- 跨项目隔离：每个 datasource/task/workflow 关联一个 project_id；admin 可看全部
- 没有"组织"层（早期不需要），用户直接挂项目的 members 列表

口令存 bcrypt hash（passlib），永不返明文。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


UserRole = Literal["admin", "editor", "viewer"]


class User(BaseModel):
    id: str = Field(..., min_length=1)
    username: str = Field(..., min_length=1, max_length=64)
    password_hash: str = Field(default="", repr=False)  # bcrypt hash；API 出去脱敏
    role: UserRole = "viewer"
    display_name: str = ""
    created_at: str = ""
    # MFA (TOTP)：mfa_secret_encrypted 由 secret_crypto 加密落盘；空 = 未 enroll。
    # enroll 后落 secret 但 mfa_enabled=False；verify 成功才置 True。
    # API 出去强制脱敏（_redact），别让 secret 走出去。
    mfa_secret_encrypted: str = Field(default="", repr=False)
    mfa_enabled: bool = False

    model_config = ConfigDict(extra="ignore")


class UserCreate(BaseModel):
    """POST /api/users 入参 —— password 明文，进 store 前 hash。"""
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=4)
    role: UserRole = "viewer"
    display_name: str = ""


class UserUpdate(BaseModel):
    """PUT /api/users/{id} 入参。password / role / display_name 都可选；
    password 留空 = 不改。"""
    password: str = ""
    role: UserRole | None = None
    display_name: str | None = None


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    """登录响应 —— 兼容 MFA 两步流。

    无 MFA / MFA 通过：`access_token`、`user` 都有。
    需 MFA：`mfa_required=True` + `mfa_token`（5 分钟 challenge token）
    取代正式 token；客户端提交 OTP 到 `/api/auth/mfa/challenge` 换正式 token。
    """
    access_token: str = ""
    token_type: str = "bearer"
    expires_in: int = 0  # seconds
    user: User | None = None
    mfa_required: bool = False
    mfa_token: str = ""  # 仅 mfa_required=True 时填充


class Project(BaseModel):
    id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1, max_length=64)
    description: str = ""
    owner_id: str = ""           # User.id；删用户时如果还是 owner 转 admin
    members: list[str] = Field(default_factory=list)  # User.id list（含 owner）
    created_at: str = ""

    model_config = ConfigDict(extra="ignore")


class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    description: str = ""
    members: list[str] = Field(default_factory=list)


class AuditLogEntry(BaseModel):
    """一条 mutating 操作日志。仓库不入库，落 logs/audit.jsonl 一行一条。"""
    ts: str
    user_id: str = ""
    username: str = ""
    method: str
    path: str
    resource_type: str = ""    # datasources / tasks / workflows / projects / users / ...
    resource_id: str = ""
    status_code: int = 0
    project_id: str = ""
