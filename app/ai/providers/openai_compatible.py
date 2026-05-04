"""app.ai.providers.openai_compatible —— OpenAI / Kimi / DeepSeek / 通用兼容。

实现支持 chat completions API 接口的所有 provider。
"""
from __future__ import annotations

from app.services.lineage_ai import OpenAICompatibleLineageAIProvider

__all__ = ["OpenAICompatibleLineageAIProvider"]
