"""切片 G：runner stream_compare=True + result_format=parquet 真流式路径测试。

monkeypatch build_reader 返伪 reader（yield 内存里准备好的 sorted rows），
跑 run_task，验证：
1. parquet + stream_compare=True 路径不再调 compare_sorted_row_iterators
2. meta.json 落对了 bucket counts
3. same 桶默认 count_only，不写 same.parquet
4. diff / only_source / only_target parquet 可读
5. result_format=json + stream_compare=True 仍走老 buckets dict 路径
6. result_format=parquet + stream_compare=False（F.3 路径）不回归

不连真实 DB，纯内存 iter。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Iterator
from pathlib import Path

import pytest

from app.models import CompareTaskCreate, RunLimits
from app.services.repositories import task_store


@dataclass
class _FakeReader:
    """模拟 SqlReader 接口的内存 reader —— 同时支持 fetch_all + iter_rows。"""
    rows: list[dict[str, Any]]

    def fetch_all(
        self, *, max_rows=None, chunk_size=None, progress_callback=None,
    ) -> list[dict[str, Any]]:
        return list(self.rows)

    def iter_rows(
        self, *, max_rows=None, chunk_size=None, progress_callback=None,
    ) -> Iterator[dict[str, Any]]:
        for row in self.rows:
            yield row


def _patch_readers(monkeypatch, source_rows, target_rows):
    """monkeypatch runner.build_reader 让 source/target 都拿到 _FakeReader。"""
    from app.services import runner as runner_module
    src_reader = _FakeReader(source_rows)
    tgt_reader = _FakeReader(target_rows)

    def fake_build_reader(task, side):
        return src_reader if side == "source" else tgt_reader

    monkeypatch.setattr(runner_module, "build_reader", fake_build_reader)


def _make_task(*, result_format: str, stream_compare: bool) -> str:
    """建一个最小 task（SQL 双端，避开 single-mode 跨类型限制）。"""
    payload = CompareTaskCreate(
        name=f"streaming-{result_format}-{stream_compare}",
        source_kind="sql", target_kind="sql",
        source_id="ds-x", target_id="ds-x",
        sql_mode="double",
        source_sql="SELECT id FROM t ORDER BY id",
        target_sql="SELECT id FROM t ORDER BY id",
        key_columns=["id"],
        limits=RunLimits(
            stream_compare=stream_compare,
            result_format=result_format,
        ),
    )
    return task_store.create(payload).id


# ─── 切片 G 主路径 ─────────────────────────────────────────────────────────


def test_stream_compare_parquet_does_not_call_iterators(isolated_storage, monkeypatch):
    """stream_compare=True + result_format=parquet 必须走 sorted_events 路径，
    绝对不能再调 compare_sorted_row_iterators（攒完整 buckets dict）。"""
    src = [{"id": i, "v": f"x{i}"} for i in range(20)]
    tgt = [{"id": i, "v": f"x{i}"} for i in range(20)]
    _patch_readers(monkeypatch, src, tgt)

    from app.services import runner as runner_module

    def _boom(*args, **kwargs):
        raise AssertionError("compare_sorted_row_iterators 不应被调（应走 events 路径）")

    monkeypatch.setattr(runner_module, "compare_sorted_row_iterators", _boom)

    task_id = _make_task(result_format="parquet", stream_compare=True)
    from app.services.runner import run_task
    result = run_task(task_id)
    assert result.summary.same == 20
    assert result.summary.diff == 0
    assert result.summary.only_source == 0
    assert result.summary.only_target == 0


def test_stream_compare_parquet_meta_counts_correct(isolated_storage, monkeypatch):
    """meta.json 的 bucket counts 跟实际归类对得上。"""
    # source: id=1..10；target: id=1..5 同 + 6..10 异 + 11..15 仅 target
    src = [{"id": i, "v": f"x{i}"} for i in range(1, 11)]
    tgt = [{"id": i, "v": f"x{i}" if i <= 5 else f"y{i}"} for i in range(1, 16)]
    _patch_readers(monkeypatch, src, tgt)

    task_id = _make_task(result_format="parquet", stream_compare=True)
    from app.services.runner import run_task
    result = run_task(task_id)

    assert result.summary.same == 5      # id 1..5
    assert result.summary.diff == 5      # id 6..10
    assert result.summary.only_source == 0
    assert result.summary.only_target == 5  # id 11..15

    # meta.json
    run_dir = isolated_storage["results"] / result.run_id
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    by = {b["name"]: b for b in meta["buckets"]}
    assert by["same"]["rows"] == 5
    assert by["diff"]["rows"] == 5
    assert by["only_target"]["rows"] == 5
    # source_rows = only_source + diff + same = 0 + 5 + 5
    assert meta["source_rows"] == 10
    # target_rows = only_target + diff + same = 5 + 5 + 5
    assert meta["target_rows"] == 15


def test_stream_compare_parquet_same_count_only_no_parquet_file(isolated_storage, monkeypatch):
    """same 桶默认 count_only：count 准确，但 same.parquet 不存在。"""
    src = [{"id": i, "v": f"x{i}"} for i in range(100)]
    tgt = list(src)  # 完全相同 → 全 same
    _patch_readers(monkeypatch, src, tgt)

    task_id = _make_task(result_format="parquet", stream_compare=True)
    from app.services.runner import run_task
    result = run_task(task_id)

    assert result.summary.same == 100
    run_dir = isolated_storage["results"] / result.run_id
    assert not (run_dir / "same.parquet").exists(), "same.parquet 应不存在（count_only）"
    # meta.json same 桶 mode=count_only
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    same_meta = next(b for b in meta["buckets"] if b["name"] == "same")
    assert same_meta["mode"] == "count_only"
    assert same_meta["rows"] == 100
    assert len(same_meta["sample"]) <= 100


def test_stream_compare_parquet_diff_only_source_only_target_parquet_readable(
    isolated_storage, monkeypatch,
):
    """diff / only_source / only_target 三桶 parquet 文件落盘且可读。"""
    src = [{"id": i, "v": f"x{i}"} for i in [1, 2, 3, 5]]
    tgt = [{"id": i, "v": f"x{i}" if i == 1 else f"y{i}"} for i in [1, 2, 4]]
    _patch_readers(monkeypatch, src, tgt)

    task_id = _make_task(result_format="parquet", stream_compare=True)
    from app.services.runner import run_task
    result = run_task(task_id)

    import pyarrow.parquet as pq
    run_dir = isolated_storage["results"] / result.run_id

    only_source = pq.read_table(run_dir / "only_source.parquet").to_pylist()
    only_target = pq.read_table(run_dir / "only_target.parquet").to_pylist()
    diff = pq.read_table(run_dir / "diff.parquet").to_pylist()

    assert sorted(r["key"][0] for r in only_source) == [3, 5]
    assert [r["key"][0] for r in only_target] == [4]
    assert [r["key"][0] for r in diff] == [2]
    # diff 行带 changes struct
    assert "changes" in diff[0]


# ─── 其它路径不回归 ─────────────────────────────────────────────────────────


# ─── Phase 13:mid-run 磁盘水位中止 ─────────────────────────────────────────


def test_stream_compare_aborts_on_mid_run_disk_critical(isolated_storage, monkeypatch):
    """模拟跑到一半磁盘到红线 —— runner 主动 raise DiskWatermarkExceeded,
    清理临时 parquet run 目录,不让半成品累积。"""
    src = [{"id": i, "v": f"x{i}"} for i in range(50)]
    tgt = list(src)
    _patch_readers(monkeypatch, src, tgt)

    # 1. 把水位检查间隔降到 5(默认 5000),让小测试数据也能触发
    from app.services import runner as runner_module
    monkeypatch.setattr(runner_module, "_DISK_WATERMARK_CHECK_INTERVAL", 5)

    # 2. 让 _disk_stats 报 critical(剩余远低于阈值)
    monkeypatch.setattr(
        "app.services.resource_guard._disk_stats",
        lambda: (0.5, 30.0),  # 0.5GB < 默认 5GB 阈值
    )

    task_id = _make_task(result_format="parquet", stream_compare=True)

    from app.services.runner import run_task
    from app.services.resource_guard import DiskWatermarkExceeded

    with pytest.raises(DiskWatermarkExceeded) as exc_info:
        run_task(task_id)

    assert "磁盘" in str(exc_info.value)
    # 临时 run 目录应被 cleanup_partial_parquet 删掉(没残留垃圾)
    # —— run_id 是事后才知道的,通过 results 目录扫确认没遗留子目录
    # (跳过 fixture 自带的 uploads / workflow_runs 两个固定子目录)
    results_dir = isolated_storage["results"]
    skip_names = {"uploads", "workflow_runs"}
    leftover = [
        p for p in results_dir.iterdir()
        if p.is_dir() and p.name not in skip_names
    ]
    assert leftover == [], f"DiskWatermarkExceeded 后应无残留 run 目录,实有:{leftover}"


def test_stream_compare_passes_when_disk_healthy(isolated_storage, monkeypatch):
    """healthy disk + 低 interval —— 不应触发,正常完成"""
    src = [{"id": i, "v": f"x{i}"} for i in range(20)]
    tgt = list(src)
    _patch_readers(monkeypatch, src, tgt)

    from app.services import runner as runner_module
    monkeypatch.setattr(runner_module, "_DISK_WATERMARK_CHECK_INTERVAL", 5)
    monkeypatch.setattr(
        "app.services.resource_guard._disk_stats",
        lambda: (200.0, 30.0),  # healthy
    )

    task_id = _make_task(result_format="parquet", stream_compare=True)
    from app.services.runner import run_task
    result = run_task(task_id)
    assert result.summary.same == 20  # 正常跑完


def test_stream_compare_aborts_on_run_quota_exceeded(isolated_storage, monkeypatch):
    """task.limits.run_disk_quota_mb=1 + 实际落盘超过 → RunQuotaExceeded +
    清理临时目录"""
    src = [{"id": i, "v": f"long_value_string_{i}_padding_for_size"} for i in range(50)]
    tgt = list(src)
    _patch_readers(monkeypatch, src, tgt)

    from app.services import runner as runner_module
    monkeypatch.setattr(runner_module, "_DISK_WATERMARK_CHECK_INTERVAL", 5)
    # 主机磁盘 healthy(确保是 run quota 触发而非 host watermark)
    monkeypatch.setattr(
        "app.services.resource_guard._disk_stats",
        lambda: (200.0, 30.0),
    )
    # 让 check_run_quota 返 True(模拟超 quota,不依赖具体字节)
    # 注意:patch runner 自己 namespace 里 import 进来的引用,而不是 resource_guard
    # 的源 —— runner 用 `from ... import check_run_quota` 拿了独立绑定
    monkeypatch.setattr(
        "app.services.runner.check_run_quota",
        lambda run_dir, quota_mb: (True, f"模拟超额 quota_mb={quota_mb}"),
    )

    # 构造带 run_disk_quota_mb=1 的 task
    from app.models import CompareTaskCreate, RunLimits
    payload = CompareTaskCreate(
        name="quota-test",
        source_kind="sql", target_kind="sql",
        source_id="ds-x", target_id="ds-x",
        sql_mode="double",
        source_sql="SELECT id FROM t ORDER BY id",
        target_sql="SELECT id FROM t ORDER BY id",
        key_columns=["id"],
        limits=RunLimits(
            stream_compare=True,
            result_format="parquet",
            run_disk_quota_mb=1,  # 1MB 配额
        ),
    )
    task_id = task_store.create(payload).id

    from app.services.runner import run_task
    from app.services.resource_guard import RunQuotaExceeded, DiskWatermarkExceeded

    with pytest.raises(RunQuotaExceeded) as exc_info:
        run_task(task_id)

    # RunQuotaExceeded 是 DiskWatermarkExceeded 子类(走同 cleanup 路径)
    assert isinstance(exc_info.value, DiskWatermarkExceeded)
    assert "配额" in str(exc_info.value)

    # cleanup 应删临时 parquet run 目录(跳 fixture 自带的两个固定子目录)
    results_dir = isolated_storage["results"]
    skip_names = {"uploads", "workflow_runs"}
    leftover = [
        p for p in results_dir.iterdir()
        if p.is_dir() and p.name not in skip_names
    ]
    assert leftover == [], f"RunQuotaExceeded 后应无残留 run 目录,实有:{leftover}"


def test_stream_compare_no_quota_no_check(isolated_storage, monkeypatch):
    """run_disk_quota_mb=None(默认)→ check_run_quota 直接 short-circuit 返 False,
    不影响正常 run"""
    src = [{"id": i, "v": f"x{i}"} for i in range(20)]
    tgt = list(src)
    _patch_readers(monkeypatch, src, tgt)

    from app.services import runner as runner_module
    monkeypatch.setattr(runner_module, "_DISK_WATERMARK_CHECK_INTERVAL", 5)
    monkeypatch.setattr(
        "app.services.resource_guard._disk_stats",
        lambda: (200.0, 30.0),  # healthy
    )

    task_id = _make_task(result_format="parquet", stream_compare=True)  # 默认 quota=None
    from app.services.runner import run_task
    result = run_task(task_id)
    assert result.summary.same == 20  # quota=None 不影响正常完成


def test_json_stream_compare_still_uses_iterators(isolated_storage, monkeypatch):
    """result_format=json + stream_compare=True 仍走 compare_sorted_row_iterators
    攒完整 dict（不能切到 events 路径，因为 JsonResultWriter 需要整 dict）。"""
    src = [{"id": 1}, {"id": 2}]
    tgt = [{"id": 1}, {"id": 2}]
    _patch_readers(monkeypatch, src, tgt)

    from app.services import runner as runner_module
    calls = {"n": 0}
    real = runner_module.compare_sorted_row_iterators

    def spy(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(runner_module, "compare_sorted_row_iterators", spy)

    task_id = _make_task(result_format="json", stream_compare=True)
    from app.services.runner import run_task
    result = run_task(task_id)
    assert calls["n"] == 1, "json+stream_compare 必须走 sorted_iterators"
    assert result.summary.same == 2


def test_parquet_non_stream_compare_still_uses_streaming_writer(
    isolated_storage, monkeypatch,
):
    """F.3 路径：result_format=parquet + stream_compare=False —— 不应走 G 路径
    （iterators 也不能调，应走 compare_rows_streaming 行级 events）。"""
    src = [{"id": 1}, {"id": 2}, {"id": 3}]
    tgt = [{"id": 1}, {"id": 2}]
    _patch_readers(monkeypatch, src, tgt)

    from app.services import runner as runner_module
    sorted_calls = {"n": 0}

    def sorted_boom(*args, **kwargs):
        sorted_calls["n"] += 1
        raise AssertionError("F.3 路径不应调 sorted_iterators")

    streaming_calls = {"n": 0}
    real_streaming = runner_module.compare_rows_streaming

    def streaming_spy(*args, **kwargs):
        streaming_calls["n"] += 1
        return real_streaming(*args, **kwargs)

    monkeypatch.setattr(runner_module, "compare_sorted_row_iterators", sorted_boom)
    monkeypatch.setattr(runner_module, "compare_rows_streaming", streaming_spy)

    task_id = _make_task(result_format="parquet", stream_compare=False)
    from app.services.runner import run_task
    result = run_task(task_id)

    assert sorted_calls["n"] == 0
    assert streaming_calls["n"] == 1
    assert result.summary.same == 2
    assert result.summary.only_source == 1


def test_json_non_stream_compare_unchanged(isolated_storage, monkeypatch):
    """原始默认路径：result_format=json + stream_compare=False —— 走 compare_rows
    攒 dict + JsonResultWriter。F.3/G 都不应影响。"""
    src = [{"id": 1, "v": "a"}]
    tgt = [{"id": 1, "v": "b"}]
    _patch_readers(monkeypatch, src, tgt)

    task_id = _make_task(result_format="json", stream_compare=False)
    from app.services.runner import run_task
    result = run_task(task_id)
    assert result.summary.diff == 1
    # legacy <run_id>.json 应该落盘（不是目录格式）
    legacy = isolated_storage["results"] / f"{result.run_id}.json"
    assert legacy.exists()


# ─── 大数据 synthetic ─────────────────────────────────────────────────────


def test_stream_compare_parquet_100k_rows_synthetic(isolated_storage, monkeypatch):
    """100k sorted rows 全 same（最坏的内存场景之一：100k 行经过 events 但都
    归到 same count_only）—— 验证：跑通 + meta counts 正确 + 无 same.parquet。
    不连真实 DB，纯内存 iter。"""
    n = 100_000
    src = [{"id": i, "v": "x"} for i in range(n)]
    tgt = src   # 完全相同
    _patch_readers(monkeypatch, src, tgt)

    task_id = _make_task(result_format="parquet", stream_compare=True)
    from app.services.runner import run_task
    result = run_task(task_id)

    assert result.summary.same == n
    assert result.summary.diff == 0
    assert result.summary.only_source == 0
    assert result.summary.only_target == 0

    run_dir = isolated_storage["results"] / result.run_id
    # same 桶 count_only —— 没 same.parquet
    assert not (run_dir / "same.parquet").exists()
    # meta.json source/target_rows 应该是 n
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["source_rows"] == n
    assert meta["target_rows"] == n
    same_meta = next(b for b in meta["buckets"] if b["name"] == "same")
    assert same_meta["rows"] == n


def test_stream_compare_parquet_100k_with_mixed_buckets(isolated_storage, monkeypatch):
    """100k 行混合：1/3 same, 1/3 diff, 1/3 only_source —— meta counts 准确，
    diff.parquet 行数对，跑通。"""
    third = 30_000
    src = [{"id": i, "v": f"src{i}"} for i in range(3 * third)]
    tgt = (
        [{"id": i, "v": f"src{i}"} for i in range(third)]  # same
        + [{"id": i, "v": f"tgt{i}"} for i in range(third, 2 * third)]  # diff
        # only_source for 2*third..3*third
    )
    _patch_readers(monkeypatch, src, tgt)

    task_id = _make_task(result_format="parquet", stream_compare=True)
    from app.services.runner import run_task
    result = run_task(task_id)

    assert result.summary.same == third
    assert result.summary.diff == third
    assert result.summary.only_source == third
    assert result.summary.only_target == 0

    import pyarrow.parquet as pq
    run_dir = isolated_storage["results"] / result.run_id
    diff_rows_n = pq.ParquetFile(run_dir / "diff.parquet").metadata.num_rows
    only_source_rows_n = pq.ParquetFile(run_dir / "only_source.parquet").metadata.num_rows
    assert diff_rows_n == third
    assert only_source_rows_n == third
    # source_rows = only_source + diff + same
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["source_rows"] == 3 * third
    assert meta["target_rows"] == 2 * third
