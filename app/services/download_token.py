"""结果文件下载的短时签名 token（安全加固方案 P1）。

`/results/*` 是路径式访问 —— URL 可猜（`results/<run_id>.xlsx`），知道路径
且对项目有权就能一直下。本模块签发 JWT 风格的短时 token：携带 run_id /
relative_path / project_id / sub / purpose / exp / jti，HMAC 签名防篡改,TTL
默认 300 秒。`GET /api/downloads/{token}` 只认 token + 当前用户的实时项目权限,
**Phase 14 起 jti 一次性消费**(consume_download_nonce)。

密钥从 auth 的 `JWT_SECRET` 用 sha256 派生 —— 跟着 JWT_SECRET 一起轮换，不必
单独配 env；`purpose=download` claim 防止下载 token 跟登录 token 混用。
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

_ALG = "HS256"
_PURPOSE = "download"


def _download_secret() -> str:
    """从 JWT_SECRET 派生下载专用密钥 —— 与登录 token 签名隔离。

    每次取 `auth` 模块的当前 JWT_SECRET（而非 import 时快照），让测试 / 轮换
    场景下也跟得上。
    """
    from app.services import auth as auth_mod

    base = getattr(auth_mod, "JWT_SECRET", "") or ""
    return hashlib.sha256((base + "::download-token::v1").encode("utf-8")).hexdigest()


def _ttl_seconds() -> int:
    try:
        value = int(os.getenv("DATAOPS_DOWNLOAD_URL_TTL_SECONDS", "300"))
    except (TypeError, ValueError):
        return 300
    return value if value > 0 else 300


def issue_download_token(
    *,
    run_id: str,
    relative_path: str,
    project_id: str,
    user_id: str,
) -> tuple[str, int]:
    """签发一个下载 token。返回 (token, expires_in_seconds)。

    Phase 14:多 jti claim,GET /api/downloads/{token} 端点会 consume_download_nonce(jti)
    第一次成功即标已消费,二次提交返 410。"""
    ttl = _ttl_seconds()
    now = datetime.now(timezone.utc)
    payload = {
        "purpose": _PURPOSE,
        "run_id": run_id,
        "rel": relative_path,
        "project_id": project_id or "",
        "sub": user_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(payload, _download_secret(), algorithm=_ALG)
    return token, ttl


def consume_download_nonce(jti: str, *, exp: int, user_id: str = "") -> bool:
    """一次性消费 jti —— 返 True 即第一次消费(允许下载),False 即已消费(410)。

    实现走 SQLite `download_nonces` 表 PRIMARY KEY 唯一约束:INSERT 成功 = 第一
    次,IntegrityError = 已消费。SQLite ACID 保证并发请求只有一个能拿到 True。
    """
    if not jti:
        # 没 jti 的老 token(Phase 14 前签发的)兼容直接放行,不强制一次性
        return True
    from app.services import sqlite_store
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        with sqlite_store.connect() as conn:
            conn.execute(
                "INSERT INTO download_nonces (jti, consumed_at, exp, user_id) "
                "VALUES (?, ?, ?, ?)",
                (jti, now_iso, exp, user_id),
            )
        return True
    except Exception as exc:
        # IntegrityError(jti 已存在)→ False;其它 SQLite 错也保守返 False 拒下载
        # —— 但记 warning 让运维知道是 storage 问题而非真正重放
        import sqlite3
        if isinstance(exc, sqlite3.IntegrityError):
            return False
        logger.warning("consume_download_nonce sqlite error: %s", exc)
        return False


def verify_download_token(token: str) -> dict[str, Any] | None:
    """验签 + 校验 purpose + exp。通过返 claims dict，否则 None。

    签名错 / 过期 / 篡改 / 非 download 用途，一律返 None —— 调用方据此 401。
    """
    if not token:
        return None
    try:
        payload = jwt.decode(token, _download_secret(), algorithms=[_ALG])
    except JWTError:
        return None
    if payload.get("purpose") != _PURPOSE:
        return None
    if not payload.get("rel"):
        return None
    return payload
