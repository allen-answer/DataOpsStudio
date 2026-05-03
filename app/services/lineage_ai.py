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
    # 采样温度。Kimi K2.6 等模型不接受 0（会 400），必须 > 0。默认 0.2 兼顾稳定 + 兼容
    temperature: float = 0.2
    # 关闭 thinking（仅 Kimi K2.6 等支持 thinking 的模型有用）。默认 True 避免
    # reasoning_content 吃掉输出 token 让 content 长时间为空。
    disable_thinking: bool = True


class LineageAIProvider(Protocol):
    name: str

    def enrich(self, payload: dict[str, Any], config: LineageAIConfig) -> dict[str, Any]:
        ...


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
    if not enabled and config.provider == "off":
        result["ai_enrichment"] = _disabled_enrichment()
        return result

    provider = _provider_for(config.provider)
    if provider is None:
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
            raise ValueError("DATAOPS_LINEAGE_AI_API_KEY is required")
        if not config.model:
            raise ValueError("DATAOPS_LINEAGE_AI_MODEL is required")
        base_url = config.base_url.rstrip("/") or "https://api.openai.com/v1"
        body: dict[str, Any] = {
            "model": config.model,
            # Kimi K2.6 不接受 temperature=0（直接 400）。默认 0.2 兼容；可由 env 覆盖
            "temperature": max(config.temperature, 0.01),
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
        # Kimi K2.6 / 月之暗面默认开启 thinking，reasoning_content 会先吃掉输出 token
        # → content 长时间为空。显式 enable_thinking=false 让它直接吐 content。
        # 不支持该字段的服务（OpenAI / 其它兼容 API）忽略未知字段，对它们无副作用。
        if config.disable_thinking:
            body["enable_thinking"] = False
        data = _post_json(
            f"{base_url}/chat/completions",
            body,
            headers={"Authorization": f"Bearer {config.api_key}"},
            timeout=config.timeout_seconds,
        )
        message = (data.get("choices") or [{}])[0].get("message") or {}
        # content 优先；空时 fallback 到 reasoning_content（thinking 模式下模型把 JSON 写在
        # reasoning 而不是 content；某些 Kimi 配置下也会出现）
        content = message.get("content") or message.get("reasoning_content") or "{}"
        return _loads_json_object(content)


class OllamaLineageAIProvider:
    name = "ollama"

    def enrich(self, payload: dict[str, Any], config: LineageAIConfig) -> dict[str, Any]:
        if not config.model:
            raise ValueError("DATAOPS_LINEAGE_AI_MODEL is required")
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
    provider = os.getenv("DATAOPS_LINEAGE_AI_PROVIDER", "off").strip().lower() or "off"
    disable_thinking = os.getenv("DATAOPS_LINEAGE_AI_DISABLE_THINKING", "true").strip().lower() not in {"0", "false", "no"}
    return LineageAIConfig(
        provider=provider,
        model=os.getenv("DATAOPS_LINEAGE_AI_MODEL", "").strip(),
        base_url=os.getenv("DATAOPS_LINEAGE_AI_BASE_URL", "").strip(),
        api_key=os.getenv("DATAOPS_LINEAGE_AI_API_KEY", "").strip(),
        timeout_seconds=float(os.getenv("DATAOPS_LINEAGE_AI_TIMEOUT_SECONDS", "20")),
        temperature=float(os.getenv("DATAOPS_LINEAGE_AI_TEMPERATURE", "0.2")),
        disable_thinking=disable_thinking,
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
        "raw": value if os.getenv("DATAOPS_LINEAGE_AI_INCLUDE_RAW", "false").lower() in {"1", "true", "yes"} else {},
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
