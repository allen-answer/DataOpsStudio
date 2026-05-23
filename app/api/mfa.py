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
    count_recovery_codes,
    decrypt_mfa_secret,
    encrypt_mfa_secret,
    generate_recovery_codes,
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
        # 剩余 recovery code 数 —— UI 显示「N/10 还能用」,低于阈值提示重新生成
        "recovery_codes_remaining": count_recovery_codes(current.id),
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
    from app.services.audit import record_auth_event
    record_auth_event("mfa_enroll", username=current.username, user_id=current.id)
    logger.info("mfa enroll user_id=%s username=%s", current.id, current.username)
    return {
        "secret": secret,
        "provisioning_uri": provisioning_uri(secret, current.username),
        # 给前端「QR 已渲染了再扫」做个 hint —— 这步只是落 secret,verify 才正式启用
        "verified": False,
    }


@router.post("/api/auth/mfa/verify")
def mfa_verify(
    request: Request,
    payload: dict = Body(...),
    current: User = Depends(get_current_user),
):
    """验 6 位 OTP 并启用 MFA。enroll 后必须调一次本端点才算开启。

    成功时生成 10 个 recovery codes（首次启用 MFA 才生成；重新 verify 已启用
    账号不动 codes）—— 明文一次性返,客户端必须当场保存。

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
    # 首次启用才生成 codes —— 已启用的用户重 verify 不重置 codes
    first_time_enable = not fresh.mfa_enabled
    plain_codes: list[str] = []
    if first_time_enable:
        plain_codes, hashed_codes = generate_recovery_codes()
        update_user_mfa(current.id, enabled=True, recovery_codes_hashed=hashed_codes)
    else:
        update_user_mfa(current.id, enabled=True)
    from app.services.audit import record_auth_event
    record_auth_event(
        "mfa_verify_success" if not first_time_enable else "mfa_enable",
        username=current.username, user_id=current.id,
        extra={"first_time": first_time_enable},
    )
    logger.info(
        "mfa enabled user_id=%s username=%s first_time=%s",
        current.id, current.username, first_time_enable,
    )
    return {"ok": True, "recovery_codes": plain_codes}


@router.post("/api/auth/mfa/recovery-codes/regenerate")
def mfa_regenerate_recovery_codes(
    request: Request,
    payload: dict = Body(...),
    current: User = Depends(get_current_user),
):
    """重新生成 10 个 recovery codes —— 老的全失效。

    要求 step-up（300s）+ 当前 OTP —— 防 token 被盗后偷换 codes 锁死用户。
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
        raise HTTPException(status_code=401, detail="OTP 验证失败")
    plain_codes, hashed_codes = generate_recovery_codes()
    update_user_mfa(current.id, recovery_codes_hashed=hashed_codes)
    from app.services.audit import record_auth_event
    record_auth_event(
        "mfa_regenerate_recovery_codes",
        username=current.username, user_id=current.id,
    )
    logger.info(
        "mfa regenerate recovery codes user_id=%s username=%s",
        current.id, current.username,
    )
    return {"ok": True, "recovery_codes": plain_codes}


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
    # disable 时清掉 recovery codes —— 它们绑死在旧 secret 上,留着没意义
    update_user_mfa(
        current.id, secret_encrypted="", enabled=False, recovery_codes_hashed=[],
    )
    from app.services.audit import record_auth_event
    record_auth_event("mfa_disable", username=current.username, user_id=current.id)
    logger.info("mfa disabled user_id=%s username=%s", current.id, current.username)
    return OkResponse(ok=True)
