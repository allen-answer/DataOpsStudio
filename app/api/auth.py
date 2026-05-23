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

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status

from app.models import LoginRequest, LoginResponse, OkResponse, User, UserCreate, UserUpdate
from app.services.auth import (
    create_access_token,
    ensure_recent_auth,
    find_user_by_username,
    get_current_user,
    hash_password,
    require_role,
    revoke_active_token,
    user_store,
    verify_password,
    _redact,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _check_rate_limit_or_429(
    request: Request, *, username: str | None = None, endpoint: str = "login",
) -> None:
    """命中 → raise 429 + Retry-After header;过了不动。env 关闭时 no-op。"""
    from app.services.rate_limit import check_auth_rate_limit

    rl = check_auth_rate_limit(request, username=username, endpoint=endpoint)
    if rl is not None and not rl.allowed:
        retry = max(1, int(rl.retry_after) + 1)
        # detail 区分 ip / user 两档 —— 让用户清楚为啥被拦(用户名被全网刷 vs
        # 自己输错太多次)
        if rl.key_type == "user":
            msg = f"该账号尝试过于频繁,请 {retry} 秒后重试"
        else:
            msg = f"登录尝试过于频繁,请 {retry} 秒后重试"
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=msg,
            headers={"Retry-After": str(retry)},
        )


@router.post("/api/auth/login", response_model=LoginResponse)
def login(payload: LoginRequest, request: Request):
    _check_rate_limit_or_429(request, username=payload.username, endpoint="login")
    user = find_user_by_username(payload.username)
    if user is None or not verify_password(payload.password, user.password_hash):
        # 用户不存在 / 密码不对都返 401，不暴露哪种
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )
    # MFA 已启用 → 不直接签 access token，先发 5min mfa_challenge token，
    # 客户端跳 OTP 输入界面提交到 /api/auth/mfa/challenge 换正式 token。
    if user.mfa_enabled:
        from app.services.mfa import issue_mfa_challenge_token

        mfa_token, mfa_ttl = issue_mfa_challenge_token(user.id)
        logger.info("auth login mfa_required user_id=%s username=%s", user.id, user.username)
        return LoginResponse(mfa_required=True, mfa_token=mfa_token, expires_in=mfa_ttl)

    token, ttl = create_access_token(user)
    from app.services.refresh import issue_refresh_token

    refresh_tok, _, refresh_ttl = issue_refresh_token(user.id)
    logger.info("auth login user_id=%s username=%s role=%s", user.id, user.username, user.role)
    return LoginResponse(
        access_token=token,
        token_type="bearer",
        expires_in=ttl,
        user=_redact(user),
        refresh_token=refresh_tok,
        refresh_expires_in=refresh_ttl,
    )


@router.post("/api/auth/refresh", response_model=LoginResponse)
def refresh_token_endpoint(request: Request, payload: dict = Body(...)):
    """OAuth2 风格 rotation：用老 refresh token 换新 access + 新 refresh。

    重放检测：若 refresh token 已被 rotation 替换过又被用 → 视为盗用,整条
    用户 refresh 链 revoke + 返 401,强制重新登录。
    """
    from app.services.refresh import rotate_refresh_token

    _check_rate_limit_or_429(request, endpoint="refresh")
    old = str(payload.get("refresh_token") or "")
    if not old:
        raise HTTPException(status_code=400, detail="refresh_token 不能为空")
    rotated = rotate_refresh_token(old)
    if rotated is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="refresh token 无效或已过期，请重新登录",
        )
    user_id, new_refresh, _new_jti, refresh_ttl = rotated
    user = user_store.get(user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="用户不存在")
    access, access_ttl = create_access_token(user)
    logger.info("auth refresh rotation user_id=%s username=%s", user.id, user.username)
    return LoginResponse(
        access_token=access,
        token_type="bearer",
        expires_in=access_ttl,
        user=_redact(user),
        refresh_token=new_refresh,
        refresh_expires_in=refresh_ttl,
    )


@router.post("/api/auth/mfa/challenge", response_model=LoginResponse)
def mfa_challenge(request: Request, payload: dict = Body(...)):
    """MFA 两步流的第二步：用 login 返的 mfa_token + (6 位 OTP **或** recovery code)
    换正式 access token。

    `code` 字段为 TOTP（6 位数字）;`recovery_code` 字段为一次性后备码（10
    字符 alphanumeric,可含 `-` 分隔符）。两者必有一。Recovery code 验过即从
    用户 codes list 删除（single-use）。

    anon 端点（用户还没拿到 access token,不能挂 get_current_user）。靠 mfa_token
    自身签名 + purpose=mfa_challenge claim + 5 分钟 exp 防滥用。
    """
    from app.services.mfa import (
        decrypt_mfa_secret,
        verify_and_consume_recovery_code,
        verify_mfa_challenge_token,
        verify_totp,
    )

    _check_rate_limit_or_429(request, endpoint="mfa_challenge")
    mfa_token = str(payload.get("mfa_token") or "")
    code = str(payload.get("code") or "").strip()
    recovery_code = str(payload.get("recovery_code") or "").strip()
    if not mfa_token or (not code and not recovery_code):
        raise HTTPException(status_code=400, detail="mfa_token + (code 或 recovery_code) 不能为空")
    user_id = verify_mfa_challenge_token(mfa_token)
    if not user_id:
        raise HTTPException(status_code=401, detail="mfa_token 无效或已过期，请重新登录")
    user = user_store.get(user_id)
    if user is None or not user.mfa_enabled:
        raise HTTPException(status_code=401, detail="用户不存在或 MFA 已关闭")
    if recovery_code:
        if not verify_and_consume_recovery_code(user.id, recovery_code):
            raise HTTPException(status_code=401, detail="恢复码无效或已用过")
        logger.warning(
            "auth mfa challenge via recovery_code user_id=%s username=%s",
            user.id, user.username,
        )
    else:
        secret = decrypt_mfa_secret(user.mfa_secret_encrypted)
        if not verify_totp(secret, code):
            raise HTTPException(status_code=401, detail="OTP 验证失败")
    token, ttl = create_access_token(user)
    from app.services.refresh import issue_refresh_token

    refresh_tok, _, refresh_ttl = issue_refresh_token(user.id)
    logger.info("auth mfa challenge ok user_id=%s username=%s", user.id, user.username)
    return LoginResponse(
        access_token=token,
        token_type="bearer",
        expires_in=ttl,
        user=_redact(user),
        refresh_token=refresh_tok,
        refresh_expires_in=refresh_ttl,
    )


@router.get("/api/auth/me", response_model=User)
def me(current: User = Depends(get_current_user)):
    return _redact(current)


@router.post("/api/auth/verify-password", response_model=LoginResponse)
def verify_password_api(
    request: Request,
    payload: dict = Body(...),
    current: User = Depends(get_current_user),
):
    """Step-up：验当前用户密码 → 签发新 token（iat 刷新）。

    前端在敏感端点 403 step_up_required 时调本端点拿到新 token，写回
    localStorage 后重试原请求。密码错 → 401。

    rate limit:挂登录同一组 IP 限速(已登录用户 step-up 高频是异常行为)。
    """
    _check_rate_limit_or_429(request, username=current.username, endpoint="verify_password")
    password = str(payload.get("password") or "")
    if not password or not verify_password(password, current.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="密码错误")
    token, ttl = create_access_token(current)
    logger.info("auth step-up verify ok user_id=%s username=%s", current.id, current.username)
    return LoginResponse(
        access_token=token,
        token_type="bearer",
        expires_in=ttl,
        user=_redact(current),
    )


@router.post("/api/auth/logout", response_model=OkResponse)
def logout(request: Request, current: User = Depends(get_current_user)):
    """登出：把当前 token 写进服务端吊销表，立即失效（不必等自然 exp）。

    幂等 —— 总返回 ok=True。老 token（无 jti）无法定位吊销，客户端丢弃即可，
    会在 TTL 内自然失效。
    """
    revoke_active_token(request)
    # refresh rotation：同时 revoke 用户所有 active refresh 链 —— 不光 access
    # 失效,refresh 也不能再换新 access
    from app.services.refresh import revoke_refresh_chain

    revoke_refresh_chain(current.id)
    logger.info("auth logout user_id=%s username=%s", current.id, current.username)
    return OkResponse(ok=True)


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
def delete_user(
    user_id: str,
    request: Request,
    current: User = Depends(require_role("admin")),
):
    """删除用户 —— admin only + step-up（300s）。不可撤销动作必须有近期认证。"""
    ensure_recent_auth(request, max_age=300)
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
