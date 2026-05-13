"""Scenario SQL templating —— `{{name}}` 变量替换（Phase 12 切片 15）。

让 `workload.sql` 像「{{cutoff_date}}」这种占位符在 lineage_script analyzer /
slow_query analyze 之前替换成 `scenario.variables[cutoff_date]` 的真实值。

- 仅替换 `{{ name }}` 形态，name 须为 `[a-zA-Z_][a-zA-Z0-9_]*`，避免误碰 Jinja
  之类的复杂语法（也避免 SQL 里偶然出现的双花括号被破坏）
- 标量值（str/int/float/bool）按 str() 直接代入；不做 quote / SQL escape
  （数据类型保持调用方意图）
- 缺失变量保留原样 `{{name}}` 不抛错（向后兼容老 scenario）+ 收集 missing 列表
  让 caller 决定是否警告

返回 `RenderedSql(text, substituted, missing)` 让 caller 既能拿到结果又能看到
哪些变量真实生效 / 哪些没找到对应值。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_PATTERN = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


@dataclass
class RenderedSql:
    text: str
    substituted: list[str] = field(default_factory=list)  # 命中并替换的 var
    missing: list[str] = field(default_factory=list)      # SQL 引用了但 variables 没有


def render_template(sql: str, variables: dict[str, Any] | None) -> RenderedSql:
    """渲染 SQL 模板。variables 为 None / 空 → 不动 SQL 原样返。"""
    if not sql:
        return RenderedSql(text=sql or "")
    if not variables:
        # 仍然扫一遍看 SQL 是否有 `{{var}}` 形态 → 计入 missing 让 caller 警告
        missing = sorted({m.group(1) for m in _PATTERN.finditer(sql)})
        return RenderedSql(text=sql, missing=missing)

    substituted: set[str] = set()
    missing: set[str] = set()

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in variables:
            substituted.add(name)
            return _coerce(variables[name])
        missing.add(name)
        return match.group(0)  # 原样保留

    text = _PATTERN.sub(_replace, sql)
    return RenderedSql(
        text=text,
        substituted=sorted(substituted),
        missing=sorted(missing),
    )


def _coerce(value: Any) -> str:
    """标量 → str。布尔小写 true/false（SQL 风格），None 渲染空串。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, str)):
        return str(value)
    # dict / list 等非标量 —— 还是 str() 兜底，避免静默丢失（caller 一般不该传）
    return str(value)
