"""Phase 14 P1-2: slow-sql plan history + diff。

每次 `/api/slow-sql/analyze` 跑完自动落一条 plan 到 SQLite,前端 plan-diff
组件拉同 sql_hash(同语义 SQL)的最近 N 条对比 —— 看「改 SQL / 加索引」前后
plan 的真实变化(type 从 ALL → range,rows 估算从 1M → 100,filesort 消了)。

sql_hash 规则:**归一化 SQL 后 sha256** —— 把空白 / 缩进 / 尾分号去掉,大小写
不动(SQL 关键字大小写不同但 column 名大小写可能有意义)。**目的**是让格式
调整(换行 / 缩进)归同一条历史线,真改语义(改 column / where 条件)走新历史线。

API:
- `save_plan(...)` —— analyze_sql 调用方在记录前调
- `list_plans_for_sql(datasource_id, sql_hash, limit)` —— 拉历史
- `list_plans_for_scenario(scenario_id, workload_name, limit)` —— scenario workload 维度
- `get_plan(plan_id)` —— 拿单条
- `diff_plans(plan_a, plan_b)` —— 算结构化 diff
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from app.services import sqlite_store


logger = logging.getLogger(__name__)


def normalize_sql_for_hash(sql: str) -> str:
    """归一化 SQL 用于 hash:折叠空白 / 去尾分号。保留大小写。"""
    if not sql:
        return ""
    # 折叠所有连续空白(含换行)成单空格
    s = re.sub(r"\s+", " ", sql).strip()
    # 去尾分号 + 周围空白
    s = s.rstrip(";").rstrip()
    return s


def sql_hash(sql: str) -> str:
    """sha256(归一化 SQL),返 hex string。"""
    return hashlib.sha256(normalize_sql_for_hash(sql).encode("utf-8")).hexdigest()


def save_plan(
    *,
    datasource_id: str,
    dialect: str,
    sql_text: str,
    plan: list[dict[str, Any]],
    issues: list[dict[str, Any]],
    suggestions: list[dict[str, Any]],
    scenario_id: str = "",
    workload_name: str = "",
) -> int:
    """落一条 plan history,返新插入 id。失败 raise(让 caller 决定吞)。"""
    h = sql_hash(sql_text)
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with sqlite_store.connect() as conn:
        cur = conn.execute(
            "INSERT INTO slow_sql_plans "
            "(ts, datasource_id, dialect, sql_text, sql_hash, scenario_id, "
            " workload_name, plan_json, issues_json, suggestions_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ts, datasource_id, dialect, sql_text, h,
                scenario_id, workload_name,
                json.dumps(plan, ensure_ascii=False, default=str),
                json.dumps(issues, ensure_ascii=False, default=str),
                json.dumps(suggestions, ensure_ascii=False, default=str),
            ),
        )
        return int(cur.lastrowid or 0)


def list_plans_for_sql(
    datasource_id: str, sql_hash_: str, *, limit: int = 10,
) -> list[dict[str, Any]]:
    """按 (datasource, sql_hash) 拉最近 N 条 plan。最新在前。"""
    if not datasource_id or not sql_hash_:
        return []
    with sqlite_store.connect() as conn:
        rows = conn.execute(
            "SELECT id, ts, dialect, sql_text, sql_hash, scenario_id, workload_name, "
            "  plan_json, issues_json, suggestions_json "
            "FROM slow_sql_plans WHERE datasource_id = ? AND sql_hash = ? "
            "ORDER BY ts DESC LIMIT ?",
            (datasource_id, sql_hash_, int(limit)),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def list_plans_for_scenario(
    scenario_id: str, workload_name: str = "", *, limit: int = 20,
) -> list[dict[str, Any]]:
    """按 scenario workload 拉最近 N 条 plan(workload_name 空 = 该 scenario 所有 workload)"""
    if not scenario_id:
        return []
    with sqlite_store.connect() as conn:
        if workload_name:
            rows = conn.execute(
                "SELECT id, ts, dialect, sql_text, sql_hash, scenario_id, workload_name, "
                "  plan_json, issues_json, suggestions_json "
                "FROM slow_sql_plans WHERE scenario_id = ? AND workload_name = ? "
                "ORDER BY ts DESC LIMIT ?",
                (scenario_id, workload_name, int(limit)),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, ts, dialect, sql_text, sql_hash, scenario_id, workload_name, "
                "  plan_json, issues_json, suggestions_json "
                "FROM slow_sql_plans WHERE scenario_id = ? "
                "ORDER BY ts DESC LIMIT ?",
                (scenario_id, int(limit)),
            ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_plan(plan_id: int) -> dict[str, Any] | None:
    with sqlite_store.connect() as conn:
        row = conn.execute(
            "SELECT id, ts, dialect, sql_text, sql_hash, scenario_id, workload_name, "
            "  plan_json, issues_json, suggestions_json, datasource_id "
            "FROM slow_sql_plans WHERE id = ?",
            (int(plan_id),),
        ).fetchone()
    return _row_to_dict(row) if row else None


def diff_plans(
    plan_a: dict[str, Any] | None,
    plan_b: dict[str, Any] | None,
) -> dict[str, Any]:
    """对两次 plan 算结构化差异(b 是新的,a 是老的)。

    返:
    {
      "rows_delta": {a: int, b: int, change: int},     # 最大单步 rows 估算变化
      "type_changes": [{idx, from, to}],               # type 列(MySQL)/operation(Oracle)
      "extra_changes": [{idx, removed: [...], added: [...]}],
      "issues_resolved": [code],                       # a 有 b 没有 = 修复了
      "issues_introduced": [code],                     # a 没 b 有 = 引入了
      "summary": "..."
    }
    """
    a = plan_a or {}
    b = plan_b or {}
    a_plan = a.get("plan") or []
    b_plan = b.get("plan") or []
    a_dialect = a.get("dialect") or "mysql"

    # rows 最大值变化
    rows_max_a = _max_rows(a_plan, a_dialect)
    rows_max_b = _max_rows(b_plan, a_dialect)

    # type 列变化(MySQL)/ operation 变化(Oracle)
    type_field = "operation" if a_dialect.startswith("oracle") else "type"
    type_changes: list[dict[str, Any]] = []
    extra_changes: list[dict[str, Any]] = []
    common = min(len(a_plan), len(b_plan))
    for i in range(common):
        a_type = str(a_plan[i].get(type_field) or "")
        b_type = str(b_plan[i].get(type_field) or "")
        if a_type != b_type:
            type_changes.append({"idx": i, "from": a_type, "to": b_type})
        a_extra = _extra_tokens(a_plan[i])
        b_extra = _extra_tokens(b_plan[i])
        if a_extra != b_extra:
            extra_changes.append({
                "idx": i,
                "removed": sorted(a_extra - b_extra),
                "added": sorted(b_extra - a_extra),
            })

    # issues 对比(按 code 集合)
    a_codes = {i.get("code") for i in (a.get("issues") or []) if i.get("code")}
    b_codes = {i.get("code") for i in (b.get("issues") or []) if i.get("code")}
    issues_resolved = sorted(a_codes - b_codes)
    issues_introduced = sorted(b_codes - a_codes)

    summary_parts = []
    if rows_max_a != rows_max_b:
        if rows_max_b < rows_max_a:
            ratio = (rows_max_a / max(rows_max_b, 1))
            summary_parts.append(f"max-rows 从 {rows_max_a:,} → {rows_max_b:,}({ratio:.1f}× 改善)")
        else:
            summary_parts.append(f"max-rows 从 {rows_max_a:,} → {rows_max_b:,}(变差)")
    if issues_resolved:
        summary_parts.append(f"修了 {len(issues_resolved)} 条:{', '.join(issues_resolved)}")
    if issues_introduced:
        summary_parts.append(f"新增 {len(issues_introduced)} 条:{', '.join(issues_introduced)}")
    if not summary_parts:
        summary_parts.append("plan 无实质变化")

    return {
        "rows_delta": {
            "a": rows_max_a, "b": rows_max_b, "change": rows_max_b - rows_max_a,
        },
        "type_changes": type_changes,
        "extra_changes": extra_changes,
        "issues_resolved": issues_resolved,
        "issues_introduced": issues_introduced,
        "summary": " · ".join(summary_parts),
    }


def _max_rows(plan: list[dict[str, Any]], dialect: str) -> int:
    """从 plan 取 max 单步行数。MySQL 看 'rows' 列;Oracle 看 'cardinality'。"""
    field = "cardinality" if dialect.startswith("oracle") else "rows"
    max_n = 0
    for step in plan:
        v = step.get(field)
        if v is None:
            continue
        try:
            n = int(v)
        except (TypeError, ValueError):
            continue
        if n > max_n:
            max_n = n
    return max_n


def _extra_tokens(step: dict[str, Any]) -> set[str]:
    """MySQL Extra 列拆分成 token 集合,方便 diff(filesort / temporary / index condition 等)"""
    raw = str(step.get("Extra") or step.get("extra") or "")
    if not raw:
        return set()
    return {t.strip() for t in raw.split(";") if t.strip()}


def _row_to_dict(row: Any) -> dict[str, Any]:
    out = dict(row)
    for json_field in ("plan_json", "issues_json", "suggestions_json"):
        if json_field in out:
            try:
                out[json_field.removesuffix("_json")] = json.loads(out.pop(json_field))
            except Exception:
                out[json_field.removesuffix("_json")] = []
    return out
