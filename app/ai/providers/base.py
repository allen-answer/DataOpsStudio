"""app.ai.providers.base —— Provider Protocol + 配置 dataclass。

Phase 9 Day 4 阶段 1：先 re-export 给新 import path 用。原始定义仍在
`app.services.lineage_ai`（避免一次性大移动）；后续 sprint 再把定义挪过来，
反向把 `lineage_ai.py` 改成 shim re-export。
"""
from __future__ import annotations

from app.services.lineage_ai import LineageAIConfig, LineageAIProvider

__all__ = ["LineageAIConfig", "LineageAIProvider"]
