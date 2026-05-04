"""字段归属推荐 prompt —— 多 source table JOIN/UNION 中的 unqualified column → suggested_table。"""
from __future__ import annotations

from app.services.lineage_ai_inference import (
    _COLUMN_ATTRIBUTION_SYSTEM_PROMPT as COLUMN_ATTRIBUTION_SYSTEM_PROMPT,
)

__all__ = ["COLUMN_ATTRIBUTION_SYSTEM_PROMPT"]
