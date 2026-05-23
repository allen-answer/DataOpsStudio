"""MFA (TOTP) 端点 —— enroll / verify / disable / status。

enroll/verify/disable 都挂 step-up：MFA 状态变更跟「含密码导出」同级敏感,
要求近 300s 内重新输过密码（登录后立刻设 MFA 一般在窗口内,无感）。

challenge（登录两步流的第二步）在 app/api/auth.py 里 —— 它不能挂
get_current_user,因为用户还没拿到正式 access token。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status

from app.models import OkResponse, User
from app.services.auth import ensure_recent_auth, get_current_user, user_store
from app.services.mfa import (
    decrypt_mfa_secret,
    encrypt_mfa_secret,
    generate_secret,
    provisioning_uri,
    update_user_mfa,
    verify_totp,
)


logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get("/api/auth/mfa/status")
def mfa_status(current: User = Depends(get_current_user)):
    """返回当前用户的 MFA 状态。给前端「已开启 / 已 enroll 但未验证 / 未配」三态。"""
    return {
        "enabled": bool(current.mfa_enabled),
        # enrolled: secret 已生成（但用户可能还没 verify）；用来让 UI 区分
        # 「全新」vs「半路放弃了 enroll，回头再来」两种状态
        "enrolled": bool(current.mfa_secret_encrypted),
    }


@router.post("/api/auth/mfa/enroll")
def mfa_enroll(
    request: Request,
    current: User = Depends(get_current_user),
):
    """生成 TOTP secret 并加密落盘 —— 但 mfa_enabled 仍为 False。

    返回明文 secret + provisioning_uri 给前端渲染 QR。用户用 Google
    Authenticator / Authy 等扫码绑定后,调 /mfa/verify 提交 6 位 OTP 才正式开启。

    step-up：MFA 状态变更跟「含密码导出」同级敏感。
    """
    ensure_recent_auth(request, max_age=300)
    if current.mfa_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="MFA 已开启;先 disable 再重新 enroll",
        )
    secret = generate_secret()
    update_user_mfa(current.id, secret_encrypted=encrypt_mfa_secret(secret), enabled=False)
    logger.info("mfa enroll user_id=%s username=%s", current.id, current.username)
    return {
        "secret": secret,
        "provisioning_uri": provisioning_uri(secret, current.username),
        # 给前端「QR 已渲染了再扫」做个 hint —— 这步只是落 secret,verify 才正式启用
        "verified": False,
    }


@router.post("/api/auth/mfa/verify", response_model=OkResponse)
def mfa_verify(
    request: Request,
    payload: dict = Body(...),
    current: User = Depends(get_current_user),
):
    """验 6 位 OTP 并启用 MFA。enroll 后必须调一次本端点才算开启。

    step-up：跟 enroll 同。
    """
    ensure_recent_auth(request, max_age=300)
    code = str(payload.get("code") or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="code 不能为空")
    # 用户对象走 user_store.get 拿最新（current 可能是过期快照）
    fresh = user_store.get(current.id)
    if fresh is None or not fresh.mfa_secret_encrypted:
        raise HTTPException(status_code=400, detail="尚未 enroll MFA,先调 /mfa/enroll")
    secret = decrypt_mfa_secret(fresh.mfa_secret_encrypted)
    if not verify_totp(secret, code):
        raise HTTPException(status_code=401, detail="OTP 验证失败 —— 检查时间同步 / 输入位数")
    update_user_mfa(current.id, enabled=True)
    logger.info("mfa enabled user_id=%s username=%s", current.id, current.username)
    return OkResponse(ok=True)


@router.post("/api/auth/mfa/disable", response_model=OkResponse)
def mfa_disable(
    request: Request,
    payload: dict = Body(...),
    current: User = Depends(get_current_user),
):
    """关 MFA —— 必须提供当前 OTP（防止 token 被盗后悄悄关 MFA 锁死用户）。

    step-up + OTP 双重确认。
    """
    ensure_recent_auth(request, max_age=300)
    code = str(payload.get("code") or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="code 不能为空")
    fresh = user_store.get(current.id)
    if fresh is None or not fresh.mfa_enabled:
        raise HTTPException(status_code=400, detail="MFA 未开启")
    secret = decrypt_mfa_secret(fresh.mfa_secret_encrypted)
    if not verify_totp(secret, code):
        raise HTTPException(status_code=401, detail="OTP 验证失败,disable 已取消")
    update_user_mfa(current.id, secret_encrypted="", enabled=False)
    logger.info("mfa disabled user_id=%s username=%s", current.id, current.username)
    return OkResponse(ok=True)
