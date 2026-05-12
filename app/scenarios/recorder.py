"""Scenario recorder —— 把 scenario.workloads 翻译成平台实体（Phase 12）。

支持的 workload kinds：
- compare_task    → `CompareTaskCreate` 入 task_store（切片 4）
- lineage_script  → 跑 analyzer + 写 history JSON（切片 12，type=lineage）

slow_query 不在 recorder 范围 —— admin 在 sandbox 视图按 `/api/slow-sql/analyze`
+ `/enrich` 即时分析，没必要持久化（每次跑环境可能不同）。

`record_scenario(...)` 一次性处理所有支持的 workload kinds，返回组合 report。
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.models import CompareTask, CompareTaskCreate
from app.models.common import SourceKind, SqlMode
from app.models.compare import CompareRules
from app.scenarios.materializer import effective_columns, quote_identifier, quote_qualified
from app.scenarios.models import Scenario, TableDef, WorkloadDef
from app.services.repositories import task_store


logger = logging.getLogger(__name__)


@dataclass
class RecordWarning:
    workload_name: str
    reason: str


@dataclass
class RecordResult:
    """build_compare_tasks 返回的「计划 + 警告」（纯函数）。"""
    payloads: list[CompareTaskCreate]
    warnings: list[RecordWarning]


def build_compare_tasks(
    scenario: Scenario,
    datasource_id: str,
    project_id: str = "",
) -> RecordResult:
    """从 scenario.workloads 里的 compare_task 项构造 CompareTaskCreate。

    不做持久化。返回 (payloads, warnings)：
    - payload 是 CompareTaskCreate，可直接喂 task_store.create
    - warning 描述哪些 workload 被跳过（缺 source/target/keys 等）
    """
    if not datasource_id.strip():
        raise ValueError("datasource_id is required")
    all_tables = {t.name: t for t in scenario.tables}
    payloads: list[CompareTaskCreate] = []
    warnings: list[RecordWarning] = []

    for wl in scenario.workloads:
        if wl.kind != "compare_task":
            continue
        extra = wl.model_extra or {}
        source = (extra.get("source") or "").strip()
        target = (extra.get("target") or "").strip()
        keys = list(extra.get("keys") or [])
        if not source or not target:
            warnings.append(RecordWarning(wl.name or "<anonymous>", "missing source/target"))
            continue
        if not keys:
            warnings.append(RecordWarning(wl.name or "<anonymous>", "missing keys"))
            continue
        if source not in all_tables:
            warnings.append(RecordWarning(wl.name or "<anonymous>", f"source table '{source}' not in scenario"))
            continue
        if target not in all_tables:
            warnings.append(RecordWarning(wl.name or "<anonymous>", f"target table '{target}' not in scenario"))
            continue

        source_sql = _build_select(all_tables[source], source, keys, all_tables)
        target_sql = _build_select(all_tables[target], target, keys, all_tables)

        display_name = wl.name or f"{source} vs {target}"
        payload = CompareTaskCreate(
            name=f"{scenario.id} · {display_name}",
            source_kind=SourceKind.SQL,
            target_kind=SourceKind.SQL,
            source_id=datasource_id,
            target_id=datasource_id,
            sql_mode=SqlMode.DOUBLE,
            source_sql=source_sql,
            target_sql=target_sql,
            key_columns=keys,
            rules=CompareRules(),
            project_id=project_id,
        )
        payloads.append(payload)

    return RecordResult(payloads=payloads, warnings=warnings)


def record_scenario(
    scenario: Scenario,
    datasource_id: str,
    project_id: str = "",
) -> dict[str, object]:
    """build_compare_tasks + task_store.create 一气呵成，外加 lineage_script
    workload 跑 analyzer + 落 history。

    返回：
        {"tasks": [CompareTask], "warnings": [{workload_name, reason}],
         "lineage_runs": [{run_id, workload_name, ok, error?}]}
    """
    plan = build_compare_tasks(scenario, datasource_id, project_id=project_id)
    created: list[CompareTask] = []
    warnings: list[dict[str, str]] = [
        {"workload_name": w.workload_name, "reason": w.reason} for w in plan.warnings
    ]
    for payload in plan.payloads:
        try:
            created.append(task_store.create(payload))
        except Exception as exc:  # noqa: BLE001 — broad for any persist failure
            warnings.append({
                "workload_name": payload.name,
                "reason": f"persist failed: {exc}",
            })

    lineage_runs = _record_lineage_scripts(scenario)
    return {"tasks": created, "warnings": warnings, "lineage_runs": lineage_runs}


# ─── lineage_script 切片 12 ────────────────────────────────────────────────


def _record_lineage_scripts(scenario: Scenario) -> list[dict[str, Any]]:
    """跑 analyzer 对每个 lineage_script workload 的 SQL，写 history JSON。

    History 文件被 list_result_history 按 `table_edges` 字段自动分类为 type=lineage，
    前端 HistoryView 可直接展示 + 跳详情。
    """
    out: list[dict[str, Any]] = []
    lineage_workloads = [w for w in scenario.workloads if w.kind == "lineage_script"]
    if not lineage_workloads:
        return out

    # lazy import 避免顶层依赖 analyzer（其引 sqlglot，启动慢）
    from app.lineage.analyzer import analyze_sql_lineage
    from app.utils.paths import RESULTS_DIR

    for wl in lineage_workloads:
        extra = wl.model_extra or {}
        sql = (extra.get("sql") or "").strip()
        wl_name = (wl.name or "").strip() or "<anonymous>"
        if not sql:
            out.append({
                "workload_name": wl_name, "ok": False, "run_id": "",
                "error": "lineage_script workload missing sql",
            })
            continue
        run_id = _generate_lineage_run_id()
        try:
            started = time.perf_counter()
            result = analyze_sql_lineage(sql, scenario.dialect)
            elapsed = round(time.perf_counter() - started, 3)
            payload = _envelope_lineage_history(
                run_id=run_id,
                task_name=f"{scenario.id} · {wl_name}",
                sql=sql,
                analyzer_result=result,
                elapsed=elapsed,
                dialect=scenario.dialect,
            )
            (RESULTS_DIR / f"{run_id}.json").write_text(
                json.dumps(payload, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            out.append({
                "workload_name": wl_name, "ok": True, "run_id": run_id,
            })
        except Exception as exc:
            logger.warning("recorder lineage_script failed wl=%s: %s", wl_name, exc)
            out.append({
                "workload_name": wl_name, "ok": False, "run_id": "",
                "error": str(exc),
            })
    return out


def _generate_lineage_run_id() -> str:
    return f"lineage_script_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"


def _envelope_lineage_history(
    *,
    run_id: str,
    task_name: str,
    sql: str,
    analyzer_result: dict[str, Any],
    elapsed: float,
    dialect: str,
) -> dict[str, Any]:
    """Wrap analyzer 输出成 history JSON 形态（list_result_history / HistoryView 期望的 shape）。

    `table_edges` 字段一定保留 —— `_classify_result` 靠它判 type=lineage。
    `sql` / `dialect` 入信息字段，前端详情页能复现。
    """
    payload = {
        "run_id": run_id,
        "task_id": "",  # scenario lineage 没绑 CompareTask
        "task_name": task_name,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "elapsed_seconds": elapsed,
        "source_rows": 0,
        "target_rows": 0,
        "summary": {},
        "type": "lineage",
        "sql": sql,
        "dialect": dialect,
    }
    # analyzer 输出整体合并进 history JSON（含 table_edges / column_edges /
    # graph_groups / target_summary / parse_errors / 等）
    if isinstance(analyzer_result, dict):
        for k, v in analyzer_result.items():
            if k not in payload:  # 不覆盖 history 元数据
                payload[k] = v
    # 安全网：确保 table_edges 字段存在（即使 analyzer 没产出），让 classifier 落 lineage
    payload.setdefault("table_edges", [])
    return payload


# ─── helpers ────────────────────────────────────────────────────────────────


def _build_select(
    table: TableDef,
    table_name: str,
    keys: list[str],
    all_tables: dict[str, TableDef],
) -> str:
    """生成 `SELECT col1, col2 FROM <quoted_table> ORDER BY <quoted_pk>`。"""
    cols = effective_columns(table, all_tables)
    col_list = ", ".join(quote_identifier(c.name) for c in cols) if cols else "*"
    qname = quote_qualified(table_name)
    order_by = ", ".join(quote_identifier(k) for k in keys)
    return f"SELECT {col_list} FROM {qname} ORDER BY {order_by}"
