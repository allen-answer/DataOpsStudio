"""切片 C 新增端点测试：

- GET /api/runs/{run_id}/meta
- GET /api/runs/{run_id}/buckets/{bucket}?offset=&limit=

覆盖 legacy json / parquet 两种格式 + 不存在 404 + 越权 403。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.compare.engine import CompareBuckets
from app.compare.result_writer import (
    JsonResultWriter,
    ParquetResultWriter,
    feed_buckets,
)
from app.models import CompareTaskCreate
from app.services.repositories import task_store


@pytest.fixture
def buckets() -> CompareBuckets:
    return {
        "only_source": [
            {"key": [i], "source": {"id": i, "name": f"u{i}"}}
            for i in range(5)
        ],
        "only_target": [],
        "diff": [
            {
                "key": [9], "source": {"id": 9, "v": 1}, "target": {"id": 9, "v": 2},
                "changes": {"v": {"source": 1, "target": 2, "target_column": "v"}},
            },
        ],
        "same": [
            {"key": [i], "source": {"id": i}, "target": {"id": i}}
            for i in range(10)
        ],
    }


def _make_envelope(task_id: str, run_id: str) -> dict:
    return {
        "run_id": run_id,
        "task_id": task_id,
        "task_name": "demo",
        "started_at": "2026-05-21T10:00:00",
        "elapsed_seconds": 1.0,
        "source_rows": 5,
        "target_rows": 5,
        "summary": {"only_source": 5, "only_target": 0, "diff": 1, "same": 10},
        "rules": {},
        "limits": {},
        "schema_report": {},
    }


def _create_task(name: str = "t1", project_id: str = "") -> str:
    """建一个最小可行 task（SQL 单端模式），返 id。runner 用不到，只为 task_id 反查。"""
    task = task_store.create(CompareTaskCreate(
        name=name, source_kind="sql", target_kind="sql",
        source_id="ds-x", target_id="ds-x", sql_mode="single",
        source_sql="SELECT 1 AS id", key_columns=["id"], project_id=project_id,
    ))
    return task.id


# ─── /meta ───────────────────────────────────────────────────────────────────


def test_meta_legacy_json_run(client, isolated_storage, buckets):
    task_id = _create_task()
    writer = JsonResultWriter(
        result_path=isolated_storage["results"] / "RUN_L.json",
        excel_path=isolated_storage["results"] / "RUN_L.xlsx",
        payload=_make_envelope(task_id, "RUN_L"),
    )
    feed_buckets(writer, buckets)
    writer.finalize()

    r = client.get("/api/runs/RUN_L/meta")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["format"] == "json"
    assert body["task_id"] == task_id
    by_name = {b["name"]: b for b in body["buckets"]}
    assert by_name["only_source"]["rows"] == 5
    assert by_name["same"]["rows"] == 10
    assert by_name["only_source"]["mode"] == "full"


def test_meta_parquet_run(client, isolated_storage, buckets):
    task_id = _create_task()
    writer = ParquetResultWriter(
        run_dir=isolated_storage["results"] / "RUN_P",
        excel_path=isolated_storage["results"] / "RUN_P.xlsx",
        payload=_make_envelope(task_id, "RUN_P"),
        same_sample_rows=3,
    )
    feed_buckets(writer, buckets)
    writer.finalize()

    r = client.get("/api/runs/RUN_P/meta")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["format"] == "parquet"
    by_name = {b["name"]: b for b in body["buckets"]}
    assert by_name["only_source"]["mode"] == "full"
    assert by_name["only_source"]["path"] == "only_source.parquet"
    assert by_name["same"]["mode"] == "count_only"
    assert len(by_name["same"]["sample"]) == 3


def test_meta_404_on_unknown_run(client, isolated_storage):
    r = client.get("/api/runs/no-such/meta")
    assert r.status_code == 404


# ─── /buckets/{bucket} ───────────────────────────────────────────────────────


def test_bucket_legacy_pagination(client, isolated_storage, buckets):
    task_id = _create_task()
    writer = JsonResultWriter(
        result_path=isolated_storage["results"] / "RUN_L.json",
        excel_path=isolated_storage["results"] / "RUN_L.xlsx",
        payload=_make_envelope(task_id, "RUN_L"),
    )
    feed_buckets(writer, buckets)
    writer.finalize()

    r = client.get("/api/runs/RUN_L/buckets/only_source?offset=0&limit=2")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 5
    assert body["mode"] == "full"
    assert [row["key"][0] for row in body["rows"]] == [0, 1]

    r = client.get("/api/runs/RUN_L/buckets/only_source?offset=4&limit=10")
    assert [row["key"][0] for row in r.json()["rows"]] == [4]


def test_bucket_parquet_full(client, isolated_storage, buckets):
    task_id = _create_task()
    writer = ParquetResultWriter(
        run_dir=isolated_storage["results"] / "RUN_P",
        excel_path=isolated_storage["results"] / "RUN_P.xlsx",
        payload=_make_envelope(task_id, "RUN_P"),
    )
    feed_buckets(writer, buckets)
    writer.finalize()

    r = client.get("/api/runs/RUN_P/buckets/only_source?limit=3")
    body = r.json()
    assert body["total"] == 5
    assert body["mode"] == "full"
    assert len(body["rows"]) == 3


def test_bucket_parquet_same_count_only(client, isolated_storage, buckets):
    task_id = _create_task()
    writer = ParquetResultWriter(
        run_dir=isolated_storage["results"] / "RUN_P",
        excel_path=isolated_storage["results"] / "RUN_P.xlsx",
        payload=_make_envelope(task_id, "RUN_P"),
        same_sample_rows=4,
    )
    feed_buckets(writer, buckets)
    writer.finalize()

    r = client.get("/api/runs/RUN_P/buckets/same?limit=10")
    body = r.json()
    assert body["total"] == 10            # 真实 count
    assert body["mode"] == "count_only"
    assert len(body["rows"]) == 4         # sample 上限


def test_bucket_invalid_name_400(client, isolated_storage, buckets):
    task_id = _create_task()
    writer = JsonResultWriter(
        result_path=isolated_storage["results"] / "RUN_L.json",
        excel_path=isolated_storage["results"] / "RUN_L.xlsx",
        payload=_make_envelope(task_id, "RUN_L"),
    )
    feed_buckets(writer, buckets)
    writer.finalize()

    r = client.get("/api/runs/RUN_L/buckets/garbage")
    assert r.status_code == 400, r.text
    assert "unknown bucket" in r.json()["detail"]


def test_bucket_404_on_unknown_run(client, isolated_storage):
    r = client.get("/api/runs/no-such/buckets/only_source")
    assert r.status_code == 404


# ─── 项目级授权 ─────────────────────────────────────────────────────────────


def test_meta_403_cross_project(client_editor, client_admin, isolated_storage, buckets):
    """editor 不能读自己无权项目的 run meta / bucket。"""
    # 用 admin 建 ProjectB + ProjectB 下的 task；editor 默认不属任何项目
    proj = client_admin.post("/api/projects", json={"name": "ProjectB", "members": []}).json()
    task_id = _create_task("t-projB", project_id=proj["id"])

    writer = JsonResultWriter(
        result_path=isolated_storage["results"] / "RUN_B.json",
        excel_path=isolated_storage["results"] / "RUN_B.xlsx",
        payload=_make_envelope(task_id, "RUN_B"),
    )
    feed_buckets(writer, buckets)
    writer.finalize()

    # admin 能读
    assert client_admin.get("/api/runs/RUN_B/meta").status_code == 200
    # editor 无权 ProjectB → 403
    r = client_editor.get("/api/runs/RUN_B/meta")
    assert r.status_code == 403, r.text
    r2 = client_editor.get("/api/runs/RUN_B/buckets/only_source")
    assert r2.status_code == 403
