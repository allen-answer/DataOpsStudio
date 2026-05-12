"""Scenario sandbox endpoints（Phase 12 切片 4）。

四个端点：
- `GET  /api/scenarios`               —— 列出 config/scenarios/ 下的 yml
- `GET  /api/scenarios/{id}`          —— 加载单个 scenario，返回模型 dump
- `POST /api/scenarios/{id}/materialize` —— 生成数据 + DDL/INSERT 到 datasource
- `POST /api/scenarios/{id}/record`   —— 把 workloads 翻译成 CompareTask 持久化

UI 在 admin 面板调这四个；MVP 先不做 lineage_script / slow_query workload。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field

from app.scenarios.ai_filler import fill_scenario
from app.scenarios.generator import generate_scenario
from app.scenarios.loader import list_scenarios, load_scenario
from app.scenarios.recorder import record_scenario
from app.scenarios.runtime import ScenarioRuntimeError, materialize_to_datasource
from app.utils.paths import SCENARIOS_DIR


router = APIRouter()


class MaterializeRequest(BaseModel):
    datasource_id: str = Field(..., min_length=1)
    drop_first: bool = True
    batch_size: int = Field(default=500, ge=1, le=10_000)
    # 切片 9：开启 → 先走 ai_filler 给 realistic 列填业务样本池再 generate
    ai_fill: bool = False


class RecordRequest(BaseModel):
    datasource_id: str = Field(..., min_length=1)
    project_id: str = ""


@router.get("/api/scenarios")
def list_scenarios_api() -> dict[str, Any]:
    """列出所有可加载的 scenario（包括坏文件，error 字段标错因）。"""
    return {"items": list_scenarios()}


@router.get("/api/scenarios/{scenario_id}")
def get_scenario_api(scenario_id: str) -> dict[str, Any]:
    """读单份 yml，返回完整 model dump（前端拿来渲染表/anomaly/workload 三块）。"""
    path = _find_scenario_path(scenario_id)
    if path is None:
        raise HTTPException(status_code=404, detail=f"scenario not found: {scenario_id}")
    try:
        scenario = load_scenario(path)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"scenario load failed: {exc}") from exc
    return {"scenario": scenario.model_dump(by_alias=True), "path": path.name}


@router.post("/api/scenarios/{scenario_id}/materialize")
def materialize_scenario_api(
    scenario_id: str,
    payload: MaterializeRequest = Body(...),
) -> dict[str, Any]:
    """generate + DDL + INSERT 到 datasource。返回 apply_plan 的 summary。"""
    scenario = _load_or_404(scenario_id)
    ai_fill_report: dict[str, Any] | None = None
    if payload.ai_fill:
        scenario, report = fill_scenario(scenario)
        ai_fill_report = {
            "ok": report.ok,
            "calls": report.calls,
            "filled_columns": report.filled_columns,
            "filled_descriptions": report.filled_descriptions,
            "errors": report.errors,
            "skipped_reason": report.skipped_reason,
        }
    data = generate_scenario(scenario)
    try:
        summary = materialize_to_datasource(
            scenario,
            data,
            payload.datasource_id,
            drop_first=payload.drop_first,
            batch_size=payload.batch_size,
        )
    except ScenarioRuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    summary["rows_generated"] = {name: len(rows) for name, rows in data.items()}
    if ai_fill_report is not None:
        summary["ai_fill"] = ai_fill_report
    return summary


@router.post("/api/scenarios/{scenario_id}/ai-fill")
def ai_fill_scenario_api(scenario_id: str) -> dict[str, Any]:
    """独立预览：跑 ai_filler 返回填好的 scenario + 报告（不持久化）。"""
    scenario = _load_or_404(scenario_id)
    filled, report = fill_scenario(scenario)
    return {
        "scenario": filled.model_dump(by_alias=True),
        "report": {
            "ok": report.ok,
            "calls": report.calls,
            "filled_columns": report.filled_columns,
            "filled_descriptions": report.filled_descriptions,
            "errors": report.errors,
            "skipped_reason": report.skipped_reason,
        },
    }


@router.post("/api/scenarios/{scenario_id}/record")
def record_scenario_api(
    scenario_id: str,
    payload: RecordRequest = Body(...),
) -> dict[str, Any]:
    """workloads → CompareTask 持久化。返回 {tasks, warnings}。"""
    scenario = _load_or_404(scenario_id)
    result = record_scenario(
        scenario, payload.datasource_id, project_id=payload.project_id
    )
    return {
        "tasks": [t.model_dump() for t in result["tasks"]],
        "warnings": result["warnings"],
    }


# ─── helpers ────────────────────────────────────────────────────────────────


def _find_scenario_path(scenario_id: str) -> Path | None:
    """找到 scenario_id 对应的 yml。优先 `<id>.yml`，回退 example。"""
    if not SCENARIOS_DIR.exists():
        return None
    candidates = sorted(SCENARIOS_DIR.glob("*.yml"))
    # 优先匹配 scenario id（需要读 yml 才能知道 id；先按文件名快筛缩窄）
    name_match = [p for p in candidates if p.stem == scenario_id or p.stem == f"{scenario_id}.example"]
    pool = name_match + [p for p in candidates if p not in name_match]
    for p in pool:
        try:
            sc = load_scenario(p)
            if sc.id == scenario_id:
                return p
        except Exception:
            continue
    return None


def _load_or_404(scenario_id: str):
    path = _find_scenario_path(scenario_id)
    if path is None:
        raise HTTPException(status_code=404, detail=f"scenario not found: {scenario_id}")
    try:
        return load_scenario(path)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"scenario load failed: {exc}") from exc
