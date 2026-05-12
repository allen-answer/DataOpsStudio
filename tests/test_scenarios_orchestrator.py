"""Scenario one-shot orchestrator tests（Phase 12 切片 11）。

scope: `run_all` 6 步串联
- materialize 失败 → 短路 + ok=False
- materialize 成功 + 所有 task run 成功 + verify pass → ok=True
- materialize 成功 + 某个 task run 抛错 → 继续，整体 ok=False
- verify fail（actual ≠ expected）→ ok=False 即使 run 全成
- ai_fill 流通到下游 scenario
- /api/scenarios/{id}/run-all endpoint smoke test
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.models.compare import CompareResult, CompareSummary
from app.scenarios.models import Scenario
from app.scenarios.orchestrator import run_all


def _scenario(**kwargs: Any) -> Scenario:
    payload = {"id": "sc-test", "name": "T", "seed": 42}
    payload.update(kwargs)
    return Scenario.model_validate(payload)


def _two_table_compare(*, expected: dict[str, int] | None = None) -> Scenario:
    """canonical fixture: 1 source / 1 target / 1 compare_task workload。"""
    workload: dict[str, Any] = {
        "kind": "compare_task", "name": "w1",
        "source": "ods.t", "target": "dwd.t", "keys": ["id"],
    }
    if expected is not None:
        workload["expected"] = expected
    return _scenario(
        tables=[
            {"name": "ods.t", "role": "source", "rows": 10,
             "columns": [
                 {"name": "id", "type": "INT", "pk": True, "gen": "sequence"},
                 {"name": "x", "type": "INT", "gen": "constant", "values": [1]},
             ]},
            {"name": "dwd.t", "role": "target", "rows": 10, "derives_from": "ods.t"},
        ],
        workloads=[workload],
    )


def _mock_materialize_ok(monkeypatch):
    """让 materialize_to_datasource 当成功不接真 DB。"""
    from app.scenarios import orchestrator as orc
    def fake(scenario, data, datasource_id, *, drop_first, batch_size):
        return {
            "dialect": "mysql",
            "schemas_created": [],
            "tables": [{"name": t, "rows_inserted": len(rows)} for t, rows in data.items()],
            "warnings": [],
        }
    monkeypatch.setattr(orc, "materialize_to_datasource", fake)


def _mock_run_task_with_summary(monkeypatch, isolated_storage, summary: dict[str, int]):
    """mock runner.run_task：返回 CompareResult + 同时落 history JSON 让 verifier 能查到。"""
    from app.services import runner as runner_mod

    def fake(task_id, status_callback=None):
        run_id = f"run-{task_id[:8]}"
        # 写一份 history 让 verifier 找到
        (isolated_storage["results"] / f"{run_id}.json").write_text(json.dumps({
            "run_id": run_id,
            "task_id": task_id,
            "task_name": "t",
            "started_at": "2026-05-12T10:00:00",
            "elapsed_seconds": 0.1,
            "summary": summary,
        }), encoding="utf-8")
        return CompareResult(
            run_id=run_id, task_id=task_id,
            summary=CompareSummary(**summary),
            result_path="", result_filename="",
            excel_path="", excel_filename="",
            samples={"only_source": [], "only_target": [], "diff": [], "same": []},
        )

    monkeypatch.setattr(runner_mod, "run_task", fake)


# ─── 短路：materialize 失败 ────────────────────────────────────────────────


def test_materialize_failure_short_circuits(isolated_storage, monkeypatch):
    from app.scenarios import orchestrator as orc
    from app.scenarios.runtime import ScenarioRuntimeError

    def fake(*a, **kw):
        raise ScenarioRuntimeError("datasource not found: nope")

    monkeypatch.setattr(orc, "materialize_to_datasource", fake)

    s = _two_table_compare()
    report = run_all(s, datasource_id="nope")
    assert report.ok is False
    assert "datasource not found" in report.error
    assert report.materialize is None
    assert report.record == {}  # 后续步骤短路
    assert report.runs == []
    assert report.verify is None


# ─── happy path ────────────────────────────────────────────────────────────


def test_happy_path_pipeline_ok(isolated_storage, monkeypatch):
    _mock_materialize_ok(monkeypatch)
    _mock_run_task_with_summary(monkeypatch, isolated_storage,
                                summary={"only_source": 1, "only_target": 0, "diff": 0, "same": 9})

    s = _two_table_compare(expected={"only_source": 1, "only_target": 0, "diff": 0, "same": 9})
    report = run_all(s, datasource_id="ds-1")
    assert report.ok is True
    assert report.error == ""
    assert report.materialize is not None
    assert report.materialize["rows_generated"]["ods.t"] == 10
    assert len(report.record["tasks"]) == 1
    assert len(report.runs) == 1
    assert report.runs[0].ok is True
    assert report.verify["summary"] == {"pass": 1, "fail": 0, "skipped": 0}


# ─── partial：某个 run 抛错 ────────────────────────────────────────────────


def test_run_task_failure_does_not_short_circuit(isolated_storage, monkeypatch):
    _mock_materialize_ok(monkeypatch)

    from app.services import runner as runner_mod

    def fake_run(task_id, status_callback=None):
        raise RuntimeError("compare engine crashed")

    monkeypatch.setattr(runner_mod, "run_task", fake_run)

    s = _two_table_compare(expected={"only_source": 0, "only_target": 0, "diff": 0, "same": 10})
    report = run_all(s, datasource_id="ds-1")
    assert report.ok is False  # 整体 fail
    assert len(report.runs) == 1
    assert report.runs[0].ok is False
    assert "compare engine crashed" in report.runs[0].error
    # verify 跑了 —— 但 no_run 因为没 history
    assert report.verify is not None
    assert report.verify["summary"]["skipped"] == 1


# ─── verify fail：actual ≠ expected ────────────────────────────────────────


def test_verify_fail_marks_overall_not_ok(isolated_storage, monkeypatch):
    _mock_materialize_ok(monkeypatch)
    # run 成功，但 summary 与 expected 不一致
    _mock_run_task_with_summary(monkeypatch, isolated_storage,
                                summary={"only_source": 5, "only_target": 0, "diff": 0, "same": 5})

    s = _two_table_compare(expected={"only_source": 1, "only_target": 0, "diff": 0, "same": 9})
    report = run_all(s, datasource_id="ds-1")
    assert report.runs[0].ok is True  # run 本身成功
    assert report.verify["summary"]["fail"] == 1
    assert report.ok is False  # 因为 verify fail，整体 ok=False


# ─── ai_fill 注入下游 scenario ────────────────────────────────────────────


def test_ai_fill_flows_into_generator(isolated_storage, monkeypatch):
    """ai_fill=True → fill_scenario 给 realistic 列写 values；下游 generate
    从 values 池里 pick；materialize 拿到的 data 应反映 AI 样本。"""
    _mock_materialize_ok(monkeypatch)
    _mock_run_task_with_summary(monkeypatch, isolated_storage,
                                summary={"only_source": 0, "only_target": 0, "diff": 0, "same": 5})

    from app.services import lineage_ai as svc
    from app.services.lineage_ai import LineageAIConfig
    monkeypatch.setattr(svc, "_config", lambda: LineageAIConfig(
        provider="openai", model="m", api_key="sk", base_url="https://x/v1",
    ))
    from app.api import ai_utils
    monkeypatch.setattr(ai_utils, "_call_ai", lambda *a, **kw: {"values": [99.99]})

    # scenario with ai.fill + realistic column
    s = _scenario(
        ai={"provider": "${default}", "fill": ["column_values"]},
        tables=[{
            "name": "t", "role": "source", "rows": 5,
            "columns": [
                {"name": "id", "type": "INT", "pk": True, "gen": "sequence"},
                {"name": "amount", "type": "DECIMAL(10,2)", "gen": "realistic"},
            ],
        }],
        workloads=[],
    )
    captured = {}
    from app.scenarios import orchestrator as orc

    def trace_materialize(scenario, data, ds_id, *, drop_first, batch_size):
        captured["amounts"] = {r["amount"] for r in data["t"]}
        return {"dialect": "mysql", "schemas_created": [],
                "tables": [{"name": "t", "rows_inserted": 5}], "warnings": []}

    monkeypatch.setattr(orc, "materialize_to_datasource", trace_materialize)

    report = run_all(s, datasource_id="ds-1", ai_fill=True)
    assert report.ai_fill is not None
    assert report.ai_fill["ok"] is True
    assert report.ai_fill["filled_columns"] == ["t.amount"]
    # generator 从 AI values 池抽，全部应该是 99.99
    assert captured["amounts"] == {99.99}


# ─── ai_fill 失败不中断 ────────────────────────────────────────────────────


def test_ai_fill_exception_continues(isolated_storage, monkeypatch):
    """ai_filler 整体抛错 → ai_fill 字段填错误，pipeline 继续。"""
    _mock_materialize_ok(monkeypatch)
    _mock_run_task_with_summary(monkeypatch, isolated_storage,
                                summary={"only_source": 0, "only_target": 0, "diff": 0, "same": 5})

    from app.scenarios import orchestrator as orc

    def fake_fill(scenario, **kw):
        raise RuntimeError("LLM provider blew up")

    monkeypatch.setattr(orc, "fill_scenario", fake_fill)

    s = _two_table_compare()
    report = run_all(s, datasource_id="ds-1", ai_fill=True)
    assert report.ai_fill is not None
    assert report.ai_fill["ok"] is False
    assert "LLM provider blew up" in report.ai_fill["errors"][0]
    # pipeline 仍然完成
    assert report.materialize is not None
    assert len(report.runs) == 1


# ─── /api/scenarios/{id}/run-all endpoint ──────────────────────────────────


@pytest.fixture
def client_with_scenario(isolated_storage, monkeypatch):
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
    from main import app
    return TestClient(app)


def test_endpoint_smoke(client_with_scenario, isolated_storage, monkeypatch):
    _mock_materialize_ok(monkeypatch)
    _mock_run_task_with_summary(
        monkeypatch, isolated_storage,
        summary={"only_source": 20, "only_target": 5, "diff": 10, "same": 965},
    )
    r = client_with_scenario.post(
        "/api/scenarios/orders-recon-mvp/run-all",
        json={"datasource_id": "ds-1"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True  # example.yml expected 跟 mock summary 完全匹配
    assert body["verify"]["summary"]["pass"] == 1
    assert len(body["runs"]) == 1


def test_endpoint_scenario_not_found_404(client_with_scenario):
    r = client_with_scenario.post(
        "/api/scenarios/no-such/run-all",
        json={"datasource_id": "ds-1"},
    )
    assert r.status_code == 404


def test_endpoint_missing_datasource_id_rejected(client_with_scenario):
    r = client_with_scenario.post(
        "/api/scenarios/orders-recon-mvp/run-all",
        json={"datasource_id": ""},
    )
    assert r.status_code in (400, 422)
