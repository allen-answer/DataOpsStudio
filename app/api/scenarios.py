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

import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

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
from app.api._authz import require_datasource_access
from app.models import User
from app.services.operation_policy import Operation, assert_operation_allowed
from app.services.repositories import datasource_store
from app.utils.paths import SCENARIOS_DIR


# Phase 14 #3:_enforce_sandbox_only 已升级为 operation_policy。
# 旧 helper 保留兼容签名(deprecate),所有 caller 改用 assert_operation_allowed。
# 端点内直接走 require_datasource_access + assert_operation_allowed(current, ds, op),
# 不再走这个 ad-hoc guard。


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


# Phase 14 #3 Round 6 L — 内置行业模板。必须放在 /{scenario_id} 之前注册,
# 否则 FastAPI first-match-wins 把 GET /scenarios/templates 当作
# scenario_id="templates" 吃掉 → _load_or_404("templates") 报 not found
@router.get("/api/scenarios/templates")
def list_scenario_templates(
    current: User = Depends(require_role("editor")),
) -> dict[str, Any]:
    """内置行业模板列表 — config/scenarios/template-*.example.yml。"""
    items: list[dict[str, Any]] = []
    if SCENARIOS_DIR.exists():
        for p in sorted(SCENARIOS_DIR.glob("template-*.example.yml")):
            try:
                sc = load_scenario(p)
                items.append({
                    "id": sc.id,
                    "name": sc.name,
                    "description": (sc.description or "")[:200],
                    "dialect": sc.dialect,
                    "tables_count": len(sc.tables),
                    "workloads_count": len(sc.workloads),
                    "file": p.name,
                })
            except Exception as exc:
                items.append({
                    "file": p.name, "error": str(exc),
                })
    return {"items": items}


@router.get("/api/scenarios/templates/{template_file}")
def get_scenario_template(
    template_file: str,
    current: User = Depends(require_role("editor")),
) -> dict[str, Any]:
    """拿单个模板的完整内容(yml 文本 + 解析后的 scenario dict)。
    白名单约束 template_file 只允许 template-*.example.yml 防路径穿越。
    """
    if not re.match(r"^template-[A-Za-z0-9_\-]+\.example\.yml$", template_file):
        raise HTTPException(status_code=400, detail="模板文件名格式不合法")
    path = SCENARIOS_DIR / template_file
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"模板 {template_file} 不存在")
    scenario = load_scenario(path)
    return {
        "file": template_file,
        "yml_text": path.read_text(encoding="utf-8"),
        "scenario": scenario.model_dump(by_alias=True, exclude_none=True),
    }


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
    current: User = Depends(require_role("editor")),
) -> dict[str, Any]:
    """generate + DDL + INSERT 到 datasource。返回 apply_plan 的 summary。

    Phase 14 #3 风控:
    - require_datasource_access:项目级授权
    - assert_operation_allowed(SCENARIO_MATERIALIZE):env=sandbox + allow_scenario_write=True
    """
    ds = require_datasource_access(current, payload.datasource_id, detail="无权访问该数据源")
    assert_operation_allowed(
        current, ds, Operation.SCENARIO_MATERIALIZE,
        context={"scenario_id": scenario_id},
    )
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
    current: User = Depends(require_role("editor")),
) -> dict[str, Any]:
    """一键链：fill → generate → materialize → record → run tasks → verify。

    报告里 ok=true 表示 6 步都成；任一 compare run 失败 / verify 有 fail
    → ok=false（admin / CI 一眼判断）。

    Phase 14 #3 风控:require_datasource_access + SCENARIO_RUN_ALL policy
    """
    ds = require_datasource_access(current, payload.datasource_id, detail="无权访问该数据源")
    assert_operation_allowed(
        current, ds, Operation.SCENARIO_RUN_ALL,
        context={"scenario_id": scenario_id},
    )
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
def verify_scenario_api(
    scenario_id: str,
    project_id: str = "",
    current: User = Depends(require_role("editor")),
) -> dict[str, Any]:
    """跑 actual vs expected 回归校验。

    遍历 compare_task workload，按命名规则（`<scenario_id> · <wl_name>`）
    找到 recorder 当时创建的 CompareTask，拿最近一次运行 summary，对比 yml
    expected 块。三态：pass / fail / skipped（no_expected / no_task / no_run）。

    Phase 14 #3 收口:caller 传的 project_id 必须是当前用户有权访问的项目;
    否则 editor 可拿别人项目 id 蹭 verify 结果。admin 不受限。
    """
    from app.api._authz import can_access_project
    if project_id and not can_access_project(current, project_id):
        raise HTTPException(
            status_code=403,
            detail=f"无权访问 project_id={project_id} 的 verify 结果",
        )
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
    current: User = Depends(require_role("editor")),
) -> dict[str, Any]:
    """workloads → CompareTask 持久化。返回 {tasks, warnings}。

    Phase 14 #3 风控:require_datasource_access + SCENARIO_RECORD policy
    """
    ds = require_datasource_access(current, payload.datasource_id, detail="无权访问该数据源")
    assert_operation_allowed(
        current, ds, Operation.SCENARIO_RECORD,
        context={"scenario_id": scenario_id, "project_id": payload.project_id},
    )
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
    current: User = Depends(require_role("editor")),
) -> dict[str, Any]:
    """走 introspect_columns + introspect_indexes + introspect_row_count 拉真
    表结构,翻成 scenario yml。

    Phase 14 #3 风控:
    - require_datasource_access:项目级授权
    - SCHEMA_IMPORT_PREVIEW:读 information_schema 默认 sandbox / prod 需 allow_schema_import
    - SCHEMA_IMPORT_SAVE (save=True 时):仅限 sandbox + allow_schema_save

    `save=True` 直接落 `config/scenarios/<scenario_id>.yml` 并刷新 list
    (注意会覆盖同名文件);`save=False` 仅返 yml 文本让 caller 自己保存。
    """
    ds = require_datasource_access(current, payload.datasource_id, detail="无权访问该数据源")
    # preview 阶段先校
    assert_operation_allowed(
        current, ds, Operation.SCHEMA_IMPORT_PREVIEW,
        context={"scenario_id": payload.scenario_id, "table_count": len(payload.table_names)},
    )
    # save=True 再校一次 SAVE policy(更严)
    if payload.save:
        assert_operation_allowed(
            current, ds, Operation.SCHEMA_IMPORT_SAVE,
            context={"scenario_id": payload.scenario_id},
        )

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


# ─── Phase 14 #3 Round 4 — 可视化场景编辑器 save endpoint ────────────────


class SaveYmlRequest(BaseModel):
    """前端可视化 builder 直接提交 scenario dict(已经按 Scenario model 形状),
    不依赖任何 datasource — 不连库纯落 yml。
    """
    scenario: dict[str, Any]
    overwrite: bool = False    # 同名 yml 是否覆盖,默认拒
    model_config = ConfigDict(extra="forbid")


class MetadataCsvRequest(BaseModel):
    """Phase 14 #3 Round 6 M — metadata csv 上传。

    csv_text 接受两类格式拼接(用空行分隔):
      段 1:`table,row_count` 表行数
      段 2:`table,column,ndv,top_5_values,top_5_freq` 列分布
    """
    csv_text: str = Field(..., min_length=1)
    model_config = ConfigDict(extra="forbid")


@router.post("/api/scenarios/import-from-metadata")
def import_from_metadata_csv(
    payload: MetadataCsvRequest = Body(...),
    current: User = Depends(require_role("editor")),
) -> dict[str, Any]:
    """Round 6 M — 从脱敏 metadata csv 反生成 scenario form payload。

    用法:DBA 给你 ANALYZE 风格的统计快照(table 行数 + 列 ndv + top values),
    你粘 csv 进 builder,系统反推 enum values + distribution + rows → scenario。
    法规上"统计信息 + 不含行级数据",可以从生产流出。

    返:{tables: [{ name, rows, columns: [{ name, gen, values, distribution }] }, ...]}
    builder 前端拿这个填 form。
    """
    import csv as _csv
    import io as _io

    text = (payload.csv_text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="csv_text 为空")

    # 拆段:空行(只含空白)隔开多段。第一段表行数,第二段列分布(任一缺则跳过)
    sections: list[list[str]] = [[]]
    for line in text.splitlines():
        if not line.strip():
            if sections[-1]:
                sections.append([])
            continue
        sections[-1].append(line)
    sections = [s for s in sections if s]

    table_rows: dict[str, int] = {}
    column_dist: dict[tuple[str, str], dict[str, Any]] = {}
    warnings: list[str] = []

    for section in sections:
        reader = _csv.reader(_io.StringIO("\n".join(section)))
        header = next(reader, None)
        if not header:
            continue
        header_l = [h.strip().lower() for h in header]
        if "row_count" in header_l and "table" in header_l:
            # 段 1:table, row_count
            tidx = header_l.index("table")
            ridx = header_l.index("row_count")
            for row in reader:
                if len(row) <= max(tidx, ridx):
                    continue
                try:
                    table_rows[row[tidx].strip()] = int(row[ridx].strip())
                except ValueError:
                    warnings.append(f"行数解析失败: {row}")
        elif "column" in header_l and "table" in header_l:
            # 段 2:table, column, ndv, top_5_values, top_5_freq
            tidx = header_l.index("table")
            cidx = header_l.index("column")
            vidx = header_l.index("top_5_values") if "top_5_values" in header_l else -1
            fidx = header_l.index("top_5_freq") if "top_5_freq" in header_l else -1
            nidx = header_l.index("ndv") if "ndv" in header_l else -1
            for row in reader:
                if len(row) <= max(tidx, cidx):
                    continue
                t = row[tidx].strip()
                c = row[cidx].strip()
                entry: dict[str, Any] = {}
                if nidx >= 0 and nidx < len(row):
                    try:
                        entry["ndv"] = int(row[nidx])
                    except ValueError:
                        pass
                if vidx >= 0 and vidx < len(row):
                    entry["values"] = [
                        v.strip() for v in row[vidx].split("|") if v.strip()
                    ]
                if fidx >= 0 and fidx < len(row):
                    try:
                        entry["freq"] = [
                            float(x.strip()) for x in row[fidx].split("|") if x.strip()
                        ]
                    except ValueError:
                        pass
                column_dist[(t, c)] = entry
        else:
            warnings.append(f"未识别的 csv 段(表头不含 table+row_count 也不含 table+column):{header}")

    # 合成 tables 列表
    tables: list[dict[str, Any]] = []
    seen_tables = set(table_rows.keys()) | {t for (t, _) in column_dist.keys()}
    for tname in sorted(seen_tables):
        cols_for_table = [
            {"name": c, **column_dist[(t, c)]}
            for (t, c) in sorted(column_dist.keys()) if t == tname
        ]
        # 自动选 gen:ndv 小 + 有 values → enum,否则 realistic
        for col in cols_for_table:
            ndv = col.get("ndv") or 0
            values = col.get("values") or []
            if values and 0 < ndv <= 20:
                col["gen"] = "enum"
            else:
                col["gen"] = "realistic"
                # ndv 高的 dropoff values,realistic 不需要
                if values:
                    col["sample_values"] = values  # 给 UI 提示
        tables.append({
            "name": tname,
            "role": "source",
            "rows": table_rows.get(tname, 1000),
            "columns": cols_for_table,
        })

    return {
        "tables_count": len(tables),
        "tables": tables,
        "warnings": warnings,
    }


@router.post("/api/scenarios/save-yml")
def save_scenario_yml_api(
    payload: SaveYmlRequest = Body(...),
    current: User = Depends(require_role("editor")),
) -> dict[str, Any]:
    """前端可视化 builder 调用:把 dict 形式的 scenario 转 yml 文本落盘。

    Phase 14 #3 风控:
    - Pydantic Scenario.model_validate 校验输入(防 LLM / 用户输入半残品)
    - 落盘前不需要 datasource — 但仍走 SCHEMA_IMPORT_SAVE 同语义的 admin 权限
      (本质 = 把 yml 写到 config/scenarios,跟 import 的 save=True 同副作用)
    - id 正则约束(scenario_id 是文件名)
    - overwrite=False(默认)时同名拒,防误改既有 fixture
    """
    import yaml

    from app.scenarios.models import Scenario

    # Pydantic 校验(forbid 拦 yml 笔误 + Literal 拦 anomaly/workload kind)
    try:
        scenario = Scenario.model_validate(payload.scenario)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=400,
            detail=f"scenario 模型校验失败: {exc}",
        ) from exc

    # id 二次正则约束(scenario_id 直接拼文件名)
    if not re.match(r"^[A-Za-z0-9_\-]+$", scenario.id):
        raise HTTPException(
            status_code=400,
            detail="scenario.id 只允许字母 / 数字 / _ / -",
        )

    SCENARIOS_DIR.mkdir(parents=True, exist_ok=True)
    target = SCENARIOS_DIR / f"{scenario.id}.yml"
    if target.exists() and not payload.overwrite:
        raise HTTPException(
            status_code=409,
            detail=(
                f"scenario '{scenario.id}.yml' 已存在。"
                "如要覆盖请显式传 overwrite=true(注意会丢失既有 fixture)。"
            ),
        )

    # dump yml — 用 model_dump(by_alias=True, exclude_none=True) 拿干净 dict
    clean = scenario.model_dump(by_alias=True, exclude_none=True)
    yml_text = yaml.safe_dump(
        clean, allow_unicode=True, sort_keys=False, default_flow_style=False,
    )
    target.write_text(yml_text, encoding="utf-8")

    return {
        "scenario_id": scenario.id,
        "saved_path": target.name,
        "yml_text": yml_text,
        "tables_count": len(scenario.tables),
        "workloads_count": len(scenario.workloads),
        "overwrite": payload.overwrite,
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
