"""parse_errors 兜底推断 prompt —— sqlglot 解析失败片段交给 AI。"""
from __future__ import annotations

from app.services.lineage_ai_inference import _SYSTEM_PROMPT as INFERENCE_SYSTEM_PROMPT

__all__ = ["INFERENCE_SYSTEM_PROMPT"]
