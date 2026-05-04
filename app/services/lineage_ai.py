from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from urllib.parse import urlsplit, urlunsplit
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol


logger = logging.getLogger(__name__)

_AI_JOB_LOCK = threading.Lock()
_AI_JOBS: dict[str, dict[str, Any]] = {}
_MAX_AI_JOBS = 200


@dataclass(frozen=True)
class LineageAIConfig:
    provider: str = "off"
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    timeout_seconds: float = 60.0
    # Phase 7 双轨 A AI 兜底：解析失败 / 低置信片段调 AI 补充推断（默认关）
    # 不替代静态解析；输出独立 result["ai_inferred"] 字段，前端虚线 + 徽章区分
    enable_inference: bool = False
    inference_max_fragment_chars: int = 8000
    inference_max_fragments: int = 10
    # Phase 9 Day 6：错误自动翻译。默认关 —— 默认让用户主动点 ✨"AI 解释"
    # 按钮触发翻译；admin 在 AIConfig 显式开启后才会按 5xx / 长 4xx 自动调
    # /api/ai/translate-error。原因：自动翻译每个错误都烧 token，多数错误
    # （403 / 404 / 简洁 4xx）已经够清楚，不值得花成本翻。
    enable_auto_translation: bool = False


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
        request_payload = _compact_payload_for_provider({
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
        }, config=config)
        logger.info(
            "lineage AI connection test provider=%s model=%s payload_chars=%s timeout=%ss",
            config.provider,
            config.model,
            len(json.dumps(request_payload, ensure_ascii=False)),
            config.timeout_seconds,
        )
        enrichment = provider.enrich(
            request_payload,
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
        result["ai_enrichment"] = _disabled_enrichment(
            reason="not_requested",
            message="本次分析未请求 AI 辅助。",
        )
        return result

    provider = _provider_for(config.provider)
    if provider is None:
        if config.provider in {"off", "disabled", "none", ""}:
            result["ai_enrichment"] = _disabled_enrichment(
                reason="provider_off",
                message="AI provider 未启用；请在 AI 配置中选择 provider 并保存配置。",
            )
        else:
            result["ai_enrichment"] = _error_enrichment(
                provider=config.provider,
                model=config.model,
                error=f"unsupported provider: {config.provider}",
            )
        return result

    config_error = _config_error(config)
    if config_error:
        result["ai_enrichment"] = _error_enrichment(
            provider=config.provider,
            model=config.model,
            error=config_error,
        )
        return result

    payload = _build_payload(result, sql_text=sql_text, dialect=dialect, scope=scope, scripts=scripts or [])
    payload = _compact_payload_for_provider(payload, config=config)
    started = time.perf_counter()
    try:
        payload_chars = len(json.dumps(payload, ensure_ascii=False))
        logger.info(
            "lineage AI enrichment start provider=%s model=%s payload_chars=%s timeout=%ss",
            config.provider,
            config.model,
            payload_chars,
            config.timeout_seconds,
        )
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


def enqueue_lineage_ai_enrichment(
    result: dict[str, Any],
    *,
    sql_text: str = "",
    dialect: str | None = None,
    scope: str = "single",
    scripts: list[dict[str, str]] | None = None,
    enabled: bool = False,
) -> dict[str, Any]:
    """Attach a pending AI job and run provider enrichment in the background."""
    config = _config()
    if not enabled:
        result["ai_enrichment"] = _disabled_enrichment(
            reason="not_requested",
            message="本次分析未请求 AI 辅助。",
        )
        return result

    provider = _provider_for(config.provider)
    if provider is None:
        if config.provider in {"off", "disabled", "none", ""}:
            result["ai_enrichment"] = _disabled_enrichment(
                reason="provider_off",
                message="AI provider 未启用；请在 AI 配置中选择 provider 并保存配置。",
            )
        else:
            result["ai_enrichment"] = _error_enrichment(
                provider=config.provider,
                model=config.model,
                error=f"unsupported provider: {config.provider}",
            )
        return result

    config_error = _config_error(config)
    if config_error:
        result["ai_enrichment"] = _error_enrichment(
            provider=config.provider,
            model=config.model,
            error=config_error,
        )
        return result

    payload = _compact_payload_for_provider(
        _build_payload(result, sql_text=sql_text, dialect=dialect, scope=scope, scripts=scripts or []),
        config=config,
    )
    job_id = uuid.uuid4().hex
    pending = {
        "enabled": True,
        "status": "pending",
        "job_id": job_id,
        "provider": provider.name,
        "model": config.model,
        "elapsed_seconds": 0,
        "summary": "",
        "suggestions": [],
        "risks": [],
        "column_hints": [],
    }
    _store_ai_job(job_id, pending)
    thread = threading.Thread(
        target=_run_ai_job,
        args=(job_id, provider, config, payload),
        name=f"lineage-ai-{job_id[:8]}",
        daemon=True,
    )
    thread.start()
    result["ai_enrichment"] = pending
    return result


def get_lineage_ai_job(job_id: str) -> dict[str, Any] | None:
    with _AI_JOB_LOCK:
        item = _AI_JOBS.get(job_id)
        return dict(item) if item else None


def _run_ai_job(
    job_id: str,
    provider: LineageAIProvider,
    config: LineageAIConfig,
    payload: dict[str, Any],
) -> None:
    started = time.perf_counter()
    try:
        payload_chars = len(json.dumps(payload, ensure_ascii=False))
        logger.info(
            "lineage AI async enrichment start job_id=%s provider=%s model=%s payload_chars=%s timeout=%ss",
            job_id,
            config.provider,
            config.model,
            payload_chars,
            config.timeout_seconds,
        )
        enrichment = provider.enrich(payload, config)
        if not isinstance(enrichment, dict):
            raise ValueError("provider returned non-object enrichment")
        result = _normalize_enrichment(
            enrichment,
            provider=provider.name,
            model=config.model,
            elapsed_seconds=round(time.perf_counter() - started, 3),
        )
        result["job_id"] = job_id
        _store_ai_job(job_id, result)
    except Exception as exc:
        logger.exception("lineage AI async enrichment failed job_id=%s provider=%s", job_id, config.provider)
        result = _error_enrichment(
            provider=config.provider,
            model=config.model,
            error=str(exc),
            elapsed_seconds=round(time.perf_counter() - started, 3),
        )
        result["job_id"] = job_id
        _store_ai_job(job_id, result)


def _store_ai_job(job_id: str, payload: dict[str, Any]) -> None:
    with _AI_JOB_LOCK:
        if len(_AI_JOBS) >= _MAX_AI_JOBS:
            for stale_id in list(_AI_JOBS)[: max(1, _MAX_AI_JOBS // 5)]:
                _AI_JOBS.pop(stale_id, None)
        _AI_JOBS[job_id] = dict(payload)


class MockLineageAIProvider:
    name = "mock"

    def enrich(self, payload: dict[str, Any], config: LineageAIConfig) -> dict[str, Any]:
        summary = payload.get("summary", {})
        return {
            "summary": (
                f"静态解析共识别出 {summary.get('table_edge_count', 0)} 条表级血缘 + "
                f"{summary.get('column_edge_count', 0)} 条字段级血缘。"
            ),
            "suggestions": [
                {
                    "message": "优先核对低置信字段、动态 SQL 片段和解析失败的脚本。",
                    "confidence": "medium",
                    "evidence": ["static_summary"],
                }
            ],
            "risks": [],
            "column_hints": [],
        }


_ENRICHMENT_SYSTEM_PROMPT = (
    "你是 SQL 血缘语义增强助手。根据用户给的【确定性解析结果】（已经由静态解析器跑出来），\n"
    "用业务语言写一段语义解读，帮数据治理 / 数据工程师快速看懂这批 SQL 在做什么。\n\n"
    "硬性规则（违反必返工）：\n"
    "1. 输出语言：**全程使用简体中文**。表名 / 字段名 / SQL 关键字保留原文（用反引号包起来）。\n"
    "2. 输出格式：仅返回 JSON 对象，包含 4 个键：summary / suggestions / risks / column_hints。"
    "JSON 之外不要任何文字 / 注释 / Markdown 围栏。\n"
    "3. **不要质疑、反驳、修正**用户给的 graph_edges / column_edges / table_edges。"
    "这些是静态解析的事实，你只能在它们之上做语义归纳；不要说「应该是 X 而不是 Y」这类话。\n"
    "4. **不要发明**未在用户输入里出现的表名 / 字段名。\n"
    "5. summary：1~3 句话讲清楚「这批脚本/单脚本主要做什么」，业务向（例如「账户事实表全量重刷」），"
    "不要罗列文件计数。\n"
    "6. suggestions：每条形如 {message: 中文简短建议, evidence: 引用 file/table/column 名}，"
    "聚焦「配 schema 元数据」「换 MERGE 提升原子性」「补字段说明」等可操作建议；"
    "不要给「应该 review 一下」这类空话。\n"
    "7. risks：每条形如 {level: low|medium|high, type: 简短分类, message: 中文, evidence: 引用}，"
    "聚焦：动态 SQL 不可控 / 解析失败片段 / SELECT * 缺 schema / 跨库依赖 等。"
    "**不要**把字段映射差异当 risk —— 那是静态解析事实。\n"
    "8. column_hints：可空。仅当有具体字段命名 / 类型一致性建议时才填，每条 {column, hint, evidence}。\n\n"
    "示例（仅供格式参考）：\n"
    '{"summary": "批量分析 5 个 DM 方言 ETL 脚本，主流程是月度账户事实表的 DELETE+INSERT 全量重刷。",'
    ' "suggestions": [{"message": "为 kgrp.r_cisp_f* 系列表补 schema 元数据，让 SELECT * 能展开真实字段",'
    ' "evidence": "r_cisp_f3001_t / r_cisp_f3003_t 等 11 张表"}],'
    ' "risks": [{"level": "medium", "type": "动态 SQL",'
    ' "message": "3 处 EXECUTE IMMEDIATE 变量未解析，下游表无法静态确定",'
    ' "evidence": "file_a.sql"}],'
    ' "column_hints": []}'
)


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
            "max_tokens": _openai_compatible_max_tokens(config),
            "messages": [
                {"role": "system", "content": _ENRICHMENT_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        }
        if _should_use_json_object_response_format(config):
            body["response_format"] = {"type": "json_object"}
        if _should_disable_kimi_thinking(config):
            body["thinking"] = {"type": "disabled"}
        data = _post_json(
            _chat_completions_url(base_url),
            body,
            headers={"Authorization": f"Bearer {config.api_key}"},
            timeout=config.timeout_seconds,
        )
        return _loads_json_object(_openai_compatible_content(data))


class AnthropicCompatibleLineageAIProvider:
    name = "anthropic"

    def enrich(self, payload: dict[str, Any], config: LineageAIConfig) -> dict[str, Any]:
        if not config.api_key:
            raise ValueError("AI API key is required")
        if not config.model:
            raise ValueError("AI model is required")
        base_url = config.base_url.rstrip("/") or "https://api.anthropic.com"
        body = {
            "model": config.model,
            "max_tokens": 2048,
            "system": _ENRICHMENT_SYSTEM_PROMPT,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "请对以下静态 SQL 血缘解析结果做语义增强，仅返回中文 JSON：\n"
                                + json.dumps(payload, ensure_ascii=False)
                            ),
                        }
                    ],
                }
            ],
        }
        data = _post_json(
            _anthropic_messages_url(base_url),
            body,
            headers={
                "x-api-key": config.api_key,
                "anthropic-version": "2023-06-01",
            },
            timeout=config.timeout_seconds,
        )
        return _loads_json_object(_anthropic_text_content(data))


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
        timeout_seconds=float(effective.get("timeout_seconds") or 60),
        enable_inference=bool(effective.get("enable_inference") or False),
        inference_max_fragment_chars=int(effective.get("inference_max_fragment_chars") or 8000),
        inference_max_fragments=int(effective.get("inference_max_fragments") or 10),
        enable_auto_translation=bool(effective.get("enable_auto_translation") or False),
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
        enable_inference=bool(
            override.get("enable_inference")
            if override.get("enable_inference") is not None
            else base.enable_inference
        ),
        inference_max_fragment_chars=base.inference_max_fragment_chars,
        inference_max_fragments=base.inference_max_fragments,
        enable_auto_translation=bool(
            override.get("enable_auto_translation")
            if override.get("enable_auto_translation") is not None
            else base.enable_auto_translation
        ),
    )


def _provider_for(name: str) -> LineageAIProvider | None:
    normalized = (name or "off").lower()
    if normalized in {"off", "disabled", "none"}:
        return None
    if normalized == "mock":
        return MockLineageAIProvider()
    if normalized in {"openai", "azure", "http", "openai-compatible"}:
        return OpenAICompatibleLineageAIProvider()
    if normalized in {"anthropic", "anthropic-compatible", "claude"}:
        return AnthropicCompatibleLineageAIProvider()
    if normalized == "ollama":
        return OllamaLineageAIProvider()
    return None


def _config_error(config: LineageAIConfig) -> str:
    provider = (config.provider or "off").lower()
    if provider == "mock":
        return ""
    if provider in {"openai", "azure", "http", "openai-compatible", "anthropic", "anthropic-compatible", "claude"}:
        missing = []
        if not config.model:
            missing.append("model")
        if not config.api_key:
            missing.append("api_key")
        if missing:
            return "AI provider 配置不完整，缺少：" + ", ".join(missing)
    if provider == "ollama" and not config.model:
        return "AI provider 配置不完整，缺少：model"
    return ""


def _chat_completions_url(base_url: str) -> str:
    """Accept common OpenAI-compatible URL shapes.

    Users often copy either the SDK base URL (`.../v1`), the bare service URL,
    or the full endpoint (`.../v1/chat/completions`). Normalize all of them to
    a single chat completions endpoint to avoid accidental 404s.
    """
    raw = (base_url or "").strip().rstrip("/") or "https://api.openai.com/v1"
    if raw.endswith("/chat/completions"):
        return raw

    parts = urlsplit(raw)
    path = parts.path.rstrip("/")
    if path in {"", "/"}:
        path = "/v1/chat/completions"
    else:
        path = f"{path}/chat/completions"
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def _should_disable_kimi_thinking(config: LineageAIConfig) -> bool:
    model = (config.model or "").lower()
    base_url = (config.base_url or "").lower()
    return model.startswith(("kimi-k2.6", "kimi-k2.5")) or "moonshot." in base_url or "kimi." in base_url


def _is_deepseek_compatible(config: LineageAIConfig) -> bool:
    model = (config.model or "").lower()
    base_url = (config.base_url or "").lower()
    return model.startswith("deepseek-") or "deepseek." in base_url


def _is_deepseek_reasoning_model(config: LineageAIConfig) -> bool:
    model = (config.model or "").lower()
    return model in {"deepseek-reasoner"} or "reasoner" in model or model.endswith("-pro")


def _should_use_json_object_response_format(config: LineageAIConfig) -> bool:
    if _should_disable_kimi_thinking(config):
        return False
    base_url = (config.base_url or "").lower()
    return _is_deepseek_compatible(config) or "api.openai.com" in base_url


def _openai_compatible_max_tokens(config: LineageAIConfig) -> int:
    """Provider 输出 token 上限。
    Kimi K2.6 / Anthropic / DeepSeek reasoning 等都需要足够大的 budget 才能完整
    返回 JSON；之前默认 1200 在 payload>20K chars 场景下被截断（content 半截 →
    JSONDecodeError）。env DATAOPS_LINEAGE_AI_MAX_TOKENS 可全局覆盖。
    """
    env_value = os.getenv("DATAOPS_LINEAGE_AI_MAX_TOKENS", "").strip()
    if env_value:
        try:
            value = int(env_value)
            if value > 0:
                return value
        except ValueError:
            pass
    if _is_deepseek_reasoning_model(config):
        return 4096
    if _should_disable_kimi_thinking(config):
        # Kimi K2.6 输出 lineage enrichment 经常 2~3K tokens；4096 兜底
        return 4096
    return 2048


def _openai_compatible_max_tokens_hint() -> str:
    """日志提示用：当前 env / 默认值。"""
    return os.getenv("DATAOPS_LINEAGE_AI_MAX_TOKENS", "").strip() or "default"


def _openai_compatible_content(data: dict[str, Any]) -> str:
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content = str(message.get("content") or "").strip()
    finish_reason = choice.get("finish_reason") or ""
    if content:
        # finish_reason=length 表示 max_tokens 不够，content 是半截 JSON。
        # 仍返回 —— 让 _loads_json_object 的容错（找 { ... }）尝试救一下；
        # 救不了再抛清晰错误（错误会包含 finish_reason），用户能看到要调大 max_tokens。
        if finish_reason == "length":
            logger.warning(
                "lineage AI content truncated by max_tokens; consider raising "
                "DATAOPS_LINEAGE_AI_MAX_TOKENS (current=%s, payload=%s chars)",
                _openai_compatible_max_tokens_hint(),
                len(content),
            )
        return content
    reasoning = str(message.get("reasoning_content") or "")
    if reasoning:
        raise ValueError(
            "provider returned empty content while reasoning_content was present"
            + (f" (finish_reason={finish_reason})" if finish_reason else "")
        )
    raise ValueError("provider returned empty content" + (f" (finish_reason={finish_reason})" if finish_reason else ""))


def _anthropic_messages_url(base_url: str) -> str:
    """Accept Anthropic SDK base URLs and full Messages API endpoints."""
    raw = (base_url or "").strip().rstrip("/") or "https://api.anthropic.com"
    if raw.endswith("/messages"):
        return raw

    parts = urlsplit(raw)
    path = parts.path.rstrip("/")
    if path.endswith("/v1"):
        path = f"{path}/messages"
    elif path in {"", "/"}:
        path = "/v1/messages"
    else:
        path = f"{path}/v1/messages"
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def _anthropic_text_content(data: dict[str, Any]) -> str:
    content = data.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                chunks.append(block["text"])
        if chunks:
            return "\n".join(chunks)
    # Some proxies return OpenAI-like envelopes even for Anthropic-compatible
    # routes; accepting this keeps the adapter useful behind gateways.
    return ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "{}"


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
        "sql_excerpt": _truncate(sql_text, 1500),
        "scripts": [
            {"file_name": item.get("file_name", ""), "sql_excerpt": _truncate(item.get("sql", ""), 600)}
            for item in scripts[:8]
        ],
        "summary": summary or _fallback_summary(result),
        "inputs": _limit_list((report.get("inputs") if isinstance(report, dict) else []) or [], 20),
        "outputs": _limit_list((report.get("outputs") if isinstance(report, dict) else []) or [], 20),
        "table_edges": _limit_list((report.get("table_edges") if isinstance(report, dict) else []) or result.get("graph_edges", []), 25),
        "column_edges": _limit_list((report.get("column_edges") if isinstance(report, dict) else []) or result.get("insert_mappings", []), 30),
        "warnings": _limit_list(result.get("warnings", []), 15),
        "parse_errors": _limit_list(result.get("parse_errors", []), 10),
        "risks": _limit_list((report.get("risks") if isinstance(report, dict) else []) or [], 15),
    }


def _compact_payload_for_provider(payload: dict[str, Any], *, config: LineageAIConfig | None = None) -> dict[str, Any]:
    limits = _payload_limits(config)
    compact = json.loads(json.dumps(payload, ensure_ascii=False, default=str))
    compact["sql_excerpt"] = _truncate(str(compact.get("sql_excerpt") or ""), limits["sql_excerpt"])
    compact["scripts"] = [
        {
            "file_name": item.get("file_name", ""),
            "sql_excerpt": _truncate(str(item.get("sql_excerpt") or ""), limits["script_excerpt"]),
        }
        for item in _limit_list(compact.get("scripts"), limits["scripts"])
        if isinstance(item, dict)
    ]
    compact["inputs"] = _compact_items(compact.get("inputs"), limits["inputs"], string_limit=limits["item_string"])
    compact["outputs"] = _compact_items(compact.get("outputs"), limits["outputs"], string_limit=limits["item_string"])
    compact["table_edges"] = _compact_items(compact.get("table_edges"), limits["table_edges"], string_limit=limits["item_string"])
    compact["column_edges"] = _compact_items(compact.get("column_edges"), limits["column_edges"], string_limit=limits["item_string"])
    compact["warnings"] = _compact_items(compact.get("warnings"), limits["warnings"], string_limit=limits["item_string"])
    compact["parse_errors"] = _compact_items(compact.get("parse_errors"), limits["parse_errors"], string_limit=limits["item_string"])
    compact["risks"] = _compact_items(compact.get("risks"), limits["risks"], string_limit=limits["item_string"])
    return compact


def _payload_limits(config: LineageAIConfig | None) -> dict[str, int]:
    if config and (_should_disable_kimi_thinking(config) or _is_deepseek_reasoning_model(config)):
        return {
            "sql_excerpt": 900,
            "script_excerpt": 360,
            "scripts": 4,
            "inputs": 12,
            "outputs": 12,
            "table_edges": 14,
            "column_edges": 18,
            "warnings": 8,
            "parse_errors": 6,
            "risks": 8,
            "item_string": 160,
        }
    return {
        "sql_excerpt": 1500,
        "script_excerpt": 600,
        "scripts": 8,
        "inputs": 20,
        "outputs": 20,
        "table_edges": 25,
        "column_edges": 30,
        "warnings": 15,
        "parse_errors": 10,
        "risks": 15,
        "item_string": 240,
    }


def _compact_items(value: Any, limit: int, *, string_limit: int = 240) -> list[Any]:
    items = _limit_list(value, limit)
    return [_compact_value(item, depth=0, string_limit=string_limit) for item in items]


def _compact_value(value: Any, *, depth: int, string_limit: int = 240) -> Any:
    if isinstance(value, str):
        return _truncate(value, string_limit)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_compact_value(item, depth=depth + 1, string_limit=string_limit) for item in value[:8]]
    if isinstance(value, dict):
        if depth >= 2:
            return {str(k): _truncate(str(v), max(80, string_limit // 2)) for k, v in list(value.items())[:10]}
        return {str(k): _compact_value(v, depth=depth + 1, string_limit=string_limit) for k, v in list(value.items())[:16]}
    return _truncate(str(value), 160)


def _normalize_enrichment(
    value: dict[str, Any],
    *,
    provider: str,
    model: str,
    elapsed_seconds: float,
) -> dict[str, Any]:
    suggestions = _list_of_dicts(value.get("suggestions"))
    risks = _list_of_dicts(value.get("risks"))
    column_hints = _list_of_dicts(value.get("column_hints"))
    summary = str(value.get("summary") or "").strip()
    if not summary and not suggestions and not risks and not column_hints:
        return _error_enrichment(
            provider=provider,
            model=model,
            error="provider returned empty AI enrichment",
            elapsed_seconds=elapsed_seconds,
        )
    return {
        "enabled": True,
        "status": "success",
        "provider": provider,
        "model": model,
        "elapsed_seconds": elapsed_seconds,
        "summary": summary,
        "suggestions": suggestions,
        "risks": risks,
        "column_hints": column_hints,
        "raw": value if _include_raw_enabled() else {},
    }


def _disabled_enrichment(reason: str = "disabled", message: str = "") -> dict[str, Any]:
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
        "reason": reason,
        "message": message,
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
    text = (content or "{}").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].lstrip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        if start < 0:
            raise
        # 1) 优先：内容完整，尾部有匹配的 }
        end = text.rfind("}")
        if end > start:
            try:
                data = json.loads(text[start:end + 1])
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                pass
        # 2) 兜底：truncate 到一半的 JSON。从 start 起逐字符走，收集
        #    "summary" / "suggestions" / "risks" / "column_hints" 4 个 top-level
        #    字段已经写完整的部分，给前端一个降级视图而不是完全空。
        salvaged = _salvage_truncated_json_object(text[start:])
        if salvaged is not None:
            return salvaged
        raise
    if not isinstance(data, dict):
        raise ValueError("provider JSON response must be an object")
    return data


def _salvage_truncated_json_object(text: str) -> dict[str, Any] | None:
    """从被截断的 JSON 串里抢救 top-level 完整 key-value pair。

    Kimi 等模型在 max_tokens 不够时给出半截 JSON：开头 `{"summary":"已完成...","suggestions":[{...},{...},...`
    末尾停在某个 escape 序列里。普通 json.loads 必失败。
    这里做精简的 brace/bracket/quote 平衡扫，沿途记录"上一个 ',' 之前已平衡的位置"
    最后用那个安全切点 + `}` 闭合再 loads。

    成功返回 dict，失败返回 None。
    """
    in_str = False
    escape = False
    depth_brace = 0
    depth_bracket = 0
    last_safe_end = -1  # 上一次顶层 brace 内 ',' 的位置（到该位置为止 JSON 是 well-formed 的去掉末尾 ',' 后）
    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_str:
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth_brace += 1
        elif ch == "}":
            depth_brace -= 1
        elif ch == "[":
            depth_bracket += 1
        elif ch == "]":
            depth_bracket -= 1
        elif ch == "," and depth_brace == 1 and depth_bracket == 0:
            last_safe_end = i  # 截在这个 ',' → 去掉 + 补 '}'
    if last_safe_end < 0:
        return None
    candidate = text[:last_safe_end] + "}"
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    data["_truncated"] = True
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
    normalized: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            normalized.append(dict(item))
        elif isinstance(item, str) and item.strip():
            normalized.append({"message": item.strip()})
    return normalized


def _truncate(value: str, limit: int) -> str:
    text = value or ""
    return text if len(text) <= limit else text[:limit] + "\n/* truncated */"
