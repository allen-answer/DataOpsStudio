"""Scenario regression verifier —— actual vs expected 回归校验（Phase 12 切片 10 + 14）。

让 scenario yml 真正成为「回归测试夹具」：每个 compare_task workload 都带
`expected: {only_source, only_target, diff, same}` 这种 ground truth；
admin 跑完对比任务后调 /verify，自动对比 actual summary vs expected，
返回 pass / fail / skipped 三态 + 每字段 delta。

匹配逻辑：
- 默认精确匹配（所有 expected 字段值 == actual 同字段值 → pass）
- 切片 14：workload 可加 `tolerance:` 块允许容差
  - 标量形式 `tolerance: 2` —— 所有字段允许 ±2 行
  - 字典形式 `tolerance: {only_source: 2, diff: 1}` —— 按字段独立容差
  - 百分比形式 `tolerance_pct: 5` —— ±5% 期望值；与 tolerance 同时存在时取宽松上限
  - 单字段 `actual - expected| <= tol` 才算命中

状态：
- pass    actual 全字段命中 expected（含 tolerance 容差）
- fail    至少一字段超出容差；deltas 字段显示 actual - expected
- no_expected  workload 没写 expected 块 → 跳过统计
- no_task      scenario 没 record 过对应 CompareTask
- no_run       task 存在但还没跑过
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.scenarios.models import Scenario


@dataclass
class WorkloadVerifyResult:
    workload_name: str
    status: str  # "pass" | "fail" | "no_expected" | "no_task" | "no_run"
    task_id: str = ""
    task_name: str = ""
    run_id: str = ""
    started_at: str = ""
    expected: dict[str, int] = field(default_factory=dict)
    actual: dict[str, int] = field(default_factory=dict)
    deltas: dict[str, int] = field(default_factory=dict)
    tolerance: dict[str, int] = field(default_factory=dict)
    match: bool = False


@dataclass
class VerifyReport:
    scenario_id: str
    results: list[WorkloadVerifyResult] = field(default_factory=list)
    summary: dict[str, int] = field(default_factory=dict)  # pass / fail / skipped


def verify_scenario(scenario: Scenario, *, project_id: str = "") -> VerifyReport:
    """对 scenario 的每个 compare_task workload 跑 expected vs actual 对比。"""
    # lazy import 避免顶层循环 + 让 test isolated_storage 把 path 切干净
    from app.services.history import list_result_history
    from app.services.repositories import task_store

    report = VerifyReport(scenario_id=scenario.id)
    prefix = f"{scenario.id} · "
    tasks_by_suffix = {
        t.name[len(prefix):]: t
        for t in task_store.list()
        if t.name.startswith(prefix)
        and (not project_id or t.project_id == project_id or not t.project_id)
    }

    pass_count = fail_count = skipped_count = 0

    for wl in scenario.workloads:
        if wl.kind != "compare_task":
            continue
        extra = wl.model_extra or {}
        wl_name = (wl.name or "").strip() or _default_name(extra)
        expected_raw = extra.get("expected")
        expected = _coerce_int_dict(expected_raw)
        tolerance = _resolve_tolerance(
            expected, extra.get("tolerance"), extra.get("tolerance_pct"),
        )

        if not expected:
            report.results.append(WorkloadVerifyResult(
                workload_name=wl_name, status="no_expected",
            ))
            skipped_count += 1
            continue

        task = tasks_by_suffix.get(wl_name)
        if task is None:
            report.results.append(WorkloadVerifyResult(
                workload_name=wl_name, status="no_task", expected=expected,
            ))
            skipped_count += 1
            continue

        runs = list_result_history(task_id=task.id, limit=1)
        if not runs:
            report.results.append(WorkloadVerifyResult(
                workload_name=wl_name, status="no_run",
                task_id=task.id, task_name=task.name, expected=expected,
            ))
            skipped_count += 1
            continue

        latest = runs[0]
        actual_raw = latest.get("summary") or {}
        actual = _coerce_int_dict(actual_raw)

        deltas: dict[str, int] = {}
        matches = True
        for key, exp_val in expected.items():
            act_val = actual.get(key, 0)
            delta = act_val - exp_val
            deltas[key] = delta
            tol = tolerance.get(key, 0)
            if abs(delta) > tol:
                matches = False

        report.results.append(WorkloadVerifyResult(
            workload_name=wl_name,
            status="pass" if matches else "fail",
            task_id=task.id,
            task_name=task.name,
            run_id=str(latest.get("run_id") or ""),
            started_at=str(latest.get("started_at") or ""),
            expected=expected,
            actual=actual,
            deltas=deltas,
            tolerance=tolerance,
            match=matches,
        ))
        if matches:
            pass_count += 1
        else:
            fail_count += 1

    report.summary = {"pass": pass_count, "fail": fail_count, "skipped": skipped_count}
    return report


# ─── helpers ────────────────────────────────────────────────────────────────


def _default_name(extra: dict[str, Any]) -> str:
    """跟 recorder._build_select 同套兜底：没 name 时用 `source vs target`。"""
    src = extra.get("source") or "?"
    tgt = extra.get("target") or "?"
    return f"{src} vs {tgt}"


def _resolve_tolerance(
    expected: dict[str, int],
    tol_raw: Any,
    pct_raw: Any,
) -> dict[str, int]:
    """合并 absolute tolerance 和 percentage tolerance —— 同字段取宽松上限。

    入参：
    - `tol_raw`：标量 int/float（应用到所有字段）或 dict（字段独立）
    - `pct_raw`：标量百分比（如 5 = ±5%），按 expected 值算出每字段的容差行数

    返回 dict[field → tolerance]，没传任一时返回 {}。
    """
    abs_tol = _scalar_or_int_dict(tol_raw, expected.keys())
    if pct_raw is None:
        return abs_tol
    try:
        pct = float(pct_raw)
    except (TypeError, ValueError):
        return abs_tol
    if pct < 0:
        return abs_tol
    merged: dict[str, int] = dict(abs_tol)
    for key, exp_val in expected.items():
        pct_tol = int(abs(exp_val) * pct / 100)
        merged[key] = max(merged.get(key, 0), pct_tol)
    return merged


def _scalar_or_int_dict(value: Any, keys: Any) -> dict[str, int]:
    """tolerance 可以是 scalar（应用全字段）或 dict（字段独立）。负值钳到 0。"""
    if value is None:
        return {}
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        v = max(0, int(value))
        return {k: v for k in keys}
    if isinstance(value, dict):
        out: dict[str, int] = {}
        for k, raw in value.items():
            if not isinstance(k, str):
                continue
            try:
                out[k] = max(0, int(raw))
            except (TypeError, ValueError):
                continue
        return out
    return {}


def _coerce_int_dict(value: Any) -> dict[str, int]:
    """expected / actual 都可能含字符串数字，统一 int 化；不可解析丢弃。"""
    if not isinstance(value, dict):
        return {}
    out: dict[str, int] = {}
    for k, v in value.items():
        if not isinstance(k, str):
            continue
        try:
            out[k] = int(v)
        except (TypeError, ValueError):
            continue
    return out
