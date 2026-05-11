"""Scenario YAML 加载 + 校验。

`load_scenario(path)` 读 YAML → Pydantic 校验 → `Scenario` 实例。
`list_scenarios()` 扫 `config/scenarios/*.yml`（默认跳 `*.example.yml`，
没有用户文件时回退到 example 让新部署也能看到）。

不做 generator / materializer 调用 —— 留给 `generator.py` / `materializer.py`。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from app.scenarios.models import Scenario
from app.utils.paths import SCENARIOS_DIR


def load_scenario(path: Path | str) -> Scenario:
    """读取 + 校验一份 scenario YAML。

    抛 `FileNotFoundError` / `yaml.YAMLError` / `ValidationError`，由上层
    （endpoint / CLI）翻译成 HTTP / shell 错误。
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(p)
    data: Any = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"scenario YAML must be a mapping at top level, got {type(data).__name__}: {p}")
    return Scenario.model_validate(data)


def list_scenarios(include_examples: bool = False) -> list[dict[str, Any]]:
    """列出 SCENARIOS_DIR 下可加载的 scenario。

    返回每项 `{id, name, path, tags, error?}` —— error 不 raise，UI 列页
    希望"坏的 scenario 也能看到、点开才报错"，所以兜底返字段。

    没有任何非-example 文件时，默认 example 会被列出来（方便新部署看 demo）。
    `include_examples=True` 强制带 example，给 admin 调试用。
    """
    if not SCENARIOS_DIR.exists():
        return []
    all_yml = sorted(SCENARIOS_DIR.glob("*.yml"))
    examples = [p for p in all_yml if p.name.endswith(".example.yml")]
    user = [p for p in all_yml if not p.name.endswith(".example.yml")]
    paths = user if user or not include_examples else examples
    if not user and not include_examples:
        # 没用户文件 → 把 example 当 fallback 列出来
        paths = examples
    out: list[dict[str, Any]] = []
    for yml in paths:
        entry: dict[str, Any] = {"path": str(yml.name)}
        try:
            s = load_scenario(yml)
            entry.update({"id": s.id, "name": s.name, "tags": s.tags, "dialect": s.dialect})
        except (ValidationError, yaml.YAMLError, ValueError) as e:
            entry.update({"id": yml.stem, "name": yml.name, "tags": [], "error": str(e)})
        out.append(entry)
    return out
