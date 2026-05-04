"""动态 SQL 兜底 prompt —— EXECUTE IMMEDIATE / 变量拼接交给 AI 提取 source/target 表。"""
from __future__ import annotations

from app.services.lineage_ai_inference import _DYNAMIC_SYSTEM_PROMPT as DYNAMIC_SQL_SYSTEM_PROMPT

__all__ = ["DYNAMIC_SQL_SYSTEM_PROMPT"]
