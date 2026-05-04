"""AI 解析失败兜底（Phase 7 双轨 A · 增量）。

适用场景：sqlglot 静态解析直接失败的片段（result["parse_errors"]）。
**严格不替代**已成功解析的 graph_edges / insert_mappings —— 只对静态解析放弃的
片段从 0 加价值。

设计不变量：
1. 不动已解析的 graph_edges / insert_mappings / column_edges
2. 输出落独立字段 result["ai_inferred"]，跟原始结果并列
3. 白名单约束：prompt 强制 "只能从这个表名集合里选"，post-filter 把幻觉 strip
4. 每条 inferred edge 必带 confidence / reason / evidence（引用 SQL 片段）
5. 前端必须明显区分（虚线 + AI 徽章），由调用方保证
6. 默认关闭，由 LineageAIConfig.enable_inference 控制

输入：
    parse_errors: list[dict]  # 每条 {sql, error}
    table_whitelist: set[str]  # 整脚本里出现过的表名（标准化后）
    column_whitelist: set[str]  # 整脚本里出现过的字段名
    dialect: str
    config: LineageAIConfig (provider 必须可用)

输出：
    {
        "edges": [
            {
                "source_table": "ods.t1",
                "target_table": "dwd.t2",
                "dml_type": "INSERT",  # INSERT/UPDATE/MERGE/DELETE
                "source_columns": ["t1.id", "t1.name"],  # 可空
                "target_columns": ["id", "name"],          # 可空
                "confidence": "low",  # AI 推断默认 low；仅有 strong evidence 时 medium
                "reason": "中文人话说明依据",
                "evidence": "EXECUTE IMMEDIATE 'INSERT INTO ' || ...",  # 引自 SQL 片段
                "fragment_index": 0,  # 对应 parse_errors 中第几条
            },
            ...
        ],
        "warnings": [...],  # 推断过程的告警，如"AI 返回非白名单表已过滤"
        "trigger_count": int,  # 实际调 AI 的片段数（裁过 max_chars 的不算）
        "filtered_count": int,  # 因白名单过滤丢掉的条目
    }
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.services.lineage_ai import (
    LineageAIConfig,
    _anthropic_messages_url,
    _anthropic_text_content,
    _chat_completions_url,
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

# AI 推断的 dml_type 枚举（避免 hallucinated 类型）
_VALID_DML = {"INSERT", "UPDATE", "MERGE", "DELETE", "CTAS", "TRUNCATE_INSERT"}
_VALID_CONFIDENCE = {"low", "medium"}  # high 不允许（AI 推断永远不能是 high）


def infer_from_parse_errors(
    parse_errors: list[dict[str, str]],
    *,
    table_whitelist: set[str],
    column_whitelist: set[str],
    dialect: str,
    config: LineageAIConfig,
    max_fragment_chars: int = 8000,
    max_fragments: int = 10,
) -> dict[str, Any]:
    """对 parse_errors 中每条片段调一次 AI 推断 + 白名单过滤。

    Best-effort：单个片段失败不影响其它；整体 provider 不可用直接返回空（不抛错）。
    `max_fragments` 防御：parse_errors 极多时不至于狂调 API（默认前 10 条）。
    """
    output: dict[str, Any] = {
        "edges": [],
        "warnings": [],
        "trigger_count": 0,
        "filtered_count": 0,
    }
    if not parse_errors:
        return output
    provider_name = (config.provider or "off").lower()
    if provider_name in {"off", "disabled", "none", ""}:
        output["warnings"].append({
            "type": "ai_inference_skipped",
            "message": f"AI provider 未启用（provider={provider_name}），跳过 parse_errors 兜底",
        })
        return output
    if provider_name not in OPENAI_COMPATIBLE_PROVIDERS and provider_name not in ANTHROPIC_COMPATIBLE_PROVIDERS and provider_name != "mock":
        output["warnings"].append({
            "type": "ai_inference_skipped",
            "message": f"provider={provider_name} 不支持 inference 兜底（仅 openai-compatible / anthropic-compatible / mock）",
        })
        return output
    if not table_whitelist:
        # 全脚本都没识别到表名 → AI 推断也只能 hallucinate，跳过
        output["warnings"].append({
            "type": "ai_inference_skipped",
            "message": "脚本未识别到任何表名，AI 推断容易 hallucinate，跳过",
        })
        return output

    # 表名白名单 normalize：lowercase + 去引号
    table_set = {_normalize_name(t) for t in table_whitelist if t}
    column_set = {_normalize_name(c) for c in column_whitelist if c}

    for index, fragment in enumerate(parse_errors[:max_fragments]):
        sql_text = (fragment.get("sql") or "").strip()
        error_text = (fragment.get("error") or "").strip()
        if not sql_text:
            continue
        if len(sql_text) > max_fragment_chars:
            sql_text = sql_text[:max_fragment_chars] + "\n/* truncated */"
        try:
            output["trigger_count"] += 1
            raw = _call_provider(
                provider_name=provider_name,
                config=config,
                sql_text=sql_text,
                error_text=error_text,
                dialect=dialect,
                table_whitelist=sorted(table_set),
                column_whitelist=sorted(column_set)[:200],
                fragment_index=index,
            )
            if not isinstance(raw, dict):
                output["warnings"].append({
                    "type": "ai_inference_invalid",
                    "fragment_index": index,
                    "message": "AI 返回非 dict，丢弃",
                })
                continue
            edges, filtered = _validate_and_filter_edges(
                raw.get("edges") or [],
                table_set=table_set,
                column_set=column_set,
                fragment_index=index,
                evidence_hint=sql_text[:200],
            )
            output["edges"].extend(edges)
            output["filtered_count"] += filtered
            if filtered:
                output["warnings"].append({
                    "type": "ai_inference_filtered",
                    "fragment_index": index,
                    "message": f"AI 返回 {filtered} 条非白名单表名，已过滤",
                })
        except Exception as exc:
            logger.warning("AI inference failed fragment_index=%s error=%s", index, exc)
            output["warnings"].append({
                "type": "ai_inference_error",
                "fragment_index": index,
                "message": str(exc),
            })

    if len(parse_errors) > max_fragments:
        output["warnings"].append({
            "type": "ai_inference_truncated",
            "message": f"parse_errors 共 {len(parse_errors)} 条，仅推断前 {max_fragments} 条（防止 API 滥用）",
        })

    return output


# ─── 动态 SQL 推断（Phase 2） ─────────────────────────────────────────────────

# 哪些 confidence 值需要 AI 兜底：
# - unresolved：变量没找到赋值（参数 / 包变量 / cursor / 外部传入）
# - var_concat 但 confidence='low'：变量拼接但 sqlglot 仍解不通顺
# - string_literal：弱兜底情况（基本不出现，extract_dynamic_sql 已经移除该路径）
# 不送 AI 的：'high'（EXECUTE IMMEDIATE 字面量已含完整 SQL）/ 'prepare_var' 高置信
_DYNAMIC_SQL_AI_TRIGGER_CONFIDENCES = {"unresolved", "low", "string_literal"}


def infer_from_dynamic_sql_segments(
    segments: list[dict[str, Any]],
    *,
    table_whitelist: set[str],
    column_whitelist: set[str],
    dialect: str,
    config: LineageAIConfig,
    procedure_segments: list[dict[str, Any]] | None = None,
    max_fragment_chars: int = 8000,
    max_fragments: int = 10,
) -> dict[str, Any]:
    """对 dynamic_sql_segments 中**低置信 / 未解析**的片段调 AI 推断。

    跟 parse_errors 的差异：
    - parse_errors 是"sqlglot 解析直接抛错"
    - dynamic_sql 是"变量拼接的 SQL，可能能拼出但 confidence 低 / 或拼不出"

    本函数只对 confidence ∈ {unresolved, low, string_literal} 的 segment 调 AI；
    high 和 prepare_var 已经有完整源信息，跳过避免浪费 token。

    procedure_segments 提供过程上下文（procedure_name + preceding_comment）—— 当
    动态 SQL 出现在某个过程体内时，把过程信息塞进 prompt，让 AI 看 "这个过程叫
    什么名字 / 注释写了啥业务含义" 来辅助推断目标表。
    """
    output: dict[str, Any] = {
        "edges": [],
        "warnings": [],
        "trigger_count": 0,
        "filtered_count": 0,
    }
    if not segments:
        return output
    provider_name = (config.provider or "off").lower()
    if provider_name in {"off", "disabled", "none", ""}:
        return output  # parse_errors 路径已加 warning，此处不重复
    if provider_name not in OPENAI_COMPATIBLE_PROVIDERS and provider_name not in ANTHROPIC_COMPATIBLE_PROVIDERS and provider_name != "mock":
        return output
    if not table_whitelist:
        return output

    table_set = {_normalize_name(t) for t in table_whitelist if t}
    column_set = {_normalize_name(c) for c in column_whitelist if c}

    # 触发筛选：只对低置信 / 未解析的 segment 调 AI
    candidates: list[tuple[int, dict[str, Any]]] = []
    for index, segment in enumerate(segments or []):
        confidence = str(segment.get("confidence") or "").lower()
        if confidence not in _DYNAMIC_SQL_AI_TRIGGER_CONFIDENCES:
            continue
        candidates.append((index, segment))

    if not candidates:
        return output

    # 构建过程上下文索引：procedure_name → preceding_comment（用于 enrich prompt）
    proc_context = _build_procedure_context(procedure_segments or [])

    for actual_count, (segment_index, segment) in enumerate(candidates):
        if actual_count >= max_fragments:
            output["warnings"].append({
                "type": "ai_inference_truncated",
                "source_kind": "dynamic_sql",
                "message": f"dynamic_sql 候选 {len(candidates)} 个，仅推断前 {max_fragments} 个（防 API 滥用）",
            })
            break
        sql_text = (segment.get("sql") or "").strip()
        if not sql_text:
            continue
        if len(sql_text) > max_fragment_chars:
            sql_text = sql_text[:max_fragment_chars] + "\n/* truncated */"

        # 试图找到这个 dynamic SQL 关联的过程：sql_text 里如果有 EXECUTE IMMEDIATE p_var
        # 那 p_var 就是变量名，可以在 procedure 里查"该 procedure 名字 / preceding_comment"
        # 简化策略：直接把所有 procedure 上下文整体丢给 AI（max 5 个，截断 comment）
        try:
            output["trigger_count"] += 1
            raw = _call_provider_dynamic(
                provider_name=provider_name,
                config=config,
                sql_text=sql_text,
                source=str(segment.get("source") or ""),
                confidence=str(segment.get("confidence") or ""),
                dialect=dialect,
                table_whitelist=sorted(table_set),
                column_whitelist=sorted(column_set)[:200],
                segment_index=segment_index,
                proc_context=proc_context[:5],
            )
            if not isinstance(raw, dict):
                output["warnings"].append({
                    "type": "ai_inference_invalid",
                    "source_kind": "dynamic_sql",
                    "segment_index": segment_index,
                    "message": "AI 返回非 dict，丢弃",
                })
                continue
            edges, filtered = _validate_and_filter_edges(
                raw.get("edges") or [],
                table_set=table_set,
                column_set=column_set,
                fragment_index=segment_index,
                evidence_hint=sql_text[:200],
                source_kind="dynamic_sql",
            )
            output["edges"].extend(edges)
            output["filtered_count"] += filtered
            if filtered:
                output["warnings"].append({
                    "type": "ai_inference_filtered",
                    "source_kind": "dynamic_sql",
                    "segment_index": segment_index,
                    "message": f"AI 返回 {filtered} 条非白名单表名，已过滤",
                })
        except Exception as exc:
            logger.warning(
                "AI dynamic_sql inference failed segment_index=%s error=%s",
                segment_index,
                exc,
            )
            output["warnings"].append({
                "type": "ai_inference_error",
                "source_kind": "dynamic_sql",
                "segment_index": segment_index,
                "message": str(exc),
            })

    return output


def _build_procedure_context(procedure_segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从 procedure_segments 构建给 AI 的过程上下文（procedure_name + 注释 + 行号）。
    每个 procedure 取第一段（业务标题最有可能在那里），注释截断 200 字符防 token 爆。
    """
    seen_procs: set[str] = set()
    context: list[dict[str, Any]] = []
    for seg in procedure_segments:
        proc_name = (seg.get("procedure_name") or "").strip()
        if not proc_name or proc_name in seen_procs:
            continue
        seen_procs.add(proc_name)
        comment = (seg.get("preceding_comment") or "").strip()
        context.append({
            "procedure_name": proc_name,
            "procedure_kind": seg.get("procedure_kind") or "",
            "preceding_comment": comment[:200],
            "line_start": seg.get("line_start"),
        })
    return context


# ─── Prompt 构建 ──────────────────────────────────────────────────────────────


_SYSTEM_PROMPT = (
    "You are a SQL lineage extractor for a deterministic analyzer's fallback path. "
    "The deterministic parser FAILED on the SQL fragment below; your job is to extract "
    "data lineage edges ONLY using table and column names from the provided whitelists. "
    "Strict rules:\n"
    "- Output ONLY a JSON object with key `edges` (an array).\n"
    "- Each edge must have: source_table, target_table, dml_type (one of "
    "INSERT/UPDATE/MERGE/DELETE/CTAS/TRUNCATE_INSERT), confidence (low|medium), reason "
    "(short Chinese sentence), evidence (literal SQL excerpt under 200 chars).\n"
    "- source_table / target_table MUST appear in the table whitelist.\n"
    "- source_columns / target_columns (optional arrays) MUST be in the column whitelist.\n"
    "- If you cannot identify a table from the whitelist with reasonable confidence, OMIT that edge.\n"
    "- Do NOT invent table or column names.\n"
    "- Do NOT explain outside the JSON.\n"
    "- If the fragment has no clear data flow, return {\"edges\": []}."
)


_DYNAMIC_SYSTEM_PROMPT = (
    "You are a SQL lineage extractor for the deterministic analyzer's dynamic-SQL fallback path. "
    "The fragment is a DYNAMIC SQL stub: usually 'EXECUTE IMMEDIATE p_var' where the variable "
    "could not be statically resolved, or a low-confidence variable concatenation. "
    "Use the procedure context (procedure_name + preceding_comment showing business intent) and "
    "the table/column whitelist to GUESS the most likely target table for the dynamic statement. "
    "Strict rules:\n"
    "- Output ONLY a JSON object with key `edges`.\n"
    "- Each edge: source_table (CAN BE EMPTY when not derivable), target_table (REQUIRED), "
    "dml_type (one of INSERT/UPDATE/MERGE/DELETE/CTAS/TRUNCATE_INSERT), confidence (low|medium, "
    "default low), reason (Chinese, must reference procedure_name or comment if used), "
    "evidence (the variable name or original fragment).\n"
    "- target_table MUST be in the table whitelist; never invent.\n"
    "- If the variable name (like p_t_name) and procedure context don't suggest any table in "
    "the whitelist, return {\"edges\": []}. NEVER guess randomly.\n"
    "- Confidence MUST be low when only variable name is available; medium ONLY when a comment "
    "explicitly mentions a target table that's also in the whitelist.\n"
    "- Do NOT explain outside the JSON."
)


def _call_provider(
    *,
    provider_name: str,
    config: LineageAIConfig,
    sql_text: str,
    error_text: str,
    dialect: str,
    table_whitelist: list[str],
    column_whitelist: list[str],
    fragment_index: int,
) -> dict[str, Any]:
    """Direct HTTP call bypassing provider.enrich()，用本模块自己的 system prompt。

    复用 lineage_ai 的 URL / max_tokens / content extraction 等 helpers，确保跟主路径
    一致的鉴权 / Kimi thinking 关闭 / max_tokens 等行为。
    """
    user_payload = {
        "scope": "parse_error_fallback",
        "fragment_index": fragment_index,
        "dialect": dialect or "",
        "parser_error": error_text,
        "sql": sql_text,
        "table_whitelist": table_whitelist,
        "column_whitelist": column_whitelist,
    }
    user_content = json.dumps(user_payload, ensure_ascii=False)

    if provider_name == "mock":
        # 测试用 mock：永远返回空 edges，不发 HTTP
        return {"edges": []}

    if provider_name in OPENAI_COMPATIBLE_PROVIDERS:
        return _call_openai_compatible(config, user_content)
    if provider_name in ANTHROPIC_COMPATIBLE_PROVIDERS:
        return _call_anthropic(config, user_content)
    raise ValueError(f"unsupported provider for inference: {provider_name}")


def _call_provider_dynamic(
    *,
    provider_name: str,
    config: LineageAIConfig,
    sql_text: str,
    source: str,
    confidence: str,
    dialect: str,
    table_whitelist: list[str],
    column_whitelist: list[str],
    segment_index: int,
    proc_context: list[dict[str, Any]],
) -> dict[str, Any]:
    """跟 _call_provider 类似，但走 _DYNAMIC_SYSTEM_PROMPT；user message 包过程上下文。"""
    user_payload = {
        "scope": "dynamic_sql_fallback",
        "segment_index": segment_index,
        "dialect": dialect or "",
        "segment_source": source,
        "segment_confidence": confidence,
        "sql": sql_text,
        "procedure_context": proc_context,
        "table_whitelist": table_whitelist,
        "column_whitelist": column_whitelist,
    }
    user_content = json.dumps(user_payload, ensure_ascii=False)

    if provider_name == "mock":
        return {"edges": []}
    if provider_name in OPENAI_COMPATIBLE_PROVIDERS:
        return _call_openai_compatible(config, user_content, system_prompt=_DYNAMIC_SYSTEM_PROMPT)
    if provider_name in ANTHROPIC_COMPATIBLE_PROVIDERS:
        return _call_anthropic(config, user_content, system_prompt=_DYNAMIC_SYSTEM_PROMPT)
    raise ValueError(f"unsupported provider for dynamic inference: {provider_name}")


def _call_openai_compatible(config: LineageAIConfig, user_content: str, *, system_prompt: str = "") -> dict[str, Any]:
    if not config.api_key:
        raise ValueError("AI API key is required")
    if not config.model:
        raise ValueError("AI model is required")
    base_url = config.base_url.rstrip("/") or "https://api.openai.com/v1"
    body: dict[str, Any] = {
        "model": config.model,
        "max_tokens": _openai_compatible_max_tokens(config),
        "messages": [
            {"role": "system", "content": system_prompt or _SYSTEM_PROMPT},
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


def _call_anthropic(config: LineageAIConfig, user_content: str, *, system_prompt: str = "") -> dict[str, Any]:
    if not config.api_key:
        raise ValueError("AI API key is required")
    if not config.model:
        raise ValueError("AI model is required")
    base_url = config.base_url.rstrip("/") or "https://api.anthropic.com"
    body = {
        "model": config.model,
        "max_tokens": 4096,
        "system": system_prompt or _SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_content}],
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


# ─── 输出校验 + 白名单过滤 ────────────────────────────────────────────────────


def _validate_and_filter_edges(
    raw_edges: Any,
    *,
    table_set: set[str],
    column_set: set[str],
    fragment_index: int,
    evidence_hint: str,
    source_kind: str = "parse_error",
) -> tuple[list[dict[str, Any]], int]:
    """逐条 edge 校验：dml_type / confidence 枚举 + 表名 / 字段名白名单。"""
    if not isinstance(raw_edges, list):
        return [], 0
    output: list[dict[str, Any]] = []
    filtered = 0
    for raw in raw_edges:
        if not isinstance(raw, dict):
            filtered += 1
            continue
        target_table = _normalize_name(raw.get("target_table") or "")
        source_table = _normalize_name(raw.get("source_table") or "")
        # 至少要有 target（INSERT/UPDATE/MERGE/DELETE 都有 target）
        if not target_table or target_table not in table_set:
            filtered += 1
            continue
        # source_table 可空（DELETE/TRUNCATE 可能没 source；但 INSERT 必须有）
        if source_table and source_table not in table_set:
            # 源不在白名单 → 整条边降级为只保留 target，source 设空
            source_table = ""
        dml_type = str(raw.get("dml_type") or "INSERT").upper()
        if dml_type not in _VALID_DML:
            dml_type = "INSERT"  # 兜底
        confidence = str(raw.get("confidence") or "low").lower()
        if confidence not in _VALID_CONFIDENCE:
            confidence = "low"
        # 字段白名单过滤
        source_columns = _filter_columns(raw.get("source_columns"), column_set)
        target_columns = _filter_columns(raw.get("target_columns"), column_set)
        # evidence 必须有 —— AI 没给的话用片段开头兜底（防止前端展示空）
        evidence = str(raw.get("evidence") or "").strip()[:300] or evidence_hint
        reason = str(raw.get("reason") or "AI 推断（无附加说明）").strip()[:200]
        output.append({
            "source_table": source_table,
            "target_table": target_table,
            "dml_type": dml_type,
            "source_columns": source_columns,
            "target_columns": target_columns,
            "confidence": confidence,
            "reason": reason,
            "evidence": evidence,
            "fragment_index": fragment_index,
            "source_kind": source_kind,  # parse_error | dynamic_sql
            "is_ai_inferred": True,
        })
    return output, filtered


def _filter_columns(raw: Any, whitelist: set[str]) -> list[str]:
    if not isinstance(raw, list) or not whitelist:
        return []
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        # 字段可能是 "t.col" 形式，剥到 col 或保留全名都查白名单
        normalized = _normalize_name(item)
        bare = normalized.split(".")[-1] if "." in normalized else normalized
        if normalized in whitelist or bare in whitelist:
            out.append(item.strip())
    # 去重保序
    seen: set[str] = set()
    unique: list[str] = []
    for item in out:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


_NAME_NORMALIZE_RE = re.compile(r'["`\[\]]')


def _normalize_name(name: str) -> str:
    """表 / 字段名归一化：去引号 + lowercase。`"ODS"."T1"` → ods.t1"""
    return _NAME_NORMALIZE_RE.sub("", str(name or "")).strip().lower()
