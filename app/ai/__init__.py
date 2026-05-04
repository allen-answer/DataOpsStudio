"""app.ai —— AI 集成包（Phase 9 Day 4 拆出）。

之前所有 AI 相关代码都堆在 `app/services/lineage_ai*.py` 三个文件，1900 行：
- `lineage_ai.py` —— enrich + provider 类 + payload 构造
- `lineage_ai_inference.py` —— inference 兜底（parse_errors / dynamic_sql / column_attr）
- `lineage_ai_config.py` —— 配置加载

Phase 9 Day 4 按职责重新切分：
- `app.ai.providers/` —— provider 实现（mock / openai_compatible / anthropic / ollama）
- `app.ai.prompts/` —— system / user prompt 字符串
- `app.ai.filters` —— 白名单过滤（`_validate_and_filter_edges` / `_validate_and_filter_column_hints` / `_normalize_name`）
- `app.ai.schemas` —— re-export `app.models.lineage` 里的 AI 相关 schema
- `app.ai.usage_log` —— **新增**：每次调用记 model / tokens / elapsed / status → `logs/ai_usage.jsonl`

老文件 `app.services.lineage_ai*` 保留 thin shim re-export，现有 import path 不破。
"""
from __future__ import annotations
