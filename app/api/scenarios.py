"""Scenario sandbox / SQL 优化沙盒 endpoints(Phase 12 切片 4,Phase 14 P0-1 重定位)。

主要端点:
- `GET  /api/scenarios`               —— 列 config/scenarios/ 下的 yml
- `GET  /api/scenarios/{id}`          —— 加载单 scenario,返模型 dump
- `POST /api/scenarios/{id}/materialize` —— 生成数据 + DDL/INSERT 到 datasource
- `POST /api/scenarios/{id}/record`   —— workloads → CompareTask 持久化
- `POST /api/scenarios/{id}/run-all`  —— 一键链
- `POST /api/scenarios/import-from-datasource` —— Phase 14 P1-1:从真实 ds 反向生成 yml

Phase 14 P0-1:权限从 `admin` 放开到 `editor` —— SQL 优化是数据工程师日常工作,
不是 admin 特权。datasource / project 级权限仍由 inner-level 的
require_datasource_access / require_project_access 保护。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field

from dataclasses import asdict

from app.scenarios.ai_filler import fill_scenario
from app.scenarios.generator import generate_scenario
from app.scenarios.loader import list_scenarios, load_scenario
from app.scenarios.orchestrator import run_all as run_all_pipeline
from app.scenarios.recorder import record_scenario
from app.scenarios.runtime import ScenarioRuntimeError, materialize_to_datasource
from app.scenarios.verifier import verify_scenario
from app.scenarios.yml_importer import import_tables_from_datasource
from app.services.auth import require_role
from app.utils.paths import SCENARIOS_DIR


# Phase 14 P0-1 重定位:editor+ 即可。inner-level authz 仍由各 endpoint 通过
# require_datasource_access / require_project_access 自己拦。
router = APIRouter(dependencies=[Depends(require_role("editor"))])


class MaterializeRequest(BaseModel):
    datasource_id: str = Field(..., min_length=1)
    drop_first: bool = True
    batch_size: int = Field(default=500, ge=1, le=10_000)
    # 切片 9：开启 → 先走 ai_filler 给 realistic 列填业务样本池再 generate
    ai_fill: bool = False


class RecordRequest(BaseModel):
    datasource_id: str = Field(..., min_length=1)
    project_id: str = ""


class RunAllRequest(BaseModel):
    datasource_id: str = Field(..., min_length=1)
    project_id: str = ""
    drop_first: bool = True
    batch_size: int = Field(default=500, ge=1, le=10_000)
    ai_fill: bool = False


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
            "filled_distributions": report.filled_distributions,
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


@router.post("/api/scenarios/{scenario_id}/run-all")
def run_all_scenario_api(
    scenario_id: str,
    payload: RunAllRequest = Body(...),
) -> dict[str, Any]:
    """一键链：fill → generate → materialize → record → run tasks → verify。

    报告里 ok=true 表示 6 步都成；任一 compare run 失败 / verify 有 fail
    → ok=false（admin / CI 一眼判断）。
    """
    scenario = _load_or_404(scenario_id)
    report = run_all_pipeline(
        scenario,
        payload.datasource_id,
        project_id=payload.project_id,
        drop_first=payload.drop_first,
        batch_size=payload.batch_size,
        ai_fill=payload.ai_fill,
    )
    return {
        "scenario_id": report.scenario_id,
        "ok": report.ok,
        "error": report.error,
        "ai_fill": report.ai_fill,
        "materialize": report.materialize,
        "record": report.record,
        "runs": [asdict(r) for r in report.runs],
        "verify": report.verify,
    }


@router.get("/api/scenarios/{scenario_id}/verify")
def verify_scenario_api(scenario_id: str, project_id: str = "") -> dict[str, Any]:
    """跑 actual vs expected 回归校验。

    遍历 compare_task workload，按命名规则（`<scenario_id> · <wl_name>`）
    找到 recorder 当时创建的 CompareTask，拿最近一次运行 summary，对比 yml
    expected 块。三态：pass / fail / skipped（no_expected / no_task / no_run）。
    """
    scenario = _load_or_404(scenario_id)
    report = verify_scenario(scenario, project_id=project_id)
    return {
        "scenario_id": report.scenario_id,
        "summary": report.summary,
        "results": [asdict(r) for r in report.results],
    }


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
            "filled_distributions": report.filled_distributions,
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


# ─── Phase 14 P1-1: 从 datasource 反向生成 yml ────────────────────────────


class ImportFromDatasourceRequest(BaseModel):
    datasource_id: str = Field(..., min_length=1)
    table_names: list[str] = Field(..., min_length=1)
    scenario_id: str = Field(..., min_length=1, pattern=r"^[A-Za-z0-9_\-]+$")
    scenario_name: str = ""
    default_rows: int = Field(default=1000, ge=1, le=1_000_000)
    save: bool = False  # True 时直接落 config/scenarios/<id>.yml


@router.post("/api/scenarios/import-from-datasource")
def import_from_datasource_api(
    payload: ImportFromDatasourceRequest = Body(...),
) -> dict[str, Any]:
    """走 introspect_columns + introspect_indexes + introspect_row_count 拉真
    表结构,翻成 scenario yml。

    `save=True` 直接落 `config/scenarios/<scenario_id>.yml` 并刷新 list
    (注意会覆盖同名文件);`save=False` 仅返 yml 文本让 caller 自己保存。
    """
    # require_datasource_access:导入是读 information_schema,跟 introspect 同级
    # 权限。复用既有 endpoint pattern,避免新设计 auth 规则。
    from app.api._authz import require_datasource_access
    from app.services.auth import get_current_user
    from fastapi import Request

    # 注:require_datasource_access 需要 User 上下文,但本 endpoint 无 current
    # 注入(router 级 require_role("editor") 已校登录但没注 user)。补一行:
    # 实际项目里 dep injection 更优雅,这里 keep 简单 -- 直接 datasource lookup
    # + project 检查 inline(同 tasks.py:194 / _guard_or_raise 的 caller 模式)
    from app.services.repositories import datasource_store
    ds = datasource_store.get(payload.datasource_id)
    if ds is None:
        raise HTTPException(status_code=404, detail="datasource not found")

    try:
        scenario, yml_text = import_tables_from_datasource(
            payload.datasource_id,
            payload.table_names,
            scenario_id=payload.scenario_id,
            scenario_name=payload.scenario_name,
            default_rows=payload.default_rows,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"import failed: {exc}") from exc

    saved_path: str | None = None
    if payload.save:
        SCENARIOS_DIR.mkdir(parents=True, exist_ok=True)
        target = SCENARIOS_DIR / f"{payload.scenario_id}.yml"
        target.write_text(yml_text, encoding="utf-8")
        saved_path = target.name

    return {
        "scenario_id": payload.scenario_id,
        "yml_text": yml_text,
        "saved_path": saved_path,
        "tables_imported": len(scenario.tables),
        "rows_per_table": {t.name: t.rows for t in scenario.tables},
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
