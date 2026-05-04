"""通用 AI 工具 endpoint：错误翻译 / 字段映射推荐 / 对比解读 等。

跟 lineage AI 走相同的 provider 抽象（lineage_ai_config 持久化的 provider /
api_key），但场景独立：每个 endpoint 自带 system prompt，不污染 enrichment。

Phase 5：/api/ai/translate-error  ——  错误消息翻译 + 排查建议
Phase 6：/api/ai/suggest-column-mapping  ——  对比任务 column_mappings 推荐
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.models import User
from app.services.auth import get_current_user
from app.services.lineage_ai import (
    LineageAIConfig,
    _anthropic_messages_url,
    _anthropic_text_content,
    _chat_completions_url,
    _config as _ai_config,
    _loads_json_object,
    _openai_compatible_content,
    _openai_compatible_max_tokens,
    _post_json,
    _should_disable_kimi_thinking,
)
from app.services.lineage_ai_config import (
    ANTHROPIC_COMPATIBLE_PROVIDERS,
    OPENAI_COMPATIBLE_PROVIDERS,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ─── 错误翻译（Phase 5） ──────────────────────────────────────────────────────


_ERROR_TRANSLATE_PROMPT = (
    "你是数据库错误消息翻译助手。用户给一段数据库报错（可能是英文 / 厂商专有 /\n"
    "深奥的 SQL 状态码），需要：\n"
    "1. 用一句中文翻译错误本质（避免逐字直译，要讲清楚错误在哪里）\n"
    "2. 给 1~3 条具体排查建议（不是空话），优先推荐排查动作而不是原理\n\n"
    "硬性规则：\n"
    "- 仅返回 JSON 对象，含 translation 和 suggestions 两个键\n"
    "- 不要复述原错误；不要 markdown；不要解释 JSON 之外的内容\n"
    "- 中文输出；表名 / 字段名 / 错误码保留原文\n"
    "- 没法翻译时返回 translation 空字符串 + suggestions 空数组\n\n"
    "示例输入：ORA-00942: table or view does not exist + sql=SELECT * FROM xx\n"
    "示例输出："
    '{"translation": "ORA-00942：表或视图 xx 不存在",'
    ' "suggestions": ["核对表名拼写", "检查当前用户是否有该表的 SELECT 权限",'
    ' "确认表所在的 schema（可能需要 schema.table 全限定名）"]}'
)


@router.post("/api/ai/translate-error")
def translate_error_api(
    payload: dict[str, Any],
    _: User = Depends(get_current_user),
):
    """对一段数据库错误做中文翻译 + 排查建议。

    入参：{ error_text, sql_excerpt?, db_type? }
    返回：{ ok, translation, suggestions, elapsed_seconds, provider, model }
    """
    error_text = str(payload.get("error_text") or "").strip()
    if not error_text:
        raise HTTPException(status_code=400, detail="error_text is required")

    config = _ai_config()
    provider_name = (config.provider or "off").lower()
    if provider_name in {"off", "disabled", "none", ""}:
        return _disabled_response(config)
    if (
        provider_name not in OPENAI_COMPATIBLE_PROVIDERS
        and provider_name not in ANTHROPIC_COMPATIBLE_PROVIDERS
        and provider_name != "mock"
    ):
        return _disabled_response(config, error=f"provider {provider_name} 不支持错误翻译")

    user_payload = {
        "error_text": error_text[:4000],
        "sql_excerpt": str(payload.get("sql_excerpt") or "")[:1500],
        "db_type": str(payload.get("db_type") or "").strip(),
    }
    started = time.perf_counter()
    try:
        raw = _call_ai(provider_name, config, _ERROR_TRANSLATE_PROMPT, user_payload)
        translation = str((raw or {}).get("translation") or "").strip()
        suggestions = _string_list(raw.get("suggestions") if isinstance(raw, dict) else None)
        return {
            "ok": True,
            "translation": translation,
            "suggestions": suggestions,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "provider": provider_name,
            "model": config.model,
        }
    except Exception as exc:
        logger.warning("translate-error failed: %s", exc)
        return {
            "ok": False,
            "translation": "",
            "suggestions": [],
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "provider": provider_name,
            "model": config.model,
            "error": str(exc),
        }


# ─── 字段映射推荐（Phase 6） ──────────────────────────────────────────────────


_COLUMN_MAPPING_PROMPT = (
    "你是数据对比字段映射推荐助手。用户对比两个数据源（source / target），\n"
    "字段命名风格不一致（如 source 用 user_id / target 用 userId / 客户号 等），\n"
    "需要根据字段名语义 + 样本值 推荐 source → target 的字段映射。\n\n"
    "硬性规则：\n"
    "- 仅返回 JSON `{\"mappings\": [{source, target, confidence, reason}], \"unmatched\": [\"src_col\", ...]}`\n"
    "- mappings：每条 source / target 必须分别在 source_fields / target_fields 里；\n"
    "  confidence: high(命名+样本完全匹配) / medium(命名近似) / low(仅猜测)\n"
    "- reason: **中文**，1 句话讲清依据（命名相似 / 样本值类型一致 / 业务命名习惯等）\n"
    "- 不要发明字段；同名字段不要重复推荐（confidence=high 默认即可）\n"
    "- unmatched：source 里没找到对应 target 的字段名列表\n"
    "- 没法推荐时 mappings=[] unmatched=全部 source_fields\n"
)


@router.post("/api/ai/suggest-column-mapping")
def suggest_column_mapping_api(
    payload: dict[str, Any],
    _: User = Depends(get_current_user),
):
    """根据 source/target 字段 + 样本值 推荐 column_mappings。

    入参：{ source_fields: [...], target_fields: [...],
           source_samples?: [{col: val, ...}, ...] (前 5 行)，
           target_samples?: [...] }
    返回：{ ok, mappings: [{source, target, confidence, reason}], unmatched, elapsed_seconds }
    """
    source_fields = payload.get("source_fields") or []
    target_fields = payload.get("target_fields") or []
    if not isinstance(source_fields, list) or not source_fields:
        raise HTTPException(status_code=400, detail="source_fields is required")
    if not isinstance(target_fields, list) or not target_fields:
        raise HTTPException(status_code=400, detail="target_fields is required")

    config = _ai_config()
    provider_name = (config.provider or "off").lower()
    if provider_name in {"off", "disabled", "none", ""}:
        return _disabled_response(config)
    if (
        provider_name not in OPENAI_COMPATIBLE_PROVIDERS
        and provider_name not in ANTHROPIC_COMPATIBLE_PROVIDERS
        and provider_name != "mock"
    ):
        return _disabled_response(config, error=f"provider {provider_name} 不支持字段映射推荐")

    source_set = {str(f) for f in source_fields if isinstance(f, str)}
    target_set = {str(f) for f in target_fields if isinstance(f, str)}
    user_payload = {
        "source_fields": list(source_set)[:200],
        "target_fields": list(target_set)[:200],
        "source_samples": _trim_samples(payload.get("source_samples")),
        "target_samples": _trim_samples(payload.get("target_samples")),
    }
    started = time.perf_counter()
    try:
        raw = _call_ai(provider_name, config, _COLUMN_MAPPING_PROMPT, user_payload)
        mappings, filtered = _filter_mappings(
            raw.get("mappings") if isinstance(raw, dict) else None,
            source_set=source_set,
            target_set=target_set,
        )
        unmatched = [
            s for s in source_fields
            if isinstance(s, str) and s not in {m["source"] for m in mappings}
        ]
        return {
            "ok": True,
            "mappings": mappings,
            "unmatched": unmatched,
            "filtered_count": filtered,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "provider": provider_name,
            "model": config.model,
        }
    except Exception as exc:
        logger.warning("suggest-column-mapping failed: %s", exc)
        return {
            "ok": False,
            "mappings": [],
            "unmatched": [],
            "filtered_count": 0,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "provider": provider_name,
            "model": config.model,
            "error": str(exc),
        }


# ─── 内部 helpers ─────────────────────────────────────────────────────────────


def _call_ai(provider_name: str, config: LineageAIConfig, system_prompt: str, user_payload: dict[str, Any]) -> dict[str, Any]:
    if not config.api_key and provider_name != "mock":
        raise ValueError("AI API key is required")
    if provider_name == "mock":
        return {"translation": "[mock] 翻译占位", "suggestions": [], "mappings": []}
    user_content = json.dumps(user_payload, ensure_ascii=False)
    if provider_name in OPENAI_COMPATIBLE_PROVIDERS:
        base_url = config.base_url.rstrip("/") or "https://api.openai.com/v1"
        body: dict[str, Any] = {
            "model": config.model,
            "max_tokens": _openai_compatible_max_tokens(config),
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        }
        if _should_disable_kimi_thinking(config):
            body["thinking"] = {"type": "disabled"}
        data = _post_json(
            _chat_completions_url(base_url),
            body,
            headers={"Authorization": f"Bearer {config.api_key}"},
            timeout=config.timeout_seconds,
        )
        return _loads_json_object(_openai_compatible_content(data))
    if provider_name in ANTHROPIC_COMPATIBLE_PROVIDERS:
        base_url = config.base_url.rstrip("/") or "https://api.anthropic.com"
        body = {
            "model": config.model,
            "max_tokens": 4096,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_content}],
        }
        data = _post_json(
            _anthropic_messages_url(base_url),
            body,
            headers={"x-api-key": config.api_key, "anthropic-version": "2023-06-01"},
            timeout=config.timeout_seconds,
        )
        return _loads_json_object(_anthropic_text_content(data))
    raise ValueError(f"unsupported provider: {provider_name}")


def _disabled_response(config: LineageAIConfig, error: str = "") -> dict[str, Any]:
    return {
        "ok": False,
        "translation": "",
        "suggestions": [],
        "mappings": [],
        "unmatched": [],
        "elapsed_seconds": 0,
        "provider": config.provider or "off",
        "model": config.model or "",
        "error": error or "AI provider 未启用；请在 admin → AI 配置中启用 provider",
    }


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            out.append(item.strip()[:300])
        elif isinstance(item, dict):
            text = str(item.get("message") or item.get("text") or "").strip()
            if text:
                out.append(text[:300])
    return out[:10]


def _trim_samples(samples: Any) -> list[dict[str, Any]]:
    if not isinstance(samples, list):
        return []
    out: list[dict[str, Any]] = []
    for row in samples[:5]:  # 最多 5 行样本
        if not isinstance(row, dict):
            continue
        # 每个 cell 截断 100 字符防 token 爆
        out.append({
            str(k): (str(v)[:100] if v is not None else None)
            for k, v in list(row.items())[:50]
        })
    return out


def _filter_mappings(
    raw: Any,
    *,
    source_set: set[str],
    target_set: set[str],
) -> tuple[list[dict[str, Any]], int]:
    if not isinstance(raw, list):
        return [], 0
    out: list[dict[str, Any]] = []
    filtered = 0
    seen_sources: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            filtered += 1
            continue
        s = str(item.get("source") or "").strip()
        t = str(item.get("target") or "").strip()
        if not s or not t or s not in source_set or t not in target_set:
            filtered += 1
            continue
        if s in seen_sources:
            filtered += 1
            continue
        seen_sources.add(s)
        confidence = str(item.get("confidence") or "low").lower()
        if confidence not in {"high", "medium", "low"}:
            confidence = "low"
        out.append({
            "source": s,
            "target": t,
            "confidence": confidence,
            "reason": str(item.get("reason") or "AI 推断（无附加说明）").strip()[:200],
        })
    return out, filtered
