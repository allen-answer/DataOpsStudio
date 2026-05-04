"""app.ai.prompts —— AI system prompt 字符串集中管理。

Phase 9 Day 4 阶段 1：建包 + re-export 给新 import path 用。原始字符串仍
散落在 `lineage_ai.py` / `lineage_ai_inference.py`，本目录文件先做 thin
re-export，后续 sprint 再正式迁过来。

文件分组（按调用场景）：
- `enrichment.py` —— `_ENRICHMENT_SYSTEM_PROMPT`（普通血缘增强）
- `inference.py` —— `_SYSTEM_PROMPT`（parse_errors 兜底推断）
- `dynamic_sql.py` —— `_DYNAMIC_SYSTEM_PROMPT`（动态 SQL 兜底）
- `column_attr.py` —— `_COLUMN_ATTRIBUTION_SYSTEM_PROMPT`（字段归属推荐）
- `error_translate.py` —— 错误翻译 prompt（5xx / 长 4xx → 中文 + 排查建议）
- `column_mapping.py` —— compare workbench 字段映射推荐 prompt
"""
from __future__ import annotations

from app.ai.prompts.enrichment import ENRICHMENT_SYSTEM_PROMPT
from app.ai.prompts.inference import INFERENCE_SYSTEM_PROMPT
from app.ai.prompts.dynamic_sql import DYNAMIC_SQL_SYSTEM_PROMPT
from app.ai.prompts.column_attr import COLUMN_ATTRIBUTION_SYSTEM_PROMPT

__all__ = [
    "ENRICHMENT_SYSTEM_PROMPT",
    "INFERENCE_SYSTEM_PROMPT",
    "DYNAMIC_SQL_SYSTEM_PROMPT",
    "COLUMN_ATTRIBUTION_SYSTEM_PROMPT",
]
