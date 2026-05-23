"""MFA (TOTP) —— 第二因子认证。

设计：
- enroll：生成 TOTP secret（base32），用 secret_crypto 加密落盘到 user.mfa_secret_encrypted，
  但 mfa_enabled 仍为 False。返回 secret 明文 + provisioning_uri 让前端渲染 QR。
- verify：客户端扫码绑定后输入 6 位 OTP；服务端用 STORED secret 验，成功置
  mfa_enabled=True。
- disable：要 6 位 OTP + step-up 双重确认，避免误关。
- login flow：登录验密码后，若 user.mfa_enabled，签发短时 mfa_challenge token
  让前端去 /api/auth/mfa/challenge 提交 6 位码换正式 access token。

TOTP 用 pyotp 标准实现（RFC 6238），默认 30s 窗口、SHA-1、6 位、容忍 1 个
window drift（前后各 30s 抗时钟漂移）。
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import pyotp
from jose import JWTError, jwt

from app.services.secret_crypto import decrypt_secret, encrypt_secret

logger = logging.getLogger(__name__)

# MFA challenge token：登录密码验过、待提交 OTP 阶段。短时（5min），purpose 区分。
_MFA_CHALLENGE_TTL_SECONDS = 300
_MFA_CHALLENGE_PURPOSE = "mfa_challenge"
_MFA_CHALLENGE_ALG = "HS256"

# 应用名 —— 在 Google Authenticator / Authy 等 app 里显示
ISSUER = "DataOpsStudio"


def generate_secret() -> str:
    """生成 TOTP base32 secret —— 长度 32（160-bit），跟 RFC 6238 兼容。"""
    return pyotp.random_base32()


def provisioning_uri(secret: str, username: str) -> str:
    """`otpauth://totp/DataOpsStudio:user@host?secret=...&issuer=DataOpsStudio`。
    前端给这个 URI 渲染 QR；用户用 Google Authenticator / Authy 等扫码绑定。"""
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=ISSUER)


def verify_totp(secret: str, code: str) -> bool:
    """验 6 位 OTP。valid_window=1 容忍前后各一个 30s 窗口的时钟漂移。"""
    if not secret or not code:
        return False
    try:
        return pyotp.TOTP(secret).verify(str(code).strip(), valid_window=1)
    except Exception:  # noqa: BLE001 —— pyotp 对非数字/长度异常会抛
        return False


# ─── User 字段读写 ──────────────────────────────────────────────────────────


def encrypt_mfa_secret(plain: str) -> str:
    """落盘前加密 —— 用全局 secret_crypto，跟 AI API Key 同一套密钥。"""
    return encrypt_secret(plain)


def decrypt_mfa_secret(encrypted: str) -> str:
    """取明文 secret 给 pyotp.TOTP 用。失败返回空串，调用方按 disabled 处理。"""
    if not encrypted:
        return ""
    try:
        return decrypt_secret(encrypted)
    except Exception:  # noqa: BLE001
        logger.warning("decrypt mfa secret failed —— maybe key rotated or data corrupt")
        return ""


def update_user_mfa(
    user_id: str,
    *,
    secret_encrypted: str | None = None,
    enabled: bool | None = None,
) -> None:
    """直接改 users.json 里这个 user 的 mfa 字段 —— 绕开 JsonStore.update 的
    全替换约束（它要求传完整 UserCreate）。读 / 改 / 写 / 失效缓存。"""
    from app.services.auth import user_store

    path = user_store.path
    if not path.exists():
        raise KeyError(user_id)
    raw = json.loads(path.read_text(encoding="utf-8") or "[]")
    for item in raw:
        if item.get("id") == user_id:
            if secret_encrypted is not None:
                item["mfa_secret_encrypted"] = secret_encrypted
            if enabled is not None:
                item["mfa_enabled"] = enabled
            break
    else:
        raise KeyError(user_id)
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    user_store.invalidate_cache()


# ─── MFA challenge token（登录两步流的中间态）──────────────────────────────


def _challenge_secret() -> str:
    """从 JWT_SECRET 派生 mfa challenge 专用密钥 —— 跟登录 token 签名隔离。"""
    from app.services import auth as auth_mod

    base = getattr(auth_mod, "JWT_SECRET", "") or ""
    return hashlib.sha256((base + "::mfa-challenge::v1").encode("utf-8")).hexdigest()


def issue_mfa_challenge_token(user_id: str) -> tuple[str, int]:
    """登录密码验过、但需 MFA 时签的中间 token。绑 sub=user_id，5 分钟有效。"""
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "purpose": _MFA_CHALLENGE_PURPOSE,
        "sub": user_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=_MFA_CHALLENGE_TTL_SECONDS)).timestamp()),
    }
    token = jwt.encode(payload, _challenge_secret(), algorithm=_MFA_CHALLENGE_ALG)
    return token, _MFA_CHALLENGE_TTL_SECONDS


def verify_mfa_challenge_token(token: str) -> str | None:
    """验 mfa challenge token，返回 user_id 或 None（无效/过期/错 purpose）。"""
    if not token:
        return None
    try:
        payload = jwt.decode(token, _challenge_secret(), algorithms=[_MFA_CHALLENGE_ALG])
    except JWTError:
        return None
    if payload.get("purpose") != _MFA_CHALLENGE_PURPOSE:
        return None
    sub = payload.get("sub")
    return str(sub) if sub else None
