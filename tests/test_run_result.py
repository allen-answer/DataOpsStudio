"""切片 C reader 双格式契约测试。

覆盖 services/run_result.py 的 detect_format / load_run_meta / read_bucket /
delete_run 四个入口，跟 ParquetResultWriter（切片 B）+ JsonResultWriter（切片 A）
来回 round-trip。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.compare.engine import CompareBuckets
from app.compare.result_writer import (
    JsonResultWriter,
    ParquetResultWriter,
    feed_buckets,
)


@pytest.fixture
def buckets() -> CompareBuckets:
    return {
        "only_source": [
            {"key": [1], "source": {"id": 1, "name": "alice"}},
            {"key": [2], "source": {"id": 2, "name": "bob"}},
            {"key": [3], "source": {"id": 3, "name": "carol"}},
        ],
        "only_target": [
            {"key": [9], "target": {"id": 9, "name": "tom"}},
        ],
        "diff": [
            {
                "key": [5],
                "source": {"id": 5, "amount": 10},
                "target": {"id": 5, "amount": 11},
                "changes": {"amount": {"source": 10, "target": 11, "target_column": "amount"}},
            },
        ],
        "same": [
            {"key": [i], "source": {"id": i}, "target": {"id": i}}
            for i in range(7)
        ],
    }


@pytest.fixture
def envelope() -> dict:
    return {
        "run_id": "TEST_RUN",
        "task_id": "task-x",
        "task_name": "demo",
        "started_at": "2026-05-21T10:00:00",
        "elapsed_seconds": 1.0,
        "source_rows": 10,
        "target_rows": 8,
        "summary": {"only_source": 3, "only_target": 1, "diff": 1, "same": 7},
        "rules": {},
        "limits": {},
        "schema_report": {},
    }


def _patch_results_dir(monkeypatch, tmp_path: Path) -> Path:
    """把 RESULTS_DIR 切到 tmp。两个模块各自 import 顶层 RESULTS_DIR 引用都要 patch。"""
    from app.utils import paths as paths_module
    from app.services import run_result as rr_module
    monkeypatch.setattr(paths_module, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(rr_module, "RESULTS_DIR", tmp_path)
    return tmp_path


# ─── detect_format ───────────────────────────────────────────────────────────


def test_detect_missing(tmp_path, monkeypatch):
    _patch_results_dir(monkeypatch, tmp_path)
    from app.services.run_result import detect_format
    assert detect_format("nonexistent") == "missing"


def test_detect_legacy_json(tmp_path, monkeypatch, envelope, buckets):
    _patch_results_dir(monkeypatch, tmp_path)
    writer = JsonResultWriter(
        result_path=tmp_path / "TEST_RUN.json",
        excel_path=tmp_path / "TEST_RUN.xlsx",
        payload=envelope,
    )
    feed_buckets(writer, buckets)
    writer.finalize()

    from app.services.run_result import detect_format
    assert detect_format("TEST_RUN") == "json"


def test_detect_parquet(tmp_path, monkeypatch, envelope, buckets):
    _patch_results_dir(monkeypatch, tmp_path)
    writer = ParquetResultWriter(
        run_dir=tmp_path / "TEST_RUN",
        excel_path=tmp_path / "TEST_RUN.xlsx",
        payload=envelope,
    )
    feed_buckets(writer, buckets)
    writer.finalize()

    from app.services.run_result import detect_format
    assert detect_format("TEST_RUN") == "parquet"


# ─── load_run_meta ───────────────────────────────────────────────────────────


def test_load_meta_legacy_synthesizes_buckets_list(tmp_path, monkeypatch, envelope, buckets):
    _patch_results_dir(monkeypatch, tmp_path)
    writer = JsonResultWriter(
        result_path=tmp_path / "TEST_RUN.json",
        excel_path=tmp_path / "TEST_RUN.xlsx",
        payload=envelope,
    )
    feed_buckets(writer, buckets)
    writer.finalize()

    from app.services.run_result import load_run_meta
    meta = load_run_meta("TEST_RUN")
    assert meta["format"] == "json"
    assert meta["task_id"] == "task-x"
    by_name = {b["name"]: b for b in meta["buckets"]}
    # legacy 每桶 mode=full + rows=count，无 path
    assert by_name["only_source"]["mode"] == "full"
    assert by_name["only_source"]["rows"] == 3
    assert by_name["only_source"]["path"] is None
    assert by_name["same"]["rows"] == 7


def test_load_meta_parquet_returns_meta_json(tmp_path, monkeypatch, envelope, buckets):
    _patch_results_dir(monkeypatch, tmp_path)
    writer = ParquetResultWriter(
        run_dir=tmp_path / "TEST_RUN",
        excel_path=tmp_path / "TEST_RUN.xlsx",
        payload=envelope,
        same_sample_rows=3,
    )
    feed_buckets(writer, buckets)
    writer.finalize()

    from app.services.run_result import load_run_meta
    meta = load_run_meta("TEST_RUN")
    assert meta["format"] == "parquet"
    by_name = {b["name"]: b for b in meta["buckets"]}
    assert by_name["only_source"]["mode"] == "full"
    assert by_name["only_source"]["path"] == "only_source.parquet"
    assert by_name["same"]["mode"] == "count_only"
    assert len(by_name["same"]["sample"]) == 3


def test_load_meta_missing_raises(tmp_path, monkeypatch):
    _patch_results_dir(monkeypatch, tmp_path)
    from app.services.run_result import RunNotFound, load_run_meta
    with pytest.raises(RunNotFound):
        load_run_meta("nope")


# ─── read_bucket ─────────────────────────────────────────────────────────────


def test_read_bucket_legacy_json_pagination(tmp_path, monkeypatch, envelope, buckets):
    _patch_results_dir(monkeypatch, tmp_path)
    writer = JsonResultWriter(
        result_path=tmp_path / "TEST_RUN.json",
        excel_path=tmp_path / "TEST_RUN.xlsx",
        payload=envelope,
    )
    feed_buckets(writer, buckets)
    writer.finalize()

    from app.services.run_result import read_bucket
    page = read_bucket("TEST_RUN", "only_source", offset=0, limit=2)
    assert page["total"] == 3
    assert page["mode"] == "full"
    assert [r["key"][0] for r in page["rows"]] == [1, 2]

    page2 = read_bucket("TEST_RUN", "only_source", offset=2, limit=2)
    assert [r["key"][0] for r in page2["rows"]] == [3]


def test_read_bucket_parquet_full_pagination(tmp_path, monkeypatch, envelope, buckets):
    _patch_results_dir(monkeypatch, tmp_path)
    writer = ParquetResultWriter(
        run_dir=tmp_path / "TEST_RUN",
        excel_path=tmp_path / "TEST_RUN.xlsx",
        payload=envelope,
    )
    feed_buckets(writer, buckets)
    writer.finalize()

    from app.services.run_result import read_bucket
    page = read_bucket("TEST_RUN", "only_source", offset=0, limit=2)
    assert page["total"] == 3
    assert page["mode"] == "full"
    assert [r["key"][0] for r in page["rows"]] == [1, 2]

    # offset = 2 取剩下 1 行
    page2 = read_bucket("TEST_RUN", "only_source", offset=2, limit=10)
    assert [r["key"][0] for r in page2["rows"]] == [3]

    # offset 超出 total 返空
    page3 = read_bucket("TEST_RUN", "only_source", offset=99, limit=10)
    assert page3["rows"] == []


def test_read_bucket_parquet_count_only_same(tmp_path, monkeypatch, envelope, buckets):
    _patch_results_dir(monkeypatch, tmp_path)
    writer = ParquetResultWriter(
        run_dir=tmp_path / "TEST_RUN",
        excel_path=tmp_path / "TEST_RUN.xlsx",
        payload=envelope,
        same_sample_rows=5,
    )
    feed_buckets(writer, buckets)
    writer.finalize()

    from app.services.run_result import read_bucket
    page = read_bucket("TEST_RUN", "same", offset=0, limit=10)
    assert page["total"] == 7         # 全量 count
    assert page["mode"] == "count_only"
    assert len(page["rows"]) == 5     # sample 上限 5


def test_read_bucket_parquet_empty_bucket(tmp_path, monkeypatch, envelope):
    """空桶不写 parquet 文件，read_bucket 仍能返空 rows + total=0。"""
    _patch_results_dir(monkeypatch, tmp_path)
    writer = ParquetResultWriter(
        run_dir=tmp_path / "TEST_RUN",
        excel_path=tmp_path / "TEST_RUN.xlsx",
        payload=envelope,
    )
    writer.finalize()  # 不 feed 任何行

    from app.services.run_result import read_bucket
    page = read_bucket("TEST_RUN", "only_source", offset=0, limit=10)
    assert page["rows"] == []
    assert page["total"] == 0


def test_read_bucket_unknown_raises(tmp_path, monkeypatch, envelope, buckets):
    _patch_results_dir(monkeypatch, tmp_path)
    writer = JsonResultWriter(
        result_path=tmp_path / "TEST_RUN.json",
        excel_path=tmp_path / "TEST_RUN.xlsx",
        payload=envelope,
    )
    feed_buckets(writer, buckets)
    writer.finalize()

    from app.services.run_result import read_bucket
    with pytest.raises(ValueError, match="unknown bucket"):
        read_bucket("TEST_RUN", "garbage", offset=0, limit=10)


def test_read_bucket_missing_run_raises(tmp_path, monkeypatch):
    _patch_results_dir(monkeypatch, tmp_path)
    from app.services.run_result import RunNotFound, read_bucket
    with pytest.raises(RunNotFound):
        read_bucket("nope", "only_source")


# ─── delete_run ──────────────────────────────────────────────────────────────


def test_delete_run_legacy(tmp_path, monkeypatch, envelope, buckets):
    _patch_results_dir(monkeypatch, tmp_path)
    writer = JsonResultWriter(
        result_path=tmp_path / "TEST_RUN.json",
        excel_path=tmp_path / "TEST_RUN.xlsx",
        payload=envelope,
    )
    feed_buckets(writer, buckets)
    writer.finalize()
    assert (tmp_path / "TEST_RUN.json").exists()

    from app.services.run_result import delete_run
    delete_run("TEST_RUN")
    assert not (tmp_path / "TEST_RUN.json").exists()
    assert not (tmp_path / "TEST_RUN.xlsx").exists()


def test_delete_run_parquet_rmtrees_dir(tmp_path, monkeypatch, envelope, buckets):
    _patch_results_dir(monkeypatch, tmp_path)
    writer = ParquetResultWriter(
        run_dir=tmp_path / "TEST_RUN",
        excel_path=tmp_path / "TEST_RUN.xlsx",
        payload=envelope,
    )
    feed_buckets(writer, buckets)
    writer.finalize()
    assert (tmp_path / "TEST_RUN" / "meta.json").exists()
    assert (tmp_path / "TEST_RUN" / "only_source.parquet").exists()

    from app.services.run_result import delete_run
    delete_run("TEST_RUN")
    assert not (tmp_path / "TEST_RUN").exists()


def test_delete_run_missing_raises(tmp_path, monkeypatch):
    _patch_results_dir(monkeypatch, tmp_path)
    from app.services.run_result import delete_run
    with pytest.raises(KeyError):
        delete_run("nope")


# ─── 切片 F.4：iter_bucket_rows 流式迭代器 ──────────────────────────────────


def test_iter_bucket_rows_legacy_yields_all(tmp_path, monkeypatch, envelope, buckets):
    """legacy json 路径：iter 全部行（max_rows=None 不截断）。"""
    _patch_results_dir(monkeypatch, tmp_path)
    writer = JsonResultWriter(
        result_path=tmp_path / "RUN.json",
        excel_path=tmp_path / "RUN.xlsx",
        payload=envelope,
    )
    feed_buckets(writer, buckets)
    writer.finalize()

    from app.services.run_result import iter_bucket_rows
    yielded = list(iter_bucket_rows("RUN", "only_source"))
    assert len(yielded) == 3
    assert [r["key"][0] for r in yielded] == [1, 2, 3]


def test_iter_bucket_rows_parquet_streams_with_iter_batches(tmp_path, monkeypatch, envelope):
    """parquet 路径：iter_batches → 一次性 yield 所有行，行内容来自 parquet。"""
    _patch_results_dir(monkeypatch, tmp_path)
    big = {
        "only_source": [{"key": [i], "source": {"id": i, "v": f"x{i}"}} for i in range(50)],
        "only_target": [], "diff": [], "same": [],
    }
    writer = ParquetResultWriter(
        run_dir=tmp_path / "RUN",
        excel_path=tmp_path / "RUN.xlsx",
        payload=envelope,
    )
    feed_buckets(writer, big)
    writer.finalize()

    from app.services.run_result import iter_bucket_rows
    rows = list(iter_bucket_rows("RUN", "only_source"))
    assert len(rows) == 50
    assert rows[0]["source"]["v"] == "x0"
    assert rows[-1]["source"]["v"] == "x49"


def test_iter_bucket_rows_respects_max_rows(tmp_path, monkeypatch, envelope):
    """max_rows 是硬上限，iter 到点 stop —— 后续 batch 不解码。"""
    _patch_results_dir(monkeypatch, tmp_path)
    big = {
        "only_source": [{"key": [i], "source": {"id": i}} for i in range(50)],
        "only_target": [], "diff": [], "same": [],
    }
    writer = ParquetResultWriter(
        run_dir=tmp_path / "RUN",
        excel_path=tmp_path / "RUN.xlsx",
        payload=envelope,
    )
    feed_buckets(writer, big)
    writer.finalize()

    from app.services.run_result import iter_bucket_rows
    rows = list(iter_bucket_rows("RUN", "only_source", max_rows=7))
    assert len(rows) == 7


def test_iter_bucket_rows_count_only_same_uses_sample(tmp_path, monkeypatch, envelope, buckets):
    """parquet same 桶 count_only —— iter yield meta.json sample。"""
    _patch_results_dir(monkeypatch, tmp_path)
    writer = ParquetResultWriter(
        run_dir=tmp_path / "RUN",
        excel_path=tmp_path / "RUN.xlsx",
        payload=envelope,
        same_sample_rows=2,
    )
    feed_buckets(writer, buckets)
    writer.finalize()

    from app.services.run_result import iter_bucket_rows
    rows = list(iter_bucket_rows("RUN", "same"))
    # buckets fixture 里 same 有 7 行，sample 上限 2
    assert len(rows) == 2


def test_iter_bucket_rows_is_generator(tmp_path, monkeypatch):
    """iter_bucket_rows 必须是 generator，否则失去"行级流式"语义。"""
    import types
    _patch_results_dir(monkeypatch, tmp_path)
    from app.services.run_result import iter_bucket_rows, RunNotFound
    gen = iter_bucket_rows("nope", "diff")
    assert isinstance(gen, types.GeneratorType)
    with pytest.raises(RunNotFound):
        list(gen)
