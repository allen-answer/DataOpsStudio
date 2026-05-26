"""Wave 4 #16:ParquetResultWriter 字节+行数双阈值 flush。"""
from __future__ import annotations

from pathlib import Path

import pytest


def test_estimate_row_bytes_monotonic():
    from app.compare.result_writer import _estimate_row_bytes
    small = _estimate_row_bytes({"id": 1, "name": "x"})
    big = _estimate_row_bytes({"id": 1, "name": "x" * 1024})
    assert big > small + 1000


def test_estimate_handles_none_and_bytes():
    from app.compare.result_writer import _estimate_row_bytes
    row = {"a": None, "b": b"\x00\x01\x02\x03"}
    assert _estimate_row_bytes(row) > 0


def test_narrow_row_flushes_on_row_count(tmp_path):
    """窄行不触发 bytes 阈值,走旧 batch_size flush 路径。"""
    from app.compare.result_writer import ParquetResultWriter

    writer = ParquetResultWriter(
        run_dir=tmp_path / "run",
        excel_path=tmp_path / "run.xlsx",
        payload={"run_id": "test"},
        batch_size=10,
        flush_bytes=100 * 1024 * 1024,  # 100MB,远大于实际
    )
    for i in range(10):
        writer.write_bucket_row("diff", {"id": i, "name": f"r{i}"})
    # 10 行触发 row count flush → buffer 应该清空
    assert writer._bucket_buffers["diff"] == []
    assert writer._bucket_buffer_bytes["diff"] == 0


def test_wide_row_flushes_on_bytes(tmp_path):
    """宽行触发 bytes 阈值,远早于 row count 上限。"""
    from app.compare.result_writer import ParquetResultWriter

    writer = ParquetResultWriter(
        run_dir=tmp_path / "run",
        excel_path=tmp_path / "run.xlsx",
        payload={"run_id": "test"},
        batch_size=10_000,    # row 阈值很高
        flush_bytes=64 * 1024,  # 64KB 字节阈值
    )
    # 每行 ~4KB,16 行就该触发 bytes 阈值
    big_text = "x" * 4000
    for i in range(20):
        writer.write_bucket_row("diff", {"id": i, "name": big_text})
    # 至少触发过一次 flush → buffer 不会含全部 20 行
    assert len(writer._bucket_buffers["diff"]) < 20


def test_env_flush_bytes_picked_up(tmp_path, monkeypatch):
    """env `DATAOPS_COMPARE_WRITER_FLUSH_BYTES` 覆盖默认 16MB。"""
    monkeypatch.setenv("DATAOPS_COMPARE_WRITER_FLUSH_BYTES", str(512 * 1024))  # 512KB
    from app.compare.result_writer import ParquetResultWriter

    writer = ParquetResultWriter(
        run_dir=tmp_path / "run",
        excel_path=tmp_path / "run.xlsx",
        payload={"run_id": "test"},
        batch_size=10_000,
    )
    assert writer._flush_bytes == 512 * 1024


def test_explicit_flush_bytes_overrides_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATAOPS_COMPARE_WRITER_FLUSH_BYTES", "999999")
    from app.compare.result_writer import ParquetResultWriter

    writer = ParquetResultWriter(
        run_dir=tmp_path / "run",
        excel_path=tmp_path / "run.xlsx",
        payload={"run_id": "test"},
        flush_bytes=128 * 1024,  # 显式优先
    )
    assert writer._flush_bytes == 128 * 1024


def test_min_64kb_floor(tmp_path):
    """配置极小值时不低于 64KB 防止退化。"""
    from app.compare.result_writer import ParquetResultWriter

    writer = ParquetResultWriter(
        run_dir=tmp_path / "run",
        excel_path=tmp_path / "run.xlsx",
        payload={"run_id": "test"},
        flush_bytes=1,
    )
    assert writer._flush_bytes >= 64 * 1024
