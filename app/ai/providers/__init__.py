"""app.ai.providers —— AI provider 实现集合。

每个 provider 是一个 thin shim，re-export `app.services.lineage_ai` 里的类
（Day 4 阶段 1：包结构先到位，类定义保留在原处避免大动；Day 4 阶段 2 / 后续
sprint 再把类挪过来）。

Day 4 落地：包路径 `app.ai.providers.{base, mock, openai_compatible, anthropic, ollama}`
都可 import；`base` 给 protocol，其它 4 个给具体实现。
"""
from __future__ import annotations

from app.ai.providers.base import LineageAIProvider, LineageAIConfig
from app.ai.providers.mock import MockLineageAIProvider
from app.ai.providers.openai_compatible import OpenAICompatibleLineageAIProvider
from app.ai.providers.anthropic import AnthropicCompatibleLineageAIProvider
from app.ai.providers.ollama import OllamaLineageAIProvider

__all__ = [
    "LineageAIProvider",
    "LineageAIConfig",
    "MockLineageAIProvider",
    "OpenAICompatibleLineageAIProvider",
    "AnthropicCompatibleLineageAIProvider",
    "OllamaLineageAIProvider",
]
