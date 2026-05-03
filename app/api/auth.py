"""认证 API：登录 / 当前用户 / 用户 CRUD（仅 admin 能管）。

- POST /api/auth/login —— username + password → access_token + user
- GET  /api/auth/me —— 当前 token 解出来的用户
- GET  /api/users —— admin only
- POST /api/users —— admin only，建用户
- PUT  /api/users/{id} —— admin only / 本人改自己 display_name+password
- DELETE /api/users/{id} —— admin only，不能删自己
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.models import LoginRequest, LoginResponse, OkResponse, User, UserCreate, UserUpdate
from app.services.auth import (
    create_access_token,
    find_user_by_username,
    get_current_user,
    hash_password,
    require_role,
    user_store,
    verify_password,
    _redact,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/auth/login", response_model=LoginResponse)
def login(payload: LoginRequest):
    user = find_user_by_username(payload.username)
    if user is None or not verify_password(payload.password, user.password_hash):
        # 用户不存在 / 密码不对都返 401，不暴露哪种
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )
    token, ttl = create_access_token(user)
    logger.info("auth login user_id=%s username=%s role=%s", user.id, user.username, user.role)
    return LoginResponse(
        access_token=token,
        token_type="bearer",
        expires_in=ttl,
        user=_redact(user),
    )


@router.get("/api/auth/me", response_model=User)
def me(current: User = Depends(get_current_user)):
    return _redact(current)


# ─── User CRUD ────────────────────────────────────────────────────────────────


@router.get("/api/users", response_model=list[User])
def list_users(_: User = Depends(require_role("admin"))):
    return [_redact(u) for u in user_store.list()]


@router.post("/api/users", response_model=User)
def create_user(payload: UserCreate, _: User = Depends(require_role("admin"))):
    if find_user_by_username(payload.username):
        raise HTTPException(status_code=400, detail=f"用户名 {payload.username} 已存在")
    # JsonStore.create 接受 Pydantic CreateT；要直接拼 User 因为有 hash 字段
    import json
    user = User(
        id=uuid.uuid4().hex,
        username=payload.username,
        password_hash=hash_password(payload.password),
        role=payload.role,
        display_name=payload.display_name,
        created_at=datetime.now().isoformat(timespec="seconds"),
    )
    target = user_store.path
    raw = json.loads(target.read_text(encoding="utf-8") or "[]")
    raw.append(user.model_dump(mode="json"))
    target.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    user_store.invalidate_cache()
    return _redact(user)


@router.put("/api/users/{user_id}", response_model=User)
def update_user(
    user_id: str,
    payload: UserUpdate,
    current: User = Depends(get_current_user),
):
    target = user_store.get(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    # 自己可以改自己的 password / display_name；admin 才能改 role / 改别人
    if current.id != user_id and current.role != "admin":
        raise HTTPException(status_code=403, detail="只能改自己的账号信息")
    if payload.role is not None and current.role != "admin":
        raise HTTPException(status_code=403, detail="只有 admin 能改 role")

    next_data: dict[str, Any] = target.model_dump()
    if payload.password:
        next_data["password_hash"] = hash_password(payload.password)
    if payload.display_name is not None:
        next_data["display_name"] = payload.display_name
    if payload.role is not None:
        next_data["role"] = payload.role

    import json
    target = user_store.path
    raw = json.loads(target.read_text(encoding="utf-8") or "[]")
    for i, item in enumerate(raw):
        if item.get("id") == user_id:
            raw[i] = next_data
            break
    target.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    user_store.invalidate_cache()
    return _redact(User.model_validate(next_data))


@router.delete("/api/users/{user_id}", response_model=OkResponse)
def delete_user(user_id: str, current: User = Depends(require_role("admin"))):
    if current.id == user_id:
        raise HTTPException(status_code=400, detail="不能删除当前登录的账号")
    target = user_store.get(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    user_store.delete(user_id)
    return {"ok": True}


# ─── 审计日志查询（admin only） ───────────────────────────────────────────────


@router.get("/api/audit-logs")
def list_audit_logs(
    limit: int = 200,
    _: User = Depends(require_role("admin")),
):
    from app.services.audit import read_recent_logs
    return {"logs": read_recent_logs(limit=max(1, min(int(limit), 1000)))}
