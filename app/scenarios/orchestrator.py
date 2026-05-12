"""Scenario one-shot orchestrator —— 一键链跑完整流程（Phase 12 切片 11）。

把 scenario sandbox 5 步合并成一个调用：
  1. (opt) ai_filler  填 realistic 列业务样本 + 表描述
  2. generator        造内存数据
  3. materializer     DDL + INSERT 到 datasource
  4. recorder         workloads → CompareTask 持久化
  5. runner           每个 task 同步跑 compare
  6. verifier         actual vs yml expected → pass/fail/skipped

返回组合 report，让 CI / admin 一次调用拿完整 pass/fail 信号。
单步失败不中断后续（除非该步骤的输出是下一步必需），错误聚合到对应字段。
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any

from app.scenarios.ai_filler import fill_scenario
from app.scenarios.generator import generate_scenario
from app.scenarios.models import Scenario
from app.scenarios.recorder import record_scenario
from app.scenarios.runtime import ScenarioRuntimeError, materialize_to_datasource
from app.scenarios.verifier import verify_scenario


logger = logging.getLogger(__name__)


@dataclass
class RunResult:
    task_id: str
    task_name: str
    ok: bool
    summary: dict[str, Any] = field(default_factory=dict)
    error: str = ""


@dataclass
class OrchestrationReport:
    scenario_id: str
    ok: bool = True
    ai_fill: dict[str, Any] | None = None
    materialize: dict[str, Any] | None = None
    record: dict[str, Any] = field(default_factory=dict)
    runs: list[RunResult] = field(default_factory=list)
    verify: dict[str, Any] | None = None
    error: str = ""


def run_all(
    scenario: Scenario,
    datasource_id: str,
    *,
    project_id: str = "",
    drop_first: bool = True,
    batch_size: int = 500,
    ai_fill: bool = False,
) -> OrchestrationReport:
    """Run the full scenario pipeline end-to-end.

    任何步骤失败 → 设 report.ok=False + 短路终止（后续步骤跳过）；
    单个 task run 失败不影响其它（每 run 独立 ok 字段）。
    """
    report = OrchestrationReport(scenario_id=scenario.id)

    # ─── 1. AI fill（optional） ─────────────────────────────────────────────
    if ai_fill:
        try:
            scenario, fill_report = fill_scenario(scenario)
            report.ai_fill = {
                "ok": fill_report.ok,
                "calls": fill_report.calls,
                "filled_columns": fill_report.filled_columns,
                "filled_descriptions": fill_report.filled_descriptions,
                "errors": fill_report.errors,
                "skipped_reason": fill_report.skipped_reason,
            }
        except Exception as exc:
            logger.warning("run_all ai_fill failed: %s", exc)
            report.ai_fill = {"ok": False, "errors": [str(exc)],
                              "filled_columns": [], "filled_descriptions": [],
                              "calls": 0, "skipped_reason": ""}
            # 继续走，data 用原 scenario 生成

    # ─── 2-3. generate + materialize ───────────────────────────────────────
    try:
        data = generate_scenario(scenario)
        report.materialize = materialize_to_datasource(
            scenario, data, datasource_id,
            drop_first=drop_first, batch_size=batch_size,
        )
        report.materialize["rows_generated"] = {name: len(rows) for name, rows in data.items()}
    except (ScenarioRuntimeError, ValueError) as exc:
        report.ok = False
        report.error = f"materialize: {exc}"
        return report
    except Exception as exc:  # 防 generator 异常炸
        report.ok = False
        report.error = f"generate or materialize: {exc}"
        return report

    # ─── 4. record（生成 compare tasks） ───────────────────────────────────
    record_res = record_scenario(scenario, datasource_id, project_id=project_id)
    report.record = {
        "tasks": [
            {"id": t.id, "name": t.name, "project_id": t.project_id}
            for t in record_res["tasks"]
        ],
        "warnings": record_res["warnings"],
    }

    # ─── 5. 同步跑每个 compare task ────────────────────────────────────────
    # lazy import：runner 引 dbclients，让 service 层不强依赖
    from app.services.runner import run_task
    for task in record_res["tasks"]:
        try:
            result = run_task(task.id)
            report.runs.append(RunResult(
                task_id=task.id, task_name=task.name, ok=True,
                summary=result.summary.model_dump(),
            ))
        except Exception as exc:
            logger.warning("run_all task run failed task_id=%s: %s", task.id, exc)
            report.runs.append(RunResult(
                task_id=task.id, task_name=task.name, ok=False,
                error=str(exc),
            ))

    # ─── 6. verify ──────────────────────────────────────────────────────────
    verify_report = verify_scenario(scenario, project_id=project_id)
    report.verify = {
        "scenario_id": verify_report.scenario_id,
        "summary": verify_report.summary,
        "results": [asdict(r) for r in verify_report.results],
    }

    # 整体 ok：有任一 run 失败 / 任一 verify fail 都标 ok=False
    if any(not r.ok for r in report.runs):
        report.ok = False
    if verify_report.summary.get("fail", 0) > 0:
        report.ok = False

    return report
