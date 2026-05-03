"""按规则给脚本里出现的每张表打 role 标签。

输入：`tables`（read-side）+ `target_summary`（write-side）+ `insert_mappings`
（行级映射，再补一遍 source_tables）三份已有数据，不重新解析 SQL。

角色分两种来源——
  • 结构角色（基于读 / 写关系）：`target` / `intermediate` / `source_fact` /
    `remote_dblink`。
  • 命名角色（基于 schema / basename 的关键词）：`config` / `reference` /
    `dimension` / `filter`。

一张表可同时挂多个 role（例如 `dwd.dim_user_snapshot` 既是 `target` 又是
`dimension`）。`primary_role` 按"对前端展示最有信息量"的优先级挑一个。

不依赖 AI、不依赖外部配置——这是 Phase 7 Track B 第 4 项的最小可工作版本。
后续业务分组规则（YAML）走第 5 项，独立开。
"""
from __future__ import annotations

import re
from typing import Any

from app.lineage._common import normalize_table_name as _normalize_table_name


# ─── 角色定义 ─────────────────────────────────────────────────────────────────

# basename 上的关键词匹配（[._] 或字符串边界做"词"分隔）。
_NAME_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("config", re.compile(
        r"(?:^|_)(?:config|conf|cfg|setting|settings|parameter|parameters|param|params|sys_param|bbq)(?:_|$)",
        re.IGNORECASE,
    )),
    ("reference", re.compile(
        r"(?:^|_)(?:ref|reference|lookup|dict|code|codes|enum|type|types)(?:_|$)",
        re.IGNORECASE,
    )),
    ("dimension", re.compile(
        r"(?:^|_)(?:dim|dimension|dimensions)(?:_|$)",
        re.IGNORECASE,
    )),
    ("filter", re.compile(
        r"(?:^|_)(?:exclude|exclusion|excl|excluded|blacklist|blocklist|exception|exceptions|filter|filters)(?:_|$)",
        re.IGNORECASE,
    )),
]

# schema 段直接落到这些关键词集合里时整段命名空间统一打 role。
_SCHEMA_KEYWORDS: dict[str, str] = {
    "dim": "dimension",
    "dimension": "dimension",
    "dimensions": "dimension",
    "ref": "reference",
    "reference": "reference",
    "lookup": "reference",
    "dict": "reference",
    "code": "reference",
    "codes": "reference",
    "config": "config",
    "conf": "config",
    "cfg": "config",
    "settings": "config",
    "filter": "filter",
    "filters": "filter",
    "exclude": "filter",
    "exclusion": "filter",
    "blacklist": "filter",
}

# Oracle DB Link：`scott.emp@remote_db` 形式。
_RE_DBLINK = re.compile(r"@[\w$#.]+$")

# `primary_role` 选择优先级，从前到后第一个命中的就是 primary。
_PRIMARY_PRIORITY = (
    "remote_dblink",
    "intermediate",
    "target",
    "config",
    "reference",
    "dimension",
    "filter",
    "source_fact",
)


# ─── 主函数 ───────────────────────────────────────────────────────────────────

def identify_table_roles(
    tables: list[dict[str, Any]],
    target_summary: list[dict[str, Any]],
    insert_mappings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """给脚本里所有出现的表打 role 标签。

    返回 list[{"table", "roles": list[str], "primary_role": str}]。表去重以
    `_normalize_table_name` 规范化结果为准（大小写 / quoting 不同被视为同一张）。
    """
    write_set = {
        _normalize_table_name(s["target_table"])
        for s in target_summary
        if s.get("target_table")
    }

    read_set: set[str] = set()
    for table in tables:
        name = table.get("table") or ""
        if name:
            read_set.add(_normalize_table_name(name))
    for mapping in insert_mappings:
        for src in mapping.get("source_tables") or []:
            if src:
                read_set.add(_normalize_table_name(src))

    # 全集：read ∪ write，保留首次出现的 display 形式。
    all_names: dict[str, str] = {}
    for table in tables:
        name = table.get("table") or ""
        if name:
            all_names.setdefault(_normalize_table_name(name), name)
    for summary in target_summary:
        name = summary.get("target_table") or ""
        if name:
            all_names.setdefault(_normalize_table_name(name), name)
    for mapping in insert_mappings:
        for src in mapping.get("source_tables") or []:
            if src:
                all_names.setdefault(_normalize_table_name(src), src)

    result: list[dict[str, Any]] = []
    for normalized, display in sorted(all_names.items()):
        roles = _classify(display, normalized, write_set, read_set)
        result.append({
            "table": display,
            "roles": roles,
            "primary_role": _primary_role(roles),
        })
    return result


def _classify(
    display: str,
    normalized: str,
    write_set: set[str],
    read_set: set[str],
) -> list[str]:
    roles: list[str] = []
    is_target = normalized in write_set
    is_source = normalized in read_set

    # 结构角色优先：target / intermediate 二选一。
    if is_target and is_source:
        roles.append("intermediate")
    elif is_target:
        roles.append("target")

    if _RE_DBLINK.search(display):
        roles.append("remote_dblink")

    schema, basename = _split_schema(display)
    schema_role = _SCHEMA_KEYWORDS.get(schema.lower()) if schema else None
    if schema_role and schema_role not in roles:
        roles.append(schema_role)

    for role, pattern in _NAME_RULES:
        if role in roles:
            continue
        if pattern.search(basename):
            roles.append(role)

    # 兜底：纯读源、没匹配任何命名规则 → source_fact。
    if not is_target and not any(r in roles for r in ("config", "reference", "dimension", "filter")):
        roles.append("source_fact")

    return roles


def _split_schema(display: str) -> tuple[str, str]:
    """把 catalog.schema.table 切成 (schema, table)；catalog 段忽略——schema 关键词
    才有业务含义，catalog 一般是物理库实例。"""
    cleaned = _RE_DBLINK.sub("", display)  # DB Link 后缀不算 basename 的一部分
    parts = [p for p in cleaned.split(".") if p]
    if len(parts) >= 2:
        return parts[-2], parts[-1]
    return "", parts[-1] if parts else ""


def _primary_role(roles: list[str]) -> str:
    for candidate in _PRIMARY_PRIORITY:
        if candidate in roles:
            return candidate
    return roles[0] if roles else "source_fact"
