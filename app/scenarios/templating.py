"""Scenario SQL templating —— `{{name}}` 变量替换 + `{% if/endif %}` 条件分支。

让 `workload.sql` 像「{{cutoff_date}}」这种占位符在 lineage_script analyzer /
slow_query analyze 之前替换成 `scenario.variables[cutoff_date]` 的真实值。

**变量**(切片 15 已落):仅替换 `{{ name }}` 形态,name 须为
`[a-zA-Z_][a-zA-Z0-9_]*`,避免误碰 Jinja 之类的复杂语法。标量值按 str() 直接
代入,不做 quote / SQL escape。缺失变量保留原样不抛错。

**条件分支**(Phase 14 追加):支持 `{% if var %}...{% endif %}` 简单条件:
- 变量真值判断:`""` / `0` / `False` / `None` / 未定义 都为 false,其它为 true
- 不支持嵌套(嵌套语义复杂,YAGNI 真出现再加完整 Jinja)
- 不支持 else / elif / 比较运算符 / and / or —— 真要复杂逻辑就改 scenario 配置

返回 `RenderedSql(text, substituted, missing)` 让 caller 既能拿到结果又能看到
哪些变量真实生效 / 哪些没找到对应值。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_PATTERN = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")
# {% if name %} ... {% endif %} —— DOTALL 让 . 跨行,非贪婪 ? 匹配最近 endif
_IF_PATTERN = re.compile(
    r"\{\%\s*if\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\%\}(.*?)\{\%\s*endif\s*\%\}",
    re.DOTALL,
)


@dataclass
class RenderedSql:
    text: str
    substituted: list[str] = field(default_factory=list)  # 命中并替换的 var
    missing: list[str] = field(default_factory=list)      # SQL 引用了但 variables 没有
    conditions_evaluated: list[str] = field(default_factory=list)  # if-block 用到的 var


def render_template(sql: str, variables: dict[str, Any] | None) -> RenderedSql:
    """渲染 SQL 模板。variables 为 None / 空 → 不动 SQL 原样返。"""
    if not sql:
        return RenderedSql(text=sql or "")

    vars_dict: dict[str, Any] = dict(variables or {})
    conditions: set[str] = set()
    missing: set[str] = set()

    # 先处理 {% if %} 块(避免里头的 {{var}} 在 var-replacement 阶段被替换后
    # 又因为 if 求假被整段删,顺序很关键)
    def _condition(match: re.Match[str]) -> str:
        name = match.group(1)
        body = match.group(2)
        conditions.add(name)
        if name not in vars_dict:
            missing.add(name)
            return ""  # 未定义视为 false → 整段删
        if _truthy(vars_dict[name]):
            return body
        return ""

    text = _IF_PATTERN.sub(_condition, sql)

    if not vars_dict:
        # 后续 var-replacement 没东西替,只扫剩下的 missing
        missing.update(m.group(1) for m in _PATTERN.finditer(text))
        return RenderedSql(text=text, missing=sorted(missing), conditions_evaluated=sorted(conditions))

    substituted: set[str] = set()

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in vars_dict:
            substituted.add(name)
            return _coerce(vars_dict[name])
        missing.add(name)
        return match.group(0)  # 原样保留

    text = _PATTERN.sub(_replace, text)
    return RenderedSql(
        text=text,
        substituted=sorted(substituted),
        missing=sorted(missing),
        conditions_evaluated=sorted(conditions),
    )


def _truthy(value: Any) -> bool:
    """SQL 模板 if 判断:`""` / `0` / `False` / `None` 算 false,其它 true。
    跟 Python truthy 语义一致 —— 让 yml 里 `flag: false` / `flag: 0` 直观工作。"""
    return bool(value)


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
