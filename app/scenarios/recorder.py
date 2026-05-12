"""Scenario recorder —— 把 scenario.workloads 翻译成平台实体（Phase 12 切片 4）。

当前切片只做 `compare_task` workload → `CompareTaskCreate`：
- 从 workload 拿 source/target 表名、keys、name、expected
- 从 scenario.tables 反查表的 effective columns（含 derives_from rename），
  生成显式列名 SELECT（避免 SELECT * 在字段顺序漂移时挂掉）
- 全部 SELECT 加 `ORDER BY <pk>` —— 让 stream_compare / 流式归并可走

lineage_script / slow_query / workflow_run 留下个切片：
- lineage_script：脚本要进 lineage history 还是 workflow 节点 待定
- slow_query：等 `/api/slow-sql/analyze` 端点定下样本载体
- workflow_run：含 nodes 编排，需要先定一种 "scenario → workflow" 翻译协议

`build_compare_tasks(...)` 纯函数返回 list[CompareTaskCreate]；
`record_scenario(...)` 走 task_store.create 持久化，给 API 端点用。
"""
from __future__ import annotations

from dataclasses import dataclass

from app.models import CompareTask, CompareTaskCreate
from app.models.common import SourceKind, SqlMode
from app.models.compare import CompareRules
from app.scenarios.materializer import effective_columns, quote_identifier, quote_qualified
from app.scenarios.models import Scenario, TableDef, WorkloadDef
from app.services.repositories import task_store


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
    """build_compare_tasks + task_store.create 一气呵成。

    返回 {"tasks": [CompareTask], "warnings": [{workload_name, reason}]}。
    持久化失败的 task 不会影响其它 —— 单条 raise 捕获后变 warning。
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
    return {"tasks": created, "warnings": warnings}


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
