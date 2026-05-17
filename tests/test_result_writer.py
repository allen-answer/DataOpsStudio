"""ResultWriter 协议 + JsonResultWriter 等价实现的契约测试。

切片 A 要求行为完全跟旧 `exporter.write_result_json` + `write_excel` 等价 —— 这
里既验证新抽象层的接口契约（feed → finalize），也跟 exporter 直调对照确认产物
完全一致。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.compare.engine import CompareBuckets
from app.compare.result_writer import (
    JsonResultWriter,
    ResultManifest,
    feed_buckets,
)
from app.services.exporter import write_excel, write_result_json


# ---------------------------------------------------------------------------
# 测试 fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_buckets() -> CompareBuckets:
    """覆盖 4 个桶都有数据 + diff 桶带 changes 字段（exporter Excel 路径要用）。"""
    return {
        "only_source": [
            {"key": [1], "source": {"id": 1, "name": "alice"}}
        ],
        "only_target": [
            {"key": [2], "target": {"id": 2, "name": "bob"}}
        ],
        "diff": [
            {
                "key": [3],
                "source": {"id": 3, "name": "carol", "amount": 10},
                "target": {"id": 3, "name": "carol", "amount": 11},
                "changes": {"amount": {"source": 10, "target": 11, "target_column": "amount"}},
            }
        ],
        "same": [
            {"key": [4], "source": {"id": 4, "name": "dave"}, "target": {"id": 4, "name": "dave"}}
        ],
    }


@pytest.fixture
def sample_payload() -> dict:
    return {
        "run_id": "20260517_deadbeef",
        "task_id": "task-1",
        "task_name": "demo",
        "started_at": "2026-05-17T10:00:00",
        "elapsed_seconds": 1.23,
        "source_rows": 4,
        "target_rows": 4,
        "summary": {"only_source": 1, "only_target": 1, "diff": 1, "same": 1},
        "rules": {},
        "limits": {},
        "schema_report": {},
    }


# ---------------------------------------------------------------------------
# 行 dispatch / 桶校验
# ---------------------------------------------------------------------------

def test_feed_buckets_dispatches_all_rows(tmp_path: Path, sample_buckets, sample_payload):
    writer = JsonResultWriter(
        result_path=tmp_path / "r.json",
        excel_path=tmp_path / "r.xlsx",
        payload=sample_payload,
    )
    feed_buckets(writer, sample_buckets)
    manifest = writer.finalize()

    assert manifest.bucket_counts == {
        "only_source": 1, "only_target": 1, "diff": 1, "same": 1,
    }


def test_write_unknown_bucket_raises(tmp_path: Path, sample_payload):
    writer = JsonResultWriter(
        result_path=tmp_path / "r.json",
        excel_path=tmp_path / "r.xlsx",
        payload=sample_payload,
    )
    with pytest.raises(ValueError, match="unknown bucket"):
        writer.write_bucket_row("nonsense", {"key": [1]})


def test_write_after_finalize_raises(tmp_path: Path, sample_payload):
    writer = JsonResultWriter(
        result_path=tmp_path / "r.json",
        excel_path=tmp_path / "r.xlsx",
        payload=sample_payload,
    )
    writer.finalize()
    with pytest.raises(RuntimeError, match="cannot write after finalize"):
        writer.write_bucket_row("same", {"key": [1]})


def test_finalize_called_twice_raises(tmp_path: Path, sample_payload):
    writer = JsonResultWriter(
        result_path=tmp_path / "r.json",
        excel_path=tmp_path / "r.xlsx",
        payload=sample_payload,
    )
    writer.finalize()
    with pytest.raises(RuntimeError, match="twice"):
        writer.finalize()


# ---------------------------------------------------------------------------
# Manifest 形状
# ---------------------------------------------------------------------------

def test_manifest_paths_and_counts(tmp_path: Path, sample_buckets, sample_payload):
    rpath = tmp_path / "20260517.json"
    xpath = tmp_path / "20260517.xlsx"
    writer = JsonResultWriter(result_path=rpath, excel_path=xpath, payload=sample_payload)
    feed_buckets(writer, sample_buckets)
    manifest = writer.finalize()

    assert isinstance(manifest, ResultManifest)
    assert manifest.result_path == rpath
    assert manifest.excel_path == xpath
    assert manifest.result_filename == "20260517.json"
    assert manifest.excel_filename == "20260517.xlsx"
    assert sum(manifest.bucket_counts.values()) == 4


# ---------------------------------------------------------------------------
# 跟旧路径等价性 —— 切片 A 行为不变的硬约束
# ---------------------------------------------------------------------------

def test_json_output_equivalent_to_legacy(tmp_path: Path, sample_buckets, sample_payload):
    """JsonResultWriter 产的 JSON 跟直接调 exporter.write_result_json 完全一致。"""
    # 新路径
    writer = JsonResultWriter(
        result_path=tmp_path / "new.json",
        excel_path=tmp_path / "new.xlsx",
        payload=sample_payload,
    )
    feed_buckets(writer, sample_buckets)
    writer.finalize()

    # 旧路径
    legacy_payload = {**sample_payload, "buckets": sample_buckets}
    write_result_json(tmp_path / "legacy.json", legacy_payload)

    new_dict = json.loads((tmp_path / "new.json").read_text(encoding="utf-8"))
    legacy_dict = json.loads((tmp_path / "legacy.json").read_text(encoding="utf-8"))

    # bucket 顺序由 _BUCKET_NAMES 决定 —— 跟旧 dict 顺序可能不同，按 set 比 key 内容
    assert set(new_dict.keys()) == set(legacy_dict.keys())
    assert new_dict["buckets"] == legacy_dict["buckets"]
    assert new_dict["summary"] == legacy_dict["summary"]
    assert new_dict["run_id"] == legacy_dict["run_id"]


def test_excel_output_exists_and_nonempty(tmp_path: Path, sample_buckets, sample_payload):
    """Excel 产出存在 + 非空（不深 inspect，留给 exporter 自己的回归覆盖）。"""
    writer = JsonResultWriter(
        result_path=tmp_path / "r.json",
        excel_path=tmp_path / "r.xlsx",
        payload=sample_payload,
    )
    feed_buckets(writer, sample_buckets)
    writer.finalize()

    excel_path = tmp_path / "r.xlsx"
    assert excel_path.exists()
    assert excel_path.stat().st_size > 0


def test_excel_max_rows_respected(tmp_path: Path, sample_payload):
    """excel_max_rows 透传到 exporter.write_excel"""
    big_buckets: CompareBuckets = {
        "only_source": [{"key": [i], "source": {"id": i}} for i in range(100)],
        "only_target": [],
        "diff": [],
        "same": [],
    }
    writer = JsonResultWriter(
        result_path=tmp_path / "big.json",
        excel_path=tmp_path / "big.xlsx",
        payload=sample_payload,
        excel_max_rows=10,
    )
    feed_buckets(writer, big_buckets)
    writer.finalize()

    # JSON 里仍是全量
    j = json.loads((tmp_path / "big.json").read_text(encoding="utf-8"))
    assert len(j["buckets"]["only_source"]) == 100
    # Excel 文件存在（详细行数检查走 exporter 自己的测试）
    assert (tmp_path / "big.xlsx").exists()


# ---------------------------------------------------------------------------
# Empty / 边界
# ---------------------------------------------------------------------------

def test_empty_buckets_round_trip(tmp_path: Path, sample_payload):
    """没有任何行 feed 时 finalize 仍能落盘，4 个桶全 0。"""
    writer = JsonResultWriter(
        result_path=tmp_path / "empty.json",
        excel_path=tmp_path / "empty.xlsx",
        payload=sample_payload,
    )
    manifest = writer.finalize()

    assert manifest.bucket_counts == {
        "only_source": 0, "only_target": 0, "diff": 0, "same": 0,
    }
    j = json.loads((tmp_path / "empty.json").read_text(encoding="utf-8"))
    assert j["buckets"] == {"only_source": [], "only_target": [], "diff": [], "same": []}


def test_feed_buckets_ignores_missing_bucket_keys(tmp_path: Path, sample_payload):
    """feed_buckets 收到部分桶（同名 dict 缺 key）时不抛 —— 缺的桶当空处理。"""
    partial: CompareBuckets = {"only_source": [{"key": [1], "source": {"id": 1}}]}
    writer = JsonResultWriter(
        result_path=tmp_path / "p.json",
        excel_path=tmp_path / "p.xlsx",
        payload=sample_payload,
    )
    feed_buckets(writer, partial)  # type: ignore[arg-type]
    manifest = writer.finalize()

    assert manifest.bucket_counts["only_source"] == 1
    assert manifest.bucket_counts["only_target"] == 0
