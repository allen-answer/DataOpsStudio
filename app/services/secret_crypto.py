from __future__ import annotations

import base64
import hashlib
import os
import stat

from cryptography.fernet import Fernet, InvalidToken

from app.utils.paths import LOCAL_SECRET_KEY_FILE


PREFIX = "fernet:"


def encrypt_secret(value: str) -> str:
    if not value:
        return ""
    token = _fernet().encrypt(value.encode("utf-8")).decode("ascii")
    return f"{PREFIX}{token}"


def decrypt_secret(value: str) -> str:
    if not value:
        return ""
    if not value.startswith(PREFIX):
        # Backward compatibility for older local config; callers may re-save to
        # migrate it to encrypted storage.
        return value
    token = value[len(PREFIX):].encode("ascii")
    try:
        return _fernet().decrypt(token).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("secret cannot be decrypted with the current DATAOPS_CONFIG_SECRET/key file") from exc


def is_encrypted(value: str) -> bool:
    return bool(value and value.startswith(PREFIX))


def _fernet() -> Fernet:
    secret = os.getenv("DATAOPS_CONFIG_SECRET") or os.getenv("DATAOPS_SECRET_KEY") or ""
    if secret:
        key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
        return Fernet(key)
    return Fernet(_local_key())


def _local_key() -> bytes:
    LOCAL_SECRET_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    if LOCAL_SECRET_KEY_FILE.exists():
        return LOCAL_SECRET_KEY_FILE.read_bytes().strip()
    key = Fernet.generate_key()
    LOCAL_SECRET_KEY_FILE.write_bytes(key)
    try:
        os.chmod(LOCAL_SECRET_KEY_FILE, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return key
