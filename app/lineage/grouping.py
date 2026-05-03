"""按规则文件把脚本里的表归到业务分组。

Phase 7 Track B 第 5 项。规则文件 `config/lineage_group_rules.yml`（或同目录
`.json`），每个分组挂多条 match 规则，命中任一即归组。一张表可同时归多组。

不存在规则文件时静默返回空列表——`semantic_lineage.business_groups` 仍然为空，
不影响其他模块。

支持的 matcher 字段（值为字符串，匹配大小写不敏感）：
  • schema_prefix / schema_exact / schema_contains
  • basename_prefix / basename_suffix / basename_exact / basename_contains
  • basename_regex（Python re，不强制 ^$）
  • title_keyword（匹配 target_summary[*].titles 任一条）

`schema` 指 `schema.tablename` 左段；无 schema 的表 schema_* 规则不生效。
Oracle DB Link `tab@dblink` 在拆 schema/basename 时会先剥掉 `@dblink`。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.lineage._common import normalize_table_name as _normalize_table_name


_RE_DBLINK = re.compile(r"@[\w$#.]+$")


@dataclass
class _Matcher:
    kind: str
    value: str
    regex: re.Pattern[str] | None = None


@dataclass
class GroupRule:
    name: str
    description: str = ""
    matchers: list[_Matcher] = field(default_factory=list)


_SUPPORTED_MATCHERS = frozenset({
    "schema_prefix", "schema_exact", "schema_contains",
    "basename_prefix", "basename_suffix", "basename_exact", "basename_contains",
    "basename_regex", "title_keyword",
})


def load_group_rules(yaml_path: Path | None = None, json_path: Path | None = None) -> list[GroupRule]:
    """加载规则文件，优先 yaml，回退 json。两个都不存在返回 []。

    解析失败抛 ValueError —— 不静默吞，否则用户改坏规则会一直没反馈。
    """
    if yaml_path is not None and yaml_path.exists():
        return _parse_rules(_load_yaml(yaml_path), str(yaml_path))
    if json_path is not None and json_path.exists():
        return _parse_rules(_load_json(json_path), str(json_path))
    return []


def _load_yaml(path: Path) -> Any:
    try:
        import yaml  # type: ignore
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            f"无法解析 {path}：未安装 pyyaml。pip install pyyaml 或改用 lineage_group_rules.json。"
        ) from exc
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _parse_rules(payload: Any, source: str) -> list[GroupRule]:
    if payload is None:
        return []
    if not isinstance(payload, dict):
        raise ValueError(f"{source}: 顶层应是 mapping，实际 {type(payload).__name__}")
    raw_groups = payload.get("groups")
    if raw_groups is None:
        return []
    if not isinstance(raw_groups, list):
        raise ValueError(f"{source}: groups 应是 list")
    rules: list[GroupRule] = []
    for index, raw in enumerate(raw_groups):
        rules.append(_parse_one_rule(raw, source, index))
    return rules


def _parse_one_rule(raw: Any, source: str, index: int) -> GroupRule:
    if not isinstance(raw, dict):
        raise ValueError(f"{source}: groups[{index}] 应是 mapping")
    name = (raw.get("name") or "").strip()
    if not name:
        raise ValueError(f"{source}: groups[{index}].name 不能为空")
    description = str(raw.get("description") or "").strip()
    raw_match = raw.get("match")
    if not isinstance(raw_match, list) or not raw_match:
        raise ValueError(f"{source}: groups[{index}].match 至少需要一条规则")
    matchers: list[_Matcher] = []
    for matcher_index, matcher_payload in enumerate(raw_match):
        matchers.append(_parse_matcher(matcher_payload, source, index, matcher_index))
    return GroupRule(name=name, description=description, matchers=matchers)


def _parse_matcher(payload: Any, source: str, group_index: int, matcher_index: int) -> _Matcher:
    if not isinstance(payload, dict) or len(payload) != 1:
        raise ValueError(
            f"{source}: groups[{group_index}].match[{matcher_index}] 应是只含一个 key 的 mapping"
        )
    [(kind, value)] = payload.items()
    if kind not in _SUPPORTED_MATCHERS:
        raise ValueError(
            f"{source}: groups[{group_index}].match[{matcher_index}] 未知 matcher {kind!r}，"
            f"支持的有：{sorted(_SUPPORTED_MATCHERS)}"
        )
    if not isinstance(value, str) or not value:
        raise ValueError(
            f"{source}: groups[{group_index}].match[{matcher_index}].{kind} 必须是非空字符串"
        )
    regex: re.Pattern[str] | None = None
    if kind == "basename_regex":
        try:
            regex = re.compile(value, re.IGNORECASE)
        except re.error as exc:
            raise ValueError(
                f"{source}: groups[{group_index}].match[{matcher_index}].basename_regex 编译失败：{exc}"
            ) from exc
    return _Matcher(kind=kind, value=value.lower(), regex=regex)


def apply_group_rules(
    rules: list[GroupRule],
    tables: list[dict[str, Any]],
    target_summary: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """按规则给每张表打分组标签，输出 (business_groups, grouped_edges)。

    `business_groups`：分组明细 + 包含表 + 写入目标计数。
    `grouped_edges`：跨分组依赖（source_group → target_group），同组内边不计入。
                     有 source/target 任一不在任何分组的边也不计入。
    """
    if not rules:
        return [], []

    targets_by_norm = _index_targets_by_name(target_summary)
    table_to_groups: dict[str, set[str]] = {}
    table_display: dict[str, str] = {}

    for table in tables:
        name = (table.get("table") or "").strip()
        if not name:
            continue
        norm = _normalize_table_name(name)
        table_display.setdefault(norm, name)
        table_to_groups.setdefault(norm, set())

    for summary in target_summary:
        name = (summary.get("target_table") or "").strip()
        if not name:
            continue
        norm = _normalize_table_name(name)
        table_display.setdefault(norm, name)
        table_to_groups.setdefault(norm, set())

    for norm, _display in table_display.items():
        titles = targets_by_norm.get(norm, [])
        for rule in rules:
            if _table_matches_rule(_display_name := table_display[norm], titles, rule):
                table_to_groups[norm].add(rule.name)

    rule_order = {rule.name: i for i, rule in enumerate(rules)}
    rule_descriptions = {rule.name: rule.description for rule in rules}
    write_targets = {_normalize_table_name(s.get("target_table") or "") for s in target_summary}

    groups_payload: dict[str, dict[str, Any]] = {}
    for norm, group_names in table_to_groups.items():
        for group_name in group_names:
            entry = groups_payload.setdefault(group_name, {
                "name": group_name,
                "description": rule_descriptions.get(group_name, ""),
                "tables": [],
                "_table_keys": set(),
                "table_count": 0,
                "target_count": 0,
            })
            if norm in entry["_table_keys"]:
                continue
            entry["_table_keys"].add(norm)
            entry["tables"].append(table_display[norm])
            entry["table_count"] += 1
            if norm in write_targets:
                entry["target_count"] += 1

    business_groups: list[dict[str, Any]] = []
    for entry in sorted(groups_payload.values(), key=lambda e: rule_order.get(e["name"], 1_000_000)):
        entry.pop("_table_keys", None)
        entry["tables"].sort()
        business_groups.append(entry)

    grouped_edges = _aggregate_grouped_edges(edges, table_to_groups, rule_order)
    return business_groups, grouped_edges


def _index_targets_by_name(target_summary: list[dict[str, Any]]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for summary in target_summary:
        name = summary.get("target_table") or ""
        if not name:
            continue
        out[_normalize_table_name(name)] = list(summary.get("titles") or [])
    return out


def _table_matches_rule(table: str, titles: list[str], rule: GroupRule) -> bool:
    schema, basename = _split_schema_basename(table)
    schema_lc = schema.lower()
    basename_lc = basename.lower()
    titles_lc = [t.lower() for t in titles]
    for matcher in rule.matchers:
        if _matcher_hits(matcher, schema_lc, basename_lc, titles_lc):
            return True
    return False


def _matcher_hits(matcher: _Matcher, schema_lc: str, basename_lc: str, titles_lc: list[str]) -> bool:
    if matcher.kind == "schema_prefix":
        return bool(schema_lc) and schema_lc.startswith(matcher.value)
    if matcher.kind == "schema_exact":
        return schema_lc == matcher.value
    if matcher.kind == "schema_contains":
        return bool(schema_lc) and matcher.value in schema_lc
    if matcher.kind == "basename_prefix":
        return basename_lc.startswith(matcher.value)
    if matcher.kind == "basename_suffix":
        return basename_lc.endswith(matcher.value)
    if matcher.kind == "basename_exact":
        return basename_lc == matcher.value
    if matcher.kind == "basename_contains":
        return matcher.value in basename_lc
    if matcher.kind == "basename_regex":
        return matcher.regex is not None and matcher.regex.search(basename_lc) is not None
    if matcher.kind == "title_keyword":
        return any(matcher.value in title for title in titles_lc)
    return False


def _split_schema_basename(table: str) -> tuple[str, str]:
    name = _RE_DBLINK.sub("", table.strip())
    if "." in name:
        schema, _, basename = name.rpartition(".")
        return schema.strip().strip('"`[]'), basename.strip().strip('"`[]')
    return "", name.strip().strip('"`[]')


def _aggregate_grouped_edges(
    edges: list[dict[str, Any]],
    table_to_groups: dict[str, set[str]],
    rule_order: dict[str, int],
) -> list[dict[str, Any]]:
    """edges 按 (source_group, target_group) 聚合。同组内 / 任一端未分组的边跳过。"""
    aggregated: dict[tuple[str, str], dict[str, Any]] = {}
    for edge in edges:
        src = (edge.get("source_table") or "").strip()
        tgt = (edge.get("target_table") or "").strip()
        if not src or not tgt:
            continue
        src_groups = table_to_groups.get(_normalize_table_name(src), set())
        tgt_groups = table_to_groups.get(_normalize_table_name(tgt), set())
        if not src_groups or not tgt_groups:
            continue
        for sg in src_groups:
            for tg in tgt_groups:
                if sg == tg:
                    continue
                key = (sg, tg)
                bucket = aggregated.setdefault(key, {
                    "source_group": sg,
                    "target_group": tg,
                    "edge_count": 0,
                    "_pair_keys": set(),
                    "table_pairs": [],
                })
                bucket["edge_count"] += 1
                pair_key = (_normalize_table_name(src), _normalize_table_name(tgt))
                if pair_key not in bucket["_pair_keys"]:
                    bucket["_pair_keys"].add(pair_key)
                    bucket["table_pairs"].append({"source_table": src, "target_table": tgt})

    out: list[dict[str, Any]] = []
    for bucket in sorted(
        aggregated.values(),
        key=lambda b: (rule_order.get(b["source_group"], 1_000_000), rule_order.get(b["target_group"], 1_000_000)),
    ):
        bucket.pop("_pair_keys", None)
        bucket["table_pairs"].sort(key=lambda p: (p["source_table"], p["target_table"]))
        out.append(bucket)
    return out
