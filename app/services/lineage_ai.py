from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LineageAIConfig:
    provider: str = "off"
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    timeout_seconds: float = 20.0


class LineageAIProvider(Protocol):
    name: str

    def enrich(self, payload: dict[str, Any], config: LineageAIConfig) -> dict[str, Any]:
        ...


def lineage_ai_status() -> dict[str, Any]:
    """Return non-sensitive AI provider state for the frontend."""
    from app.services.lineage_ai_config import get_public_lineage_ai_config

    status = get_public_lineage_ai_config()
    return {
        **status,
        "base_url_configured": bool(status.get("base_url")),
        "base_url": "",
    }


def test_lineage_ai_connection(override: dict[str, Any] | None = None) -> dict[str, Any]:
    """Probe the configured provider with a tiny payload.

    The optional override is in-memory only. It lets the admin UI test a new
    key/base URL before saving, while persisted secrets remain encrypted.
    """
    config = _config_with_override(override or {})
    provider = _provider_for(config.provider)
    if provider is None:
        return {
            "ok": False,
            "status": "disabled",
            "provider": config.provider or "off",
            "model": config.model,
            "error": "AI provider is disabled",
        }

    started = time.perf_counter()
    try:
        enrichment = provider.enrich(
            {
                "scope": "connection-test",
                "dialect": "",
                "sql_excerpt": "select 1",
                "scripts": [],
                "summary": {"table_edge_count": 0, "column_edge_count": 0},
                "inputs": [],
                "outputs": [],
                "table_edges": [],
                "column_edges": [],
                "warnings": [],
                "parse_errors": [],
                "risks": [],
            },
            config,
        )
        return {
            "ok": True,
            "status": "success",
            "provider": provider.name,
            "model": config.model,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "summary": str((enrichment or {}).get("summary") or ""),
        }
    except Exception as exc:
        logger.warning("lineage AI connection test failed provider=%s error=%s", config.provider, exc)
        return {
            "ok": False,
            "status": "error",
            "provider": config.provider,
            "model": config.model,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "error": str(exc),
        }


def enrich_lineage_result(
    result: dict[str, Any],
    *,
    sql_text: str = "",
    dialect: str | None = None,
    scope: str = "single",
    scripts: list[dict[str, str]] | None = None,
    enabled: bool = False,
) -> dict[str, Any]:
    """Attach optional AI enrichment without mutating deterministic lineage.

    The AI layer is deliberately additive. It only writes `ai_enrichment` and
    never rewrites graph edges, columns, risks, or report content produced by
    the static analyzer.
    """
    config = _config()
    if not enabled:
        result["ai_enrichment"] = _disabled_enrichment()
        return result

    provider = _provider_for(config.provider)
    if provider is None:
        if config.provider in {"off", "disabled", "none", ""}:
            result["ai_enrichment"] = _disabled_enrichment()
        else:
            result["ai_enrichment"] = _error_enrichment(
                provider=config.provider,
                model=config.model,
                error=f"unsupported provider: {config.provider}",
            )
        return result

    payload = _build_payload(result, sql_text=sql_text, dialect=dialect, scope=scope, scripts=scripts or [])
    started = time.perf_counter()
    try:
        enrichment = provider.enrich(payload, config)
        if not isinstance(enrichment, dict):
            raise ValueError("provider returned non-object enrichment")
        result["ai_enrichment"] = _normalize_enrichment(
            enrichment,
            provider=provider.name,
            model=config.model,
            elapsed_seconds=round(time.perf_counter() - started, 3),
        )
    except Exception as exc:
        logger.exception("lineage AI enrichment failed provider=%s", config.provider)
        result["ai_enrichment"] = _error_enrichment(
            provider=config.provider,
            model=config.model,
            error=str(exc),
            elapsed_seconds=round(time.perf_counter() - started, 3),
        )
    return result


class MockLineageAIProvider:
    name = "mock"

    def enrich(self, payload: dict[str, Any], config: LineageAIConfig) -> dict[str, Any]:
        summary = payload.get("summary", {})
        return {
            "summary": (
                f"Static lineage found {summary.get('table_edge_count', 0)} table edges and "
                f"{summary.get('column_edge_count', 0)} column edges."
            ),
            "suggestions": [
                {
                    "type": "review",
                    "message": "Review low-confidence columns, dynamic SQL, and parse failures first.",
                    "confidence": "medium",
                    "evidence": ["static_summary"],
                }
            ],
            "risks": [],
            "column_hints": [],
        }


class OpenAICompatibleLineageAIProvider:
    name = "openai"

    def enrich(self, payload: dict[str, Any], config: LineageAIConfig) -> dict[str, Any]:
        if not config.api_key:
            raise ValueError("AI API key is required")
        if not config.model:
            raise ValueError("AI model is required")
        base_url = config.base_url.rstrip("/") or "https://api.openai.com/v1"
        body = {
            "model": config.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a SQL lineage reviewer. Return compact JSON with keys "
                        "summary, suggestions, risks, column_hints. Do not invent tables "
                        "or overwrite deterministic lineage; cite evidence ids when possible."
                    ),
                },
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        }
        data = _post_json(
            f"{base_url}/chat/completions",
            body,
            headers={"Authorization": f"Bearer {config.api_key}"},
            timeout=config.timeout_seconds,
        )
        content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "{}"
        return _loads_json_object(content)


class OllamaLineageAIProvider:
    name = "ollama"

    def enrich(self, payload: dict[str, Any], config: LineageAIConfig) -> dict[str, Any]:
        if not config.model:
            raise ValueError("AI model is required")
        base_url = config.base_url.rstrip("/") or "http://localhost:11434"
        body = {
            "model": config.model,
            "stream": False,
            "format": "json",
            "prompt": (
                "Return JSON with keys summary, suggestions, risks, column_hints. "
                "Review this static SQL lineage result without inventing evidence:\n"
                + json.dumps(payload, ensure_ascii=False)
            ),
        }
        data = _post_json(f"{base_url}/api/generate", body, timeout=config.timeout_seconds)
        return _loads_json_object(str(data.get("response") or "{}"))


def _config() -> LineageAIConfig:
    from app.services.lineage_ai_config import get_effective_lineage_ai_config

    effective = get_effective_lineage_ai_config()
    provider = str(effective.get("provider") or "off").strip().lower() or "off"
    return LineageAIConfig(
        provider=provider,
        model=str(effective.get("model") or "").strip(),
        base_url=str(effective.get("base_url") or "").strip(),
        api_key=str(effective.get("api_key") or "").strip(),
        timeout_seconds=float(effective.get("timeout_seconds") or 20),
    )


def _config_with_override(override: dict[str, Any]) -> LineageAIConfig:
    base = _config()
    provider = str(override.get("provider") or base.provider or "off").strip().lower() or "off"
    timeout = override.get("timeout_seconds", base.timeout_seconds)
    try:
        timeout_seconds = float(timeout if timeout not in (None, "") else base.timeout_seconds)
    except (TypeError, ValueError):
        timeout_seconds = base.timeout_seconds
    return LineageAIConfig(
        provider=provider,
        model=str(override.get("model") if override.get("model") is not None else base.model).strip(),
        base_url=str(override.get("base_url") if override.get("base_url") is not None else base.base_url).strip(),
        api_key=str(override.get("api_key") or base.api_key).strip(),
        timeout_seconds=timeout_seconds,
    )


def _provider_for(name: str) -> LineageAIProvider | None:
    normalized = (name or "off").lower()
    if normalized in {"off", "disabled", "none"}:
        return None
    if normalized == "mock":
        return MockLineageAIProvider()
    if normalized in {"openai", "azure", "http", "openai-compatible"}:
        return OpenAICompatibleLineageAIProvider()
    if normalized == "ollama":
        return OllamaLineageAIProvider()
    return None


def _build_payload(
    result: dict[str, Any],
    *,
    sql_text: str,
    dialect: str | None,
    scope: str,
    scripts: list[dict[str, str]],
) -> dict[str, Any]:
    report = result.get("report") or {}
    summary = report.get("summary") if isinstance(report, dict) else {}
    return {
        "scope": scope,
        "dialect": dialect or "",
        "sql_excerpt": _truncate(sql_text, 4000),
        "scripts": [
            {"file_name": item.get("file_name", ""), "sql_excerpt": _truncate(item.get("sql", ""), 1500)}
            for item in scripts[:20]
        ],
        "summary": summary or _fallback_summary(result),
        "inputs": (report.get("inputs") if isinstance(report, dict) else []) or [],
        "outputs": (report.get("outputs") if isinstance(report, dict) else []) or [],
        "table_edges": _limit_list((report.get("table_edges") if isinstance(report, dict) else []) or result.get("graph_edges", []), 80),
        "column_edges": _limit_list((report.get("column_edges") if isinstance(report, dict) else []) or result.get("insert_mappings", []), 120),
        "warnings": _limit_list(result.get("warnings", []), 80),
        "parse_errors": _limit_list(result.get("parse_errors", []), 80),
        "risks": _limit_list((report.get("risks") if isinstance(report, dict) else []) or [], 80),
    }


def _normalize_enrichment(
    value: dict[str, Any],
    *,
    provider: str,
    model: str,
    elapsed_seconds: float,
) -> dict[str, Any]:
    return {
        "enabled": True,
        "status": "success",
        "provider": provider,
        "model": model,
        "elapsed_seconds": elapsed_seconds,
        "summary": str(value.get("summary") or ""),
        "suggestions": _list_of_dicts(value.get("suggestions")),
        "risks": _list_of_dicts(value.get("risks")),
        "column_hints": _list_of_dicts(value.get("column_hints")),
        "raw": value if _include_raw_enabled() else {},
    }


def _disabled_enrichment() -> dict[str, Any]:
    return {
        "enabled": False,
        "status": "disabled",
        "provider": "off",
        "model": "",
        "elapsed_seconds": 0,
        "summary": "",
        "suggestions": [],
        "risks": [],
        "column_hints": [],
    }


def _error_enrichment(
    *,
    provider: str,
    model: str,
    error: str,
    elapsed_seconds: float = 0,
) -> dict[str, Any]:
    return {
        "enabled": True,
        "status": "error",
        "provider": provider,
        "model": model,
        "elapsed_seconds": elapsed_seconds,
        "summary": "",
        "suggestions": [],
        "risks": [],
        "column_hints": [],
        "error": error,
    }


def _include_raw_enabled() -> bool:
    try:
        from app.services.lineage_ai_config import get_effective_lineage_ai_config

        return bool(get_effective_lineage_ai_config().get("include_raw"))
    except Exception:
        return os.getenv("DATAOPS_LINEAGE_AI_INCLUDE_RAW", "false").lower() in {"1", "true", "yes"}


def _post_json(
    url: str,
    body: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    timeout: float,
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - user-configured AI endpoint
        return json.loads(response.read().decode("utf-8"))


def _loads_json_object(content: str) -> dict[str, Any]:
    data = json.loads(content)
    if not isinstance(data, dict):
        raise ValueError("provider JSON response must be an object")
    return data


def _fallback_summary(result: dict[str, Any]) -> dict[str, int]:
    return {
        "table_edge_count": len(result.get("graph_edges", []) or []),
        "column_edge_count": len(result.get("insert_mappings", []) or []),
        "warning_count": len(result.get("warnings", []) or []),
        "parse_error_count": len(result.get("parse_errors", []) or []),
    }


def _limit_list(value: Any, limit: int) -> list[Any]:
    return list(value or [])[:limit] if isinstance(value, list) else []


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _truncate(value: str, limit: int) -> str:
    text = value or ""
    return text if len(text) <= limit else text[:limit] + "\n/* truncated */"
