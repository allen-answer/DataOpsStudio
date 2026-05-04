"""血缘增强 prompt —— 给规则解析结果加 summary / suggestions / risks / column_hints。"""
from __future__ import annotations

from app.services.lineage_ai import _ENRICHMENT_SYSTEM_PROMPT as ENRICHMENT_SYSTEM_PROMPT

__all__ = ["ENRICHMENT_SYSTEM_PROMPT"]
