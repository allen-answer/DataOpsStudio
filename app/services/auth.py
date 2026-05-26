"""认证 / 授权服务。

- 密码 bcrypt（passlib），永不存明文
- JWT HS256 签发 / 校验，密钥读 env DATAOPS_JWT_SECRET（缺省落 dev key
  + 启动 warning）
- current_user FastAPI 依赖：从 Authorization: Bearer <token> 解出 User
- require_role(role)：依赖工厂，admin>editor>viewer 等级
- 自举：USERS_FILE 为空时启动自动建一个 admin 账号（密码读 env
  DATAOPS_ADMIN_PASSWORD，缺省 'admin'，记 logger.warning）
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt

from app.models import User, UserCreate, UserRole
from app.services.json_store import JsonStore
from app.utils.paths import USERS_FILE

logger = logging.getLogger(__name__)

# ── 密码 hash ──
# 直接用 bcrypt 库（passlib 1.7.4 + bcrypt 5.x 在 secret/salt 校验路径上不兼
# 容，会在任意短密码上 raise "password cannot be longer than 72 bytes"）。
# bcrypt 本身硬性 72-byte 上限，超出截断 —— 用户应该是 ASCII 密码就够。

# ── 生产模式 fail-fast (#8 / #9) ──
# DATAOPS_ENV=prod / production 时:
#   - 缺 DATAOPS_JWT_SECRET → RuntimeError(避免 dev key 上线)
#   - 空 users.json 默认自举 admin/admin 被禁(需 DATAOPS_BOOTSTRAP_ADMIN_ONCE=true
#     + DATAOPS_ADMIN_PASSWORD=<显式> 才放行)
IS_PROD = os.getenv("DATAOPS_ENV", "").strip().lower() in {"prod", "production"}

# ── JWT ──
JWT_SECRET = os.getenv("DATAOPS_JWT_SECRET", "").strip()
JWT_ALG = "HS256"
JWT_TTL_SECONDS = int(os.getenv("DATAOPS_JWT_TTL_SECONDS", str(8 * 3600)))

if IS_PROD and not JWT_SECRET:
    raise RuntimeError(
        "DATAOPS_JWT_SECRET is required in production (DATAOPS_ENV=prod). "
        "Set a strong random secret via env, e.g.\n"
        "  export DATAOPS_JWT_SECRET=$(python -c 'import secrets;print(secrets.token_urlsafe(64))')"
    )

if not JWT_SECRET:
    JWT_SECRET = "dev-only-jwt-secret-change-me-in-prod"
    logger.warning(
        "DATAOPS_JWT_SECRET 未配置，使用默认 dev key —— 部署生产前请通过 env 设置",
    )

ROLE_ORDER = {"viewer": 0, "editor": 1, "admin": 2}

# OAuth2 scheme：FastAPI 的 dependency injection 用，不真的去 OAuth2 server
_oauth2 = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


# ─── User store ───────────────────────────────────────────────────────────────

user_store: JsonStore[User, UserCreate] = JsonStore(USERS_FILE, User)


def _to_bytes(plain: str) -> bytes:
    """bcrypt 上限 72 bytes —— 超出截断，不抛错。"""
    return plain.encode("utf-8")[:72]


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(_to_bytes(plain), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(_to_bytes(plain), hashed.encode("utf-8"))
    except Exception:
        return False


def bootstrap_default_admin() -> None:
    """USERS_FILE 空时建一个 admin 账号。开发友好；生产建议改密码。

    用 user_store.path 而不是 module-level USERS_FILE —— 后者顶层 import 时锁定，
    测试 monkeypatch 改不了。

    生产模式硬规则(#9):
    - DATAOPS_ENV=prod 且空 users.json + 无 DATAOPS_BOOTSTRAP_ADMIN_ONCE=true → RuntimeError
    - DATAOPS_BOOTSTRAP_ADMIN_ONCE=true 但缺 DATAOPS_ADMIN_PASSWORD → RuntimeError
    - 两个 env 都给 → 用显式密码创建一次,后续重启不会重复触发(users.json 已非空)
    """
    if user_store.list():
        return
    if IS_PROD:
        bootstrap_once = os.getenv("DATAOPS_BOOTSTRAP_ADMIN_ONCE", "").strip().lower() in {"1", "true", "yes"}
        if not bootstrap_once:
            raise RuntimeError(
                "Refusing to auto-bootstrap admin in production (DATAOPS_ENV=prod). "
                "To create the initial admin once, set:\n"
                "  DATAOPS_BOOTSTRAP_ADMIN_ONCE=true\n"
                "  DATAOPS_ADMIN_PASSWORD=<strong-explicit-password>"
            )
        if not os.getenv("DATAOPS_ADMIN_PASSWORD"):
            raise RuntimeError(
                "DATAOPS_ADMIN_PASSWORD is required for production bootstrap "
                "(set an explicit strong password, do not rely on default 'admin')"
            )
    default_password = os.getenv("DATAOPS_ADMIN_PASSWORD", "admin")
    user = User(
        id=uuid.uuid4().hex,
        username="admin",
        password_hash=hash_password(default_password),
        role="admin",
        display_name="系统管理员",
        created_at=datetime.now().isoformat(timespec="seconds"),
    )
    import json
    target = user_store.path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps([user.model_dump(mode="json")], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    user_store.invalidate_cache()
    logger.warning(
        "已自举默认管理员账号 admin/%s —— 首次登录后请改密码",
        "***" if os.getenv("DATAOPS_ADMIN_PASSWORD") else "admin",
    )


def find_user_by_username(username: str) -> User | None:
    for user in user_store.list():
        if user.username == username:
            return user
    return None


# ─── JWT ──────────────────────────────────────────────────────────────────────


def create_access_token(user: User) -> tuple[str, int]:
    """返 (token, expires_in_seconds)。

    带 `jti`（唯一 token id）—— logout / 吊销靠它定位单个 token。
    """
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user.id,
        "username": user.username,
        "role": user.role,
        "jti": uuid.uuid4().hex,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=JWT_TTL_SECONDS)).timestamp()),
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)
    return token, JWT_TTL_SECONDS


def decode_access_token(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except JWTError:
        return None


# ─── token 吊销（真正的 logout）────────────────────────────────────────────────
# JWT 是无状态的：签发后到 exp 前一直有效，logout 只丢客户端 token 挡不住已
# 泄露的副本。这里维护一张服务端吊销表 —— 命中即视为无效。表落 SQLite，重启
# 后仍生效。


def revoke_token(jti: str, exp: int, user_id: str = "") -> None:
    """把一个 jti 写入吊销表。重复吊销同一 jti 幂等（INSERT OR IGNORE）。"""
    if not jti:
        return
    from app.services import sqlite_store

    with sqlite_store.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO revoked_tokens (jti, exp, revoked_at, user_id) "
            "VALUES (?, ?, ?, ?)",
            (
                jti,
                int(exp),
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                user_id,
            ),
        )


def is_token_revoked(jti: str | None) -> bool:
    """该 jti 是否已吊销。jti 为空（老 token 无此 claim）→ False，平滑兼容。"""
    if not jti:
        return False
    from app.services import sqlite_store

    with sqlite_store.connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM revoked_tokens WHERE jti = ?", (jti,)
        ).fetchone()
    return row is not None


def prune_revoked_tokens() -> int:
    """删掉已自然过期的吊销记录 —— 过期 token 本就失效，不必再占表。"""
    now = int(datetime.now(timezone.utc).timestamp())
    from app.services import sqlite_store

    with sqlite_store.connect() as conn:
        cur = conn.execute("DELETE FROM revoked_tokens WHERE exp < ?", (now,))
        return cur.rowcount


def ensure_recent_auth(request: Request, *, max_age: int = 300) -> None:
    """Step-up：当前 token 的 `iat` 必须在 max_age 秒内，否则 403。

    `iat` 是登录时签发 / verify-password 成功重发的「最近认证时间戳」——
    敏感操作（含密码导出 / 用户删除 / 等）调本 helper 强制用户在窗口内
    重新输入过密码。stateless，不依赖额外表。

    403 detail 以 `step_up_required` 起头 —— 前端据此触发密码 prompt + 重试。
    """
    token = _extract_token(request, None)
    payload = decode_access_token(token) if token else None
    if not payload:
        # 通常上游 get_current_user 已挡 401；这里再保一次防漏
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录或 token 无效",
        )
    iat = int(payload.get("iat") or 0)
    now = int(datetime.now(timezone.utc).timestamp())
    if now - iat > max_age:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"step_up_required: 此操作需要重新输入密码确认"
                f"（最近一次认证已超过 {max_age} 秒）"
            ),
        )


def revoke_active_token(request: Request) -> bool:
    """吊销当前请求携带的 token。返回是否真的吊销了。

    老 token 无 `jti` claim 时无法定位 → 返回 False（logout 仍算成功，客户端
    丢弃 token 即可；这类 token 会在 8h TTL 内自然失效）。
    """
    token = _extract_token(request, None)
    if not token:
        return False
    payload = decode_access_token(token)
    if not payload:
        return False
    jti = payload.get("jti")
    if not jti:
        return False
    revoke_token(str(jti), int(payload.get("exp") or 0), str(payload.get("sub") or ""))
    prune_revoked_tokens()
    return True


# ─── FastAPI 依赖 ─────────────────────────────────────────────────────────────


def _redact(user: User) -> User:
    """API 出去脱敏：password_hash + mfa_secret_encrypted + recovery code 哈希都清空。"""
    return user.model_copy(update={
        "password_hash": "",
        "mfa_secret_encrypted": "",
        "mfa_recovery_codes_hashed": [],
    })


def _extract_token(request: Request, oauth_token: str | None) -> str | None:
    """OAuth2PasswordBearer 优先；fallback：Authorization 头 / cookie。"""
    if oauth_token:
        return oauth_token
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def get_current_user_optional(
    request: Request,
    oauth_token: str | None = Depends(_oauth2),
) -> User | None:
    """没 token 也不抛错，给"可匿名"endpoint 用（如 /api/auth/me 校验前置）。"""
    token = _extract_token(request, oauth_token)
    if not token:
        return None
    payload = decode_access_token(token)
    if not payload:
        return None
    if is_token_revoked(payload.get("jti")):
        return None
    user = user_store.get(payload.get("sub", ""))
    if user is None:
        return None
    return user


def get_current_user(
    user: User | None = Depends(get_current_user_optional),
) -> User:
    """要求登录，未登录 → 401。"""
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录或 token 无效",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_role(min_role: UserRole):
    """依赖工厂：要求至少 min_role。admin > editor > viewer。"""
    min_level = ROLE_ORDER[min_role]

    def dep(current: User = Depends(get_current_user)) -> User:
        if ROLE_ORDER.get(current.role, -1) < min_level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"需要 {min_role} 及以上权限",
            )
        return current

    return dep


# 启动时自举 default admin
bootstrap_default_admin()
