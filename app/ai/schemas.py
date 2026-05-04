"""app.ai.schemas —— re-export `app.models.lineage` 里的 AI schema。

让 AI 包内部代码从这里 import schema，避免到处写 `from app.models.lineage import ...`。
schema 单一定义来源仍是 `app.models.lineage`（不重复定义）。
"""
from __future__ import annotations

from app.models.lineage import (
    AIColumnHint,
    AIConfidence,
    AIInferenceDmlType,
    AIInferenceResult,
    AIInferenceSourceKind,
    AIInferredEdge,
)

__all__ = [
    "AIColumnHint",
    "AIConfidence",
    "AIInferenceDmlType",
    "AIInferenceResult",
    "AIInferenceSourceKind",
    "AIInferredEdge",
]
