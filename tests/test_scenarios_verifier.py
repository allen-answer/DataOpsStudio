"""Scenario verifier tests（Phase 12 切片 10）。

scope:
- `verify_scenario`：5 个状态（pass / fail / no_expected / no_task / no_run）
- summary 汇总：pass/fail/skipped 计数
- 非 compare_task workload 自动跳过
- /api/scenarios/{id}/verify endpoint
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.models.common import SourceKind, SqlMode
from app.scenarios.models import Scenario
from app.scenarios.verifier import verify_scenario


def _scenario(**kwargs: Any) -> Scenario:
    payload = {"id": "test-sc", "name": "T", "seed": 42}
    payload.update(kwargs)
    return Scenario.model_validate(payload)


def _make_history_run(results_dir: Path, *, run_id: str, task_id: str,
                      summary: dict[str, int]) -> None:
    """直接写一份 history JSON 到 results dir，模拟跑完的 compare run。"""
    (results_dir / f"{run_id}.json").write_text(json.dumps({
        "run_id": run_id,
        "task_id": task_id,
        "task_name": "irrelevant",
        "started_at": "2026-05-12T10:00:00",
        "elapsed_seconds": 1.0,
        "source_rows": sum(summary.values()),
        "target_rows": sum(summary.values()),
        "summary": summary,
    }), encoding="utf-8")


def _create_compare_task(name: str, project_id: str = "") -> str:
    """新建一个 CompareTask 并返回 id（SQL 模式 + 必填项凑齐）。"""
    from app.models.compare import CompareTaskCreate
    from app.services.repositories import task_store
    payload = CompareTaskCreate(
        name=name,
        source_kind=SourceKind.SQL, target_kind=SourceKind.SQL,
        source_id="ds-1", target_id="ds-1",
        sql_mode=SqlMode.DOUBLE,
        source_sql="SELECT * FROM s",
        target_sql="SELECT * FROM t",
        key_columns=["id"],
        project_id=project_id,
    )
    return task_store.create(payload).id


def _basic_scenario(workloads: list[dict[str, Any]]) -> Scenario:
    return _scenario(workloads=workloads, tables=[{
        "name": "t", "role": "source", "rows": 0,
        "columns": [{"name": "id", "type": "INT", "pk": True, "gen": "sequence"}],
    }])


# ─── 状态：no_expected ──────────────────────────────────────────────────────


def test_no_expected_block_is_skipped(isolated_storage):
    s = _basic_scenario([{
        "kind": "compare_task", "name": "w1",
        "source": "t", "target": "t", "keys": ["id"],
        # 没有 expected
    }])
    report = verify_scenario(s)
    assert report.summary == {"pass": 0, "fail": 0, "skipped": 1}
    assert report.results[0].status == "no_expected"


# ─── 状态：no_task ──────────────────────────────────────────────────────────


def test_no_matching_task_skipped(isolated_storage):
    s = _basic_scenario([{
        "kind": "compare_task", "name": "w1",
        "source": "t", "target": "t", "keys": ["id"],
        "expected": {"only_source": 1, "only_target": 2, "diff": 3, "same": 100},
    }])
    report = verify_scenario(s)
    assert report.summary == {"pass": 0, "fail": 0, "skipped": 1}
    r0 = report.results[0]
    assert r0.status == "no_task"
    assert r0.expected == {"only_source": 1, "only_target": 2, "diff": 3, "same": 100}


# ─── 状态：no_run ──────────────────────────────────────────────────────────


def test_task_no_run_skipped(isolated_storage):
    """task 存在但还没跑过 → no_run。"""
    _create_compare_task("test-sc · w1")
    s = _basic_scenario([{
        "kind": "compare_task", "name": "w1",
        "source": "t", "target": "t", "keys": ["id"],
        "expected": {"only_source": 1, "diff": 0, "same": 99, "only_target": 0},
    }])
    report = verify_scenario(s)
    assert report.summary == {"pass": 0, "fail": 0, "skipped": 1}
    assert report.results[0].status == "no_run"
    assert report.results[0].task_id  # task_id 填了


# ─── 状态：pass ─────────────────────────────────────────────────────────────


def test_actual_matches_expected_pass(isolated_storage):
    task_id = _create_compare_task("test-sc · w1")
    _make_history_run(
        isolated_storage["results"], run_id="run-1", task_id=task_id,
        summary={"only_source": 20, "only_target": 5, "diff": 10, "same": 965},
    )
    s = _basic_scenario([{
        "kind": "compare_task", "name": "w1",
        "source": "t", "target": "t", "keys": ["id"],
        "expected": {"only_source": 20, "only_target": 5, "diff": 10, "same": 965},
    }])
    report = verify_scenario(s)
    assert report.summary == {"pass": 1, "fail": 0, "skipped": 0}
    r = report.results[0]
    assert r.status == "pass"
    assert r.match is True
    assert all(d == 0 for d in r.deltas.values())
    assert r.run_id == "run-1"


# ─── 状态：fail ─────────────────────────────────────────────────────────────


def test_actual_differs_from_expected_fail(isolated_storage):
    task_id = _create_compare_task("test-sc · w1")
    _make_history_run(
        isolated_storage["results"], run_id="run-1", task_id=task_id,
        summary={"only_source": 19, "only_target": 5, "diff": 12, "same": 965},
    )
    s = _basic_scenario([{
        "kind": "compare_task", "name": "w1",
        "source": "t", "target": "t", "keys": ["id"],
        "expected": {"only_source": 20, "only_target": 5, "diff": 10, "same": 965},
    }])
    report = verify_scenario(s)
    assert report.summary == {"pass": 0, "fail": 1, "skipped": 0}
    r = report.results[0]
    assert r.status == "fail"
    assert r.match is False
    assert r.deltas == {"only_source": -1, "only_target": 0, "diff": 2, "same": 0}


def test_actual_missing_field_treated_as_zero_delta(isolated_storage):
    """history summary 漏字段 → 当 0 算（不抛），delta 反映完整差距。"""
    task_id = _create_compare_task("test-sc · w1")
    _make_history_run(
        isolated_storage["results"], run_id="run-1", task_id=task_id,
        summary={"only_source": 20},  # 漏 only_target / diff / same
    )
    s = _basic_scenario([{
        "kind": "compare_task", "name": "w1",
        "source": "t", "target": "t", "keys": ["id"],
        "expected": {"only_source": 20, "only_target": 5, "diff": 10, "same": 965},
    }])
    r = verify_scenario(s).results[0]
    assert r.status == "fail"
    assert r.deltas["only_target"] == -5  # 0 - 5
    assert r.deltas["same"] == -965


# ─── 切片 14：tolerance 容差 ────────────────────────────────────────────────


def test_scalar_tolerance_allows_pass_within_band(isolated_storage):
    """tolerance: 2 标量 —— 全字段允许 ±2 偏移。"""
    task_id = _create_compare_task("test-sc · w1")
    _make_history_run(
        isolated_storage["results"], run_id="run-1", task_id=task_id,
        # diff 偏离 +1, only_source 偏离 -2 都在 ±2 内
        summary={"only_source": 18, "only_target": 5, "diff": 11, "same": 965},
    )
    s = _basic_scenario([{
        "kind": "compare_task", "name": "w1",
        "source": "t", "target": "t", "keys": ["id"],
        "expected": {"only_source": 20, "only_target": 5, "diff": 10, "same": 965},
        "tolerance": 2,
    }])
    r = verify_scenario(s).results[0]
    assert r.status == "pass"
    assert r.tolerance == {"only_source": 2, "only_target": 2, "diff": 2, "same": 2}
    assert r.deltas == {"only_source": -2, "only_target": 0, "diff": 1, "same": 0}


def test_scalar_tolerance_fail_when_one_field_exceeds(isolated_storage):
    """单字段超出容差就 fail。"""
    task_id = _create_compare_task("test-sc · w1")
    _make_history_run(
        isolated_storage["results"], run_id="run-1", task_id=task_id,
        # only_source 偏 -3 超 ±2 → fail（即使其它字段都在容差内）
        summary={"only_source": 17, "only_target": 5, "diff": 10, "same": 965},
    )
    s = _basic_scenario([{
        "kind": "compare_task", "name": "w1",
        "source": "t", "target": "t", "keys": ["id"],
        "expected": {"only_source": 20, "only_target": 5, "diff": 10, "same": 965},
        "tolerance": 2,
    }])
    r = verify_scenario(s).results[0]
    assert r.status == "fail"
    assert r.deltas["only_source"] == -3


def test_per_field_tolerance_dict(isolated_storage):
    """tolerance 字典形式 —— 字段独立容差。"""
    task_id = _create_compare_task("test-sc · w1")
    _make_history_run(
        isolated_storage["results"], run_id="run-1", task_id=task_id,
        # only_source 偏 -5（容差 5 内 → ok）, diff 偏 +1（容差 1 内 → ok），
        # only_target / same 没设容差 = 0，但 actual 跟 expected 一致也 ok
        summary={"only_source": 15, "only_target": 5, "diff": 11, "same": 965},
    )
    s = _basic_scenario([{
        "kind": "compare_task", "name": "w1",
        "source": "t", "target": "t", "keys": ["id"],
        "expected": {"only_source": 20, "only_target": 5, "diff": 10, "same": 965},
        "tolerance": {"only_source": 5, "diff": 1},
    }])
    r = verify_scenario(s).results[0]
    assert r.status == "pass"
    # 未设的字段容差为 0（精确匹配）
    assert r.tolerance == {"only_source": 5, "diff": 1}


def test_pct_tolerance_resolves_per_field(isolated_storage):
    """tolerance_pct: 5 —— 按 expected 值的 5% 算每字段容差行数。"""
    task_id = _create_compare_task("test-sc · w1")
    _make_history_run(
        isolated_storage["results"], run_id="run-1", task_id=task_id,
        # expected.same=1000 → 5% = 50 → actual=970 偏 30 在容差内
        # expected.only_source=20 → 5% = 1 → actual=21 偏 1 在容差内
        summary={"only_source": 21, "only_target": 5, "diff": 10, "same": 970},
    )
    s = _basic_scenario([{
        "kind": "compare_task", "name": "w1",
        "source": "t", "target": "t", "keys": ["id"],
        "expected": {"only_source": 20, "only_target": 5, "diff": 10, "same": 1000},
        "tolerance_pct": 5,
    }])
    r = verify_scenario(s).results[0]
    assert r.status == "pass"
    assert r.tolerance["same"] == 50  # 1000 * 5% = 50
    assert r.tolerance["only_source"] == 1  # 20 * 5% = 1


def test_abs_and_pct_tolerance_takes_max(isolated_storage):
    """tolerance + tolerance_pct 同存 —— 取宽松上限（max）。"""
    task_id = _create_compare_task("test-sc · w1")
    _make_history_run(
        isolated_storage["results"], run_id="run-1", task_id=task_id,
        # expected.only_source=20: abs=3, pct 5% = 1 → max=3 → actual 偏 -3 ok
        summary={"only_source": 17, "only_target": 5, "diff": 10, "same": 965},
    )
    s = _basic_scenario([{
        "kind": "compare_task", "name": "w1",
        "source": "t", "target": "t", "keys": ["id"],
        "expected": {"only_source": 20, "only_target": 5, "diff": 10, "same": 965},
        "tolerance": 3,
        "tolerance_pct": 5,
    }])
    r = verify_scenario(s).results[0]
    assert r.status == "pass"
    assert r.tolerance["only_source"] == 3  # max(3, 20*5%=1)


def test_no_tolerance_still_exact_match(isolated_storage):
    """没传 tolerance —— 保持精确匹配 (back-compat)。"""
    task_id = _create_compare_task("test-sc · w1")
    _make_history_run(
        isolated_storage["results"], run_id="run-1", task_id=task_id,
        summary={"only_source": 21, "only_target": 5, "diff": 10, "same": 965},  # 偏 1
    )
    s = _basic_scenario([{
        "kind": "compare_task", "name": "w1",
        "source": "t", "target": "t", "keys": ["id"],
        "expected": {"only_source": 20, "only_target": 5, "diff": 10, "same": 965},
    }])
    r = verify_scenario(s).results[0]
    assert r.status == "fail"
    assert r.tolerance == {}


def test_negative_tolerance_clamped_to_zero(isolated_storage):
    """负值容差防呆 —— 钳到 0（等价于精确匹配）。"""
    task_id = _create_compare_task("test-sc · w1")
    _make_history_run(
        isolated_storage["results"], run_id="run-1", task_id=task_id,
        summary={"only_source": 20, "only_target": 5, "diff": 10, "same": 965},
    )
    s = _basic_scenario([{
        "kind": "compare_task", "name": "w1",
        "source": "t", "target": "t", "keys": ["id"],
        "expected": {"only_source": 20, "only_target": 5, "diff": 10, "same": 965},
        "tolerance": -5,
    }])
    r = verify_scenario(s).results[0]
    assert r.status == "pass"
    assert all(v == 0 for v in r.tolerance.values())


# ─── 多 workload + 混合状态 ────────────────────────────────────────────────


def test_mixed_workloads_summary_correct(isolated_storage):
    task_pass = _create_compare_task("test-sc · pass-one")
    task_fail = _create_compare_task("test-sc · fail-one")
    _make_history_run(
        isolated_storage["results"], run_id="rp", task_id=task_pass,
        summary={"only_source": 1, "only_target": 0, "diff": 0, "same": 99},
    )
    _make_history_run(
        isolated_storage["results"], run_id="rf", task_id=task_fail,
        summary={"only_source": 5, "only_target": 0, "diff": 0, "same": 95},
    )
    # third workload has no task → no_task
    s = _basic_scenario([
        {"kind": "compare_task", "name": "pass-one",
         "source": "t", "target": "t", "keys": ["id"],
         "expected": {"only_source": 1, "only_target": 0, "diff": 0, "same": 99}},
        {"kind": "compare_task", "name": "fail-one",
         "source": "t", "target": "t", "keys": ["id"],
         "expected": {"only_source": 1, "only_target": 0, "diff": 0, "same": 99}},
        {"kind": "compare_task", "name": "missing-one",
         "source": "t", "target": "t", "keys": ["id"],
         "expected": {"only_source": 1, "only_target": 0, "diff": 0, "same": 99}},
    ])
    report = verify_scenario(s)
    assert report.summary == {"pass": 1, "fail": 1, "skipped": 1}
    statuses = {r.workload_name: r.status for r in report.results}
    assert statuses == {"pass-one": "pass", "fail-one": "fail", "missing-one": "no_task"}


def test_non_compare_workloads_filtered_out(isolated_storage):
    s = _basic_scenario([
        {"kind": "lineage_script", "name": "lin", "sql": "SELECT 1"},
        {"kind": "slow_query", "name": "slow", "sql": "SELECT 2"},
    ])
    report = verify_scenario(s)
    assert report.results == []
    assert report.summary == {"pass": 0, "fail": 0, "skipped": 0}


def test_project_id_filter(isolated_storage):
    """project_id 非空时仅匹配该项目下 task。"""
    _create_compare_task("test-sc · w1", project_id="proj-A")
    _create_compare_task("test-sc · w1", project_id="proj-B")
    s = _basic_scenario([{
        "kind": "compare_task", "name": "w1",
        "source": "t", "target": "t", "keys": ["id"],
        "expected": {"only_source": 1, "only_target": 0, "diff": 0, "same": 99},
    }])
    # 两个 task 名相同 —— 在 proj-A 下 tasks_by_suffix 的"w1"会被 proj-B 同名覆盖
    # （后写的覆盖先写的），但走 project_id 过滤后只剩一个，命中确定的 task
    report = verify_scenario(s, project_id="proj-A")
    r = report.results[0]
    # 没 history → no_run，但 task_id 应对应 proj-A 的 task
    assert r.status == "no_run"
    from app.services.repositories import task_store
    proj_a_task = next(t for t in task_store.list() if t.project_id == "proj-A")
    assert r.task_id == proj_a_task.id


# ─── /api/scenarios/{id}/verify endpoint ────────────────────────────────────


@pytest.fixture
def client_with_scenario(client_admin, isolated_storage, monkeypatch):
    """把 example.yml 复制进 isolated SCENARIOS_DIR + admin token（scenarios admin only）。"""
    from app.utils.paths import BASE_DIR
    from app.api import scenarios as api_module
    from app.scenarios import loader as loader_module
    from app.utils import paths as paths_module
    sdir = isolated_storage["cfg"] / "scenarios"
    sdir.mkdir()
    src = BASE_DIR / "config" / "scenarios" / "orders-recon.example.yml"
    sdir.joinpath("orders-recon.example.yml").write_text(
        src.read_text(encoding="utf-8"), encoding="utf-8"
    )
    monkeypatch.setattr(paths_module, "SCENARIOS_DIR", sdir)
    monkeypatch.setattr(loader_module, "SCENARIOS_DIR", sdir)
    monkeypatch.setattr(api_module, "SCENARIOS_DIR", sdir)
    return client_admin


def test_endpoint_returns_no_task_when_nothing_recorded(client_with_scenario):
    r = client_with_scenario.get("/api/scenarios/orders-recon-mvp/verify")
    assert r.status_code == 200, r.text
    body = r.json()
    # example.yml 1 个 compare_task 但还没 record → no_task
    assert body["summary"]["skipped"] == 1
    assert body["results"][0]["status"] == "no_task"


def test_endpoint_pass_after_record_and_run(client_with_scenario, isolated_storage):
    # 先模拟 recorder 跑过：创建 CompareTask 名字按 recorder 规则
    task_id = _create_compare_task("orders-recon-mvp · orders-recon-daily")
    # 再模拟跑出和 expected 完全一致的 summary
    _make_history_run(
        isolated_storage["results"], run_id="run-x", task_id=task_id,
        summary={"only_source": 20, "only_target": 5, "diff": 10, "same": 965},
    )
    r = client_with_scenario.get("/api/scenarios/orders-recon-mvp/verify")
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["pass"] == 1
    assert body["results"][0]["status"] == "pass"


def test_endpoint_scenario_not_found_404(client_with_scenario):
    r = client_with_scenario.get("/api/scenarios/no-such/verify")
    assert r.status_code == 404
