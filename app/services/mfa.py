"""MFA (TOTP) —— 第二因子认证 + recovery codes。

设计：
- enroll：生成 TOTP secret（base32），用 secret_crypto 加密落盘到 user.mfa_secret_encrypted，
  但 mfa_enabled 仍为 False。返回 secret 明文 + provisioning_uri 让前端渲染 QR。
- verify：客户端扫码绑定后输入 6 位 OTP；服务端用 STORED secret 验,成功置
  mfa_enabled=True 同时生成 10 个 recovery codes（明文返一次给前端,bcrypt 落盘）。
- disable：要 6 位 OTP + step-up 双重确认，避免误关；disable 同时清空 recovery codes。
- login flow：登录验密码后，若 user.mfa_enabled，签发短时 mfa_challenge token
  让前端去 /api/auth/mfa/challenge 提交 6 位 OTP **或** recovery code 换正式 access token。
- recovery code：丢手机时用 1 个进系统改 / 关 MFA。single-use（用过即从 list 删除）。
  bcrypt hash 落盘（跟密码同一档保护）—— DB 泄露也无法暴力破出明文 code。

TOTP 用 pyotp 标准实现（RFC 6238），默认 30s 窗口、SHA-1、6 位、容忍 1 个
window drift（前后各 30s 抗时钟漂移）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
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
    recovery_codes_hashed: list[str] | None = None,
) -> None:
    """直接改 users.json 里这个 user 的 mfa 字段 —— 绕开 JsonStore.update 的
    全替换约束（它要求传完整 UserCreate）。读 / 改 / 写 / 失效缓存。

    `recovery_codes_hashed=[]` 显式传空列表 = 清空 recovery codes。
    `recovery_codes_hashed=None`（默认）= 不动 recovery codes。
    """
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
            if recovery_codes_hashed is not None:
                item["mfa_recovery_codes_hashed"] = list(recovery_codes_hashed)
            break
    else:
        raise KeyError(user_id)
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    user_store.invalidate_cache()


# ─── Recovery codes（一次性后备码） ────────────────────────────────────────────

# alphanumeric 字符集，去掉容易看错的 0/O/1/I/L —— 用户手抄不出错
_RECOVERY_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_RECOVERY_CODE_LEN = 10            # 不含分隔符,~50 bit 熵足够防猜
_RECOVERY_CODES_PER_USER = 10
_RECOVERY_BCRYPT_ROUNDS = 10       # bcrypt 默认成本 = 12,这里降一档(10) —— 单 verify
# 大概 ~100ms 而非 ~400ms,recovery code 比密码低频很多,可以宽松一点


def _format_recovery_code(plain: str) -> str:
    """`ABCDEFGHJK` → `ABCDE-FGHJK`(5+5 分隔,方便人眼读 / 手抄)。"""
    if len(plain) != _RECOVERY_CODE_LEN:
        return plain
    return f"{plain[:5]}-{plain[5:]}"


def _normalize_recovery_code(raw: str) -> str:
    """用户输入去空白 / 去分隔符 / 大写 —— 让 `abcde-fghjk` `ABCDE FGHJK`
    `ABCDEFGHJK` 三种写法都能验。"""
    if not raw:
        return ""
    return "".join(ch for ch in str(raw).upper() if ch.isalnum())


def generate_recovery_codes(n: int = _RECOVERY_CODES_PER_USER) -> tuple[list[str], list[str]]:
    """生成 `n` 个 recovery codes —— 返回 (明文列表给前端一次性显示, bcrypt 哈希列表落盘)。

    明文格式带分隔符（`ABCDE-FGHJK`）方便用户读;校验时 _normalize_recovery_code
    剥掉分隔符。同一字符不重复字符 alphabet 23 字符 ≈ 4.5 bit/char × 10 = 50 bit
    熵,单码暴力空间 ~10^15,够抗在线 brute-force。
    """
    if n < 1:
        return [], []
    plain_list: list[str] = []
    hashed_list: list[str] = []
    seen: set[str] = set()
    while len(plain_list) < n:
        raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(_RECOVERY_CODE_LEN))
        if raw in seen:
            continue       # secrets 抽到重复极罕见,保险起见显式去重
        seen.add(raw)
        plain_list.append(_format_recovery_code(raw))
        hashed_list.append(
            bcrypt.hashpw(raw.encode("utf-8"), bcrypt.gensalt(rounds=_RECOVERY_BCRYPT_ROUNDS)).decode("utf-8"),
        )
    return plain_list, hashed_list


def verify_and_consume_recovery_code(user_id: str, plain_code: str) -> bool:
    """用户输入的 recovery code 跟 user.mfa_recovery_codes_hashed 对碰。

    匹配到 → 从落盘的 list 里删除该 hash（single-use）→ 返 True。
    未匹配 / 用户无 codes / user 不存在 → 返 False。

    并发安全性：单用户输 recovery code 是低频动作（丢手机才用,1 次/年级别）,
    冲突可能性极低；这里读 + 写之间没锁,极端并发下可能出现「同一 code 被两个
    并发请求都判通过」。生产场景按当前用户量不修。
    """
    from app.services.auth import user_store

    normalized = _normalize_recovery_code(plain_code)
    if not normalized:
        return False
    path = user_store.path
    if not path.exists():
        return False
    raw = json.loads(path.read_text(encoding="utf-8") or "[]")
    matched_idx: int | None = None
    target_codes: list[str] = []
    user_idx: int | None = None
    for i, item in enumerate(raw):
        if item.get("id") == user_id:
            user_idx = i
            target_codes = list(item.get("mfa_recovery_codes_hashed") or [])
            break
    if user_idx is None or not target_codes:
        return False
    for idx, hashed in enumerate(target_codes):
        try:
            if bcrypt.checkpw(normalized.encode("utf-8"), hashed.encode("utf-8")):
                matched_idx = idx
                break
        except Exception:  # noqa: BLE001  —— 损坏的 hash 跳过继续
            continue
    if matched_idx is None:
        return False
    # 消费：从落盘 list 删该 hash,写回
    target_codes.pop(matched_idx)
    raw[user_idx]["mfa_recovery_codes_hashed"] = target_codes
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    user_store.invalidate_cache()
    return True


def count_recovery_codes(user_id: str) -> int:
    """读当前用户剩余的 recovery code 个数 —— 用来给 status endpoint / UI 展示。"""
    from app.services.auth import user_store

    user = user_store.get(user_id)
    if user is None:
        return 0
    return len(user.mfa_recovery_codes_hashed or [])


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
