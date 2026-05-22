"""services/history.py：compare 任务历史结果列表测试。

历史 perf 优化：limit 设了走 mtime DESC 预排，只读 limit*2+10 个 JSON，再按
sort_time 二次排序保语义正确。limit=None 时仍读全量保兼容。

切片 C：list_result_history 同时扫 RESULTS_DIR/*.json（legacy）+ RESULTS_DIR/*/meta.json
（parquet）；后者 result_filename 走 `<run_id>/meta.json`，format 字段标 "parquet"。
"""
from __future__ import annotations

import json


def test_list_history_returns_all_when_no_limit(isolated_storage):
    """不传 limit 时仍返回全部条目（向后兼容）。"""
    from app.services.history import list_result_history
    results_dir = isolated_storage["results"]
    for i in range(5):
        (results_dir / f"r{i:02d}.json").write_text(
            json.dumps({"run_id": f"r{i:02d}", "task_id": "", "started_at": f"2026-05-0{i+1}T10:00:00"}),
            encoding="utf-8",
        )
    items = list_result_history()
    assert len(items) == 5


def test_list_history_limit_truncates_after_sort(isolated_storage):
    """limit 设 3 时返回最新 3 条（按 sort_time DESC）。"""
    from app.services.history import list_result_history
    results_dir = isolated_storage["results"]
    for i in range(10):
        (results_dir / f"r{i:02d}.json").write_text(
            json.dumps({"run_id": f"r{i:02d}", "task_id": "",
                        "started_at": f"2026-05-{i+1:02d}T10:00:00"}),
            encoding="utf-8",
        )
    items = list_result_history(limit=3)
    assert len(items) == 3
    # 按 sort_time DESC，最大的几个先
    assert [it["run_id"] for it in items] == ["r09", "r08", "r07"]


def test_list_history_limit_bounds_json_reads(isolated_storage, monkeypatch):
    """50 个 history 但 limit=10 时不应读全量 JSON —— 跟 list_workflow_runs 同套路。"""
    from app.services import history as history_svc
    results_dir = isolated_storage["results"]
    for i in range(50):
        (results_dir / f"r{i:02d}.json").write_text(
            json.dumps({"run_id": f"r{i:02d}", "task_id": "",
                        "started_at": f"2026-05-{(i % 28) + 1:02d}T10:{i:02d}:00"}),
            encoding="utf-8",
        )

    read_count = {"n": 0}
    real_read = history_svc.Path.read_text

    def counting_read(self, *args, **kwargs):
        read_count["n"] += 1
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(history_svc.Path, "read_text", counting_read)
    items = history_svc.list_result_history(limit=10)
    assert len(items) == 10
    # 50 个文件，limit=10 时 read_budget=2*10+10=30，读取数 <= 30
    assert read_count["n"] <= 30, f"read 太多 JSON：{read_count['n']}（应 ≤ 30）"


def test_list_history_respects_started_at_when_mtime_diverges(isolated_storage):
    """长任务：started_at 早但 mtime 晚（落盘很晚）—— 按 sort_time（started_at）返回。"""
    import os
    from app.services.history import list_result_history
    results_dir = isolated_storage["results"]
    # A：started_at 较新（5/2），mtime 调到 2023
    (results_dir / "r-A.json").write_text(
        json.dumps({"run_id": "r-A", "task_id": "", "started_at": "2026-05-02T10:00:00"}),
        encoding="utf-8",
    )
    early = 1_700_000_000
    os.utime(results_dir / "r-A.json", (early, early))
    # B：started_at 较旧（4/30），mtime 是现在（最新）
    (results_dir / "r-B.json").write_text(
        json.dumps({"run_id": "r-B", "task_id": "", "started_at": "2026-04-30T10:00:00"}),
        encoding="utf-8",
    )
    items = list_result_history(limit=10)
    # mtime: B 新 → A 旧；started_at: A 新 → B 旧 —— 最终按 started_at
    assert [it["run_id"] for it in items] == ["r-A", "r-B"]


def test_list_history_filter_by_task_id_with_limit(isolated_storage):
    """task_id 过滤跟 limit 协作：返回最新 N 条匹配项。"""
    from app.services.history import list_result_history
    results_dir = isolated_storage["results"]
    for i in range(10):
        (results_dir / f"r{i:02d}.json").write_text(
            json.dumps({
                "run_id": f"r{i:02d}",
                "task_id": "task-A" if i % 2 == 0 else "task-B",
                "started_at": f"2026-05-{i+1:02d}T10:00:00",
            }),
            encoding="utf-8",
        )
    items_a = list_result_history(task_id="task-A", limit=10)
    assert all(it["task_id"] == "task-A" for it in items_a)
    assert len(items_a) == 5  # 只有 5 个匹配


# ─── 切片 C：list_result_history 兼容 parquet runs ───────────────────────────


def _make_parquet_meta(results_dir, run_id: str, task_id: str = "", started_at: str = "") -> None:
    """模拟一个 parquet run：建 <run_id>/meta.json（不需要真 parquet 文件，
    list_result_history 只读 meta.json 取 summary）。"""
    run_dir = results_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "run_id": run_id, "task_id": task_id,
        "task_name": "t",
        "started_at": started_at or "2026-05-01T10:00:00",
        "summary": {"only_source": 0, "only_target": 0, "diff": 0, "same": 0},
        "buckets": [],
        "format": "parquet",
        "format_version": 1,
    }
    (run_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")


def test_list_history_picks_up_parquet_runs(isolated_storage):
    """legacy json + parquet dir 同存时两类都列出。"""
    from app.services.history import list_result_history
    results_dir = isolated_storage["results"]
    # 2 个 legacy json
    for i in range(2):
        (results_dir / f"legacy_r{i}.json").write_text(
            json.dumps({"run_id": f"legacy_r{i}", "task_id": "",
                        "started_at": f"2026-05-0{i+1}T10:00:00",
                        "summary": {"only_source": 0, "only_target": 0, "diff": 0, "same": 0}}),
            encoding="utf-8",
        )
    # 3 个 parquet 目录
    for i in range(3):
        _make_parquet_meta(results_dir, f"parq_r{i}", started_at=f"2026-05-1{i}T10:00:00")

    items = list_result_history()
    by_id = {it["run_id"]: it for it in items}
    assert "legacy_r0" in by_id and "legacy_r1" in by_id
    assert "parq_r0" in by_id and "parq_r1" in by_id and "parq_r2" in by_id

    # parquet 的 result_filename 是目录化路径 + format 标 parquet
    assert by_id["parq_r0"]["result_filename"] == "parq_r0/meta.json"
    assert by_id["parq_r0"]["format"] == "parquet"
    # legacy 的 result_filename 仍是文件名 + format 标 json
    assert by_id["legacy_r0"]["result_filename"] == "legacy_r0.json"
    assert by_id["legacy_r0"]["format"] == "json"


def test_list_history_filter_by_task_id_covers_both_formats(isolated_storage):
    from app.services.history import list_result_history
    results_dir = isolated_storage["results"]
    (results_dir / "L1.json").write_text(
        json.dumps({"run_id": "L1", "task_id": "tA",
                    "started_at": "2026-05-01T10:00:00",
                    "summary": {"only_source": 0, "only_target": 0, "diff": 0, "same": 0}}),
        encoding="utf-8",
    )
    _make_parquet_meta(results_dir, "P1", task_id="tA")
    _make_parquet_meta(results_dir, "P2", task_id="tB")

    items = list_result_history(task_id="tA")
    ids = {it["run_id"] for it in items}
    assert ids == {"L1", "P1"}


# ─── P1：list_result_history offset 标准分页 ────────────────────────────────


def test_list_history_offset_basic_slice(isolated_storage):
    """20 条 history + limit=5 + offset=5 返回第 6-10 条（按 sort_time DESC）。"""
    from app.services.history import list_result_history
    results_dir = isolated_storage["results"]
    for i in range(20):
        (results_dir / f"r{i:02d}.json").write_text(
            json.dumps({"run_id": f"r{i:02d}", "task_id": "",
                        "started_at": f"2026-05-{i+1:02d}T10:00:00"}),
            encoding="utf-8",
        )
    page1 = list_result_history(limit=5, offset=0)
    page2 = list_result_history(limit=5, offset=5)
    page3 = list_result_history(limit=5, offset=10)
    # 整体按 started_at DESC：page1=[r19..r15], page2=[r14..r10], page3=[r09..r05]
    assert [it["run_id"] for it in page1] == ["r19", "r18", "r17", "r16", "r15"]
    assert [it["run_id"] for it in page2] == ["r14", "r13", "r12", "r11", "r10"]
    assert [it["run_id"] for it in page3] == ["r09", "r08", "r07", "r06", "r05"]


def test_list_history_offset_overshoot_returns_empty(isolated_storage):
    """offset 超出总数 → 空 list，不抛。"""
    from app.services.history import list_result_history
    (isolated_storage["results"] / "r.json").write_text(
        json.dumps({"run_id": "r", "task_id": ""}), encoding="utf-8",
    )
    assert list_result_history(limit=10, offset=100) == []


def test_list_history_offset_default_zero(isolated_storage):
    """没传 offset 时跟前 PR 行为完全一致。"""
    from app.services.history import list_result_history
    for i in range(5):
        (isolated_storage["results"] / f"r{i}.json").write_text(
            json.dumps({"run_id": f"r{i}", "task_id": "",
                        "started_at": f"2026-05-0{i+1}T10:00:00"}),
            encoding="utf-8",
        )
    items = list_result_history(limit=3)  # 默认 offset=0
    assert [it["run_id"] for it in items] == ["r4", "r3", "r2"]


def test_list_history_offset_endpoint(client, isolated_storage):
    """端点透传 offset query —— 拼到 service call。"""
    for i in range(10):
        (isolated_storage["results"] / f"r{i}.json").write_text(
            json.dumps({"run_id": f"r{i}", "task_id": "",
                        "started_at": f"2026-05-{i+1:02d}T10:00:00"}),
            encoding="utf-8",
        )
    r = client.get("/api/history?limit=3&offset=3")
    assert r.status_code == 200, r.text
    body = r.json()
    assert [it["run_id"] for it in body] == ["r6", "r5", "r4"]


def test_list_history_offset_endpoint_rejects_negative(client, isolated_storage):
    """offset < 0 应该被 FastAPI Query(ge=0) 拒掉。"""
    r = client.get("/api/history?limit=10&offset=-1")
    assert r.status_code == 422
