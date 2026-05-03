from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from app.services.secret_crypto import decrypt_secret, encrypt_secret, is_encrypted
from app.utils.paths import LINEAGE_AI_CONFIG_FILE


OPENAI_COMPATIBLE_PROVIDERS = {"openai", "azure", "http", "openai-compatible"}
VALID_PROVIDERS = {"off", "mock", "ollama", *OPENAI_COMPATIBLE_PROVIDERS}


def get_effective_lineage_ai_config() -> dict[str, Any]:
    stored = _read_stored()
    provider = str(stored.get("provider") or os.getenv("DATAOPS_LINEAGE_AI_PROVIDER", "off")).strip().lower() or "off"
    encrypted_key = str(stored.get("api_key_encrypted") or "")
    env_key = os.getenv("DATAOPS_LINEAGE_AI_API_KEY", "").strip()
    return {
        "provider": provider,
        "model": str(stored.get("model") or os.getenv("DATAOPS_LINEAGE_AI_MODEL", "")).strip(),
        "base_url": str(stored.get("base_url") or os.getenv("DATAOPS_LINEAGE_AI_BASE_URL", "")).strip(),
        "api_key": decrypt_secret(encrypted_key) if encrypted_key else env_key,
        "timeout_seconds": _float(stored.get("timeout_seconds"), os.getenv("DATAOPS_LINEAGE_AI_TIMEOUT_SECONDS", "20")),
        "include_raw": _bool(stored.get("include_raw"), os.getenv("DATAOPS_LINEAGE_AI_INCLUDE_RAW", "false")),
        "source": "stored" if stored else "env",
        "api_key_source": "stored" if encrypted_key else ("env" if env_key else ""),
        "api_key_encrypted": is_encrypted(encrypted_key),
        "updated_at": str(stored.get("updated_at") or ""),
    }


def get_public_lineage_ai_config() -> dict[str, Any]:
    effective = get_effective_lineage_ai_config()
    return _public_from_effective(effective)


def save_lineage_ai_config(payload: dict[str, Any]) -> dict[str, Any]:
    current = _read_stored()
    provider = str(payload.get("provider", current.get("provider", "off")) or "off").strip().lower()
    if provider not in VALID_PROVIDERS:
        raise ValueError(f"unsupported AI provider: {provider}")

    next_config = {
        "provider": provider,
        "model": str(payload.get("model", current.get("model", "")) or "").strip(),
        "base_url": str(payload.get("base_url", current.get("base_url", "")) or "").strip(),
        "timeout_seconds": _float(payload.get("timeout_seconds"), current.get("timeout_seconds", 20)),
        "include_raw": _bool(payload.get("include_raw"), current.get("include_raw", False)),
        "api_key_encrypted": str(current.get("api_key_encrypted") or ""),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }

    if payload.get("clear_api_key") is True:
        next_config["api_key_encrypted"] = ""
    elif "api_key" in payload and str(payload.get("api_key") or ""):
        next_config["api_key_encrypted"] = encrypt_secret(str(payload["api_key"]))
    elif next_config["api_key_encrypted"] and not is_encrypted(next_config["api_key_encrypted"]):
        next_config["api_key_encrypted"] = encrypt_secret(decrypt_secret(next_config["api_key_encrypted"]))

    _write_stored(next_config)
    return get_public_lineage_ai_config()


def lineage_ai_configured(config: dict[str, Any]) -> bool:
    provider = str(config.get("provider") or "off").lower()
    if provider == "mock":
        return True
    if provider in OPENAI_COMPATIBLE_PROVIDERS:
        return bool(config.get("api_key") and config.get("model"))
    if provider == "ollama":
        return bool(config.get("model"))
    return False


def _public_from_effective(config: dict[str, Any]) -> dict[str, Any]:
    provider = str(config.get("provider") or "off").lower()
    enabled = provider not in {"off", "disabled", "none", ""}
    return {
        "enabled": enabled,
        "provider": provider,
        "model": config.get("model") or "",
        "base_url": config.get("base_url") or "",
        "configured": lineage_ai_configured(config),
        "api_key_set": bool(config.get("api_key")),
        "api_key_source": config.get("api_key_source") or "",
        "api_key_encrypted": bool(config.get("api_key_encrypted")),
        "source": config.get("source") or "env",
        "timeout_seconds": config.get("timeout_seconds") or 20,
        "include_raw": bool(config.get("include_raw")),
        "updated_at": config.get("updated_at") or "",
    }


def _read_stored() -> dict[str, Any]:
    path = Path(LINEAGE_AI_CONFIG_FILE)
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8").strip() or "{}")
    if not isinstance(data, dict):
        raise ValueError("lineage_ai.json must contain a JSON object")
    return data


def _write_stored(data: dict[str, Any]) -> None:
    LINEAGE_AI_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    LINEAGE_AI_CONFIG_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _float(value: Any, fallback: Any) -> float:
    try:
        return float(value if value not in (None, "") else fallback)
    except (TypeError, ValueError):
        return float(fallback if fallback not in (None, "") else 20)


def _bool(value: Any, fallback: Any = False) -> bool:
    raw = value if value not in (None, "") else fallback
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}
