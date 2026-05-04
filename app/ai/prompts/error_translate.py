"""错误翻译 prompt —— 5xx / 长 4xx 错误 → 中文 + 排查建议。"""
from __future__ import annotations

from app.api.ai_utils import _ERROR_TRANSLATE_PROMPT as ERROR_TRANSLATE_SYSTEM_PROMPT

__all__ = ["ERROR_TRANSLATE_SYSTEM_PROMPT"]
