"""Refresh token rotation —— OAuth2 风格的会话延展机制。

设计：
- login 签短 access (默认 8h) + 长 refresh (默认 7 天)
- access 临过期前 / 已过期 401 时,前端调 POST /api/auth/refresh 拿新对
- 每次 rotation：旧 refresh 标 replaced_by=新jti,新 refresh 写入 DB
- **重放检测**：若 replaced_by 非空的 refresh 又被使用 → 视为盗用,整条
  用户 refresh 链 revoke,强制重新登录。这是 OAuth2 refresh rotation 的
  标志性安全性质（RFC 6749 Sec 10.4 + RFC 8252 OAuth2 BCP）。
- logout 一并 revoke 用户全部 active refresh。

refresh token 是 JWT,密钥从 JWT_SECRET sha256 派生（跟登录 token / mfa
challenge / download token 各自独立的派生路径,purpose claim 区分用途）。
"""
from __future__ import annotations

import hashlib
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt

logger = logging.getLogger(__name__)

_PURPOSE = "refresh"
_ALG = "HS256"


def _refresh_secret() -> str:
    """从 JWT_SECRET 派生 refresh 专用密钥 —— 跟登录 token 签名隔离。"""
    from app.services import auth as auth_mod

    base = getattr(auth_mod, "JWT_SECRET", "") or ""
    return hashlib.sha256((base + "::refresh-token::v1").encode("utf-8")).hexdigest()


def _refresh_ttl_seconds() -> int:
    """env `DATAOPS_REFRESH_TTL_SECONDS`,默认 7 天。`<= 0` 关闭 refresh 机制。"""
    try:
        value = int(os.getenv("DATAOPS_REFRESH_TTL_SECONDS", str(7 * 24 * 3600)))
    except (TypeError, ValueError):
        return 7 * 24 * 3600
    return value if value > 0 else 0


def issue_refresh_token(user_id: str) -> tuple[str, str, int]:
    """签发新 refresh token + 持久化。返回 (token, jti, ttl_seconds)。

    `ttl_seconds=0` 表示 env 关了 refresh,调用方应跳过 refresh 流程。
    """
    ttl = _refresh_ttl_seconds()
    if ttl <= 0:
        return ("", "", 0)
    jti = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    exp_ts = int((now + timedelta(seconds=ttl)).timestamp())
    payload: dict[str, Any] = {
        "purpose": _PURPOSE,
        "sub": user_id,
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": exp_ts,
    }
    token = jwt.encode(payload, _refresh_secret(), algorithm=_ALG)

    from app.services import sqlite_store

    with sqlite_store.connect() as conn:
        conn.execute(
            "INSERT INTO refresh_tokens (jti, user_id, exp, issued_at) VALUES (?, ?, ?, ?)",
            (jti, user_id, exp_ts, now.isoformat(timespec="seconds")),
        )
    return (token, jti, ttl)


def verify_refresh_token(token: str) -> tuple[str, str] | None:
    """验 refresh token 签名 + purpose + DB 状态。

    返回 `(user_id, jti)` 或 `None`（无效 / 过期 / 已被替换 / 已 revoke / 重放）。

    **重放检测**：若 jti 已有 replaced_by → 视为盗用,**revoke 整条用户 refresh
    链** + 返 None。这是 OAuth2 refresh rotation 的核心安全语义。
    """
    if not token:
        return None
    try:
        payload = jwt.decode(token, _refresh_secret(), algorithms=[_ALG])
    except JWTError:
        return None
    if payload.get("purpose") != _PURPOSE:
        return None
    user_id = str(payload.get("sub") or "")
    jti = str(payload.get("jti") or "")
    if not user_id or not jti:
        return None

    from app.services import sqlite_store

    with sqlite_store.connect() as conn:
        row = conn.execute(
            "SELECT user_id, exp, replaced_by, revoked_at FROM refresh_tokens WHERE jti = ?",
            (jti,),
        ).fetchone()
    if row is None:
        return None
    db_user_id, exp, replaced_by, revoked_at = row[0], row[1], row[2], row[3]
    if db_user_id != user_id:
        return None
    now_ts = int(datetime.now(timezone.utc).timestamp())
    if int(exp) < now_ts:
        return None
    if revoked_at:
        return None
    if replaced_by:
        # 重放！已被 rotation 替换过的 refresh 又被用 —— 视为盗用,整条 revoke
        logger.warning(
            "refresh reuse detected user_id=%s jti=%s replaced_by=%s —— revoking chain",
            user_id, jti, replaced_by,
        )
        revoke_refresh_chain(user_id)
        return None
    return (user_id, jti)


def rotate_refresh_token(old_token: str) -> tuple[str, str, str, int] | None:
    """rotation：验老 → 签新对 → 标老 replaced_by。

    返回 `(user_id, new_refresh_token, new_jti, ttl)` 或 None（老 token 无效 /
    重放检出）。调用方拿 user_id 顺便签新 access。
    """
    verified = verify_refresh_token(old_token)
    if verified is None:
        return None
    user_id, old_jti = verified
    new_token, new_jti, ttl = issue_refresh_token(user_id)
    if not new_token:
        return None

    from app.services import sqlite_store

    with sqlite_store.connect() as conn:
        conn.execute(
            "UPDATE refresh_tokens SET replaced_by = ? WHERE jti = ?",
            (new_jti, old_jti),
        )
    return (user_id, new_token, new_jti, ttl)


def revoke_refresh_chain(user_id: str) -> int:
    """把 user 名下所有 active(未 revoke 未 replaced) refresh 全 revoke。

    logout 或重放检出时调。返回 revoke 数量。
    """
    if not user_id:
        return 0
    from app.services import sqlite_store

    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with sqlite_store.connect() as conn:
        cur = conn.execute(
            "UPDATE refresh_tokens SET revoked_at = ? "
            "WHERE user_id = ? AND revoked_at IS NULL",
            (now_iso, user_id),
        )
        return cur.rowcount


def prune_refresh_tokens() -> int:
    """删已自然过期的记录,表不膨胀。"""
    now_ts = int(datetime.now(timezone.utc).timestamp())
    from app.services import sqlite_store

    with sqlite_store.connect() as conn:
        cur = conn.execute("DELETE FROM refresh_tokens WHERE exp < ?", (now_ts,))
        return cur.rowcount
