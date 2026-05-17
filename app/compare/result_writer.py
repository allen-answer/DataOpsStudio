"""Compare 结果落盘抽象。

切片 A（本切片）：定义 `ResultWriter` 协议 + 一个 `JsonResultWriter` 等价实现
——行为完全跟旧路径一致（单文件 `results/<run_id>.json` + Excel），但 runner
不再直接调 `exporter.write_result_json` / `write_excel`，而是 feed writer。

切片 B 起：`ParquetResultWriter` 接同一个协议落 `results/<run_id>/` 目录形式。
runner 完全不感知格式差异。

设计来源：docs/COMPARE_RESULT_STORAGE.md
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from app.compare.engine import CompareBuckets
from app.services.exporter import write_excel, write_result_json


_BUCKET_NAMES: tuple[str, ...] = ("only_source", "only_target", "diff", "same")


@dataclass
class ResultManifest:
    """`writer.finalize()` 返回值 —— runner 拿这个拼 `CompareResult` 响应。"""

    result_path: Path
    result_filename: str
    excel_path: Path
    excel_filename: str
    bucket_counts: dict[str, int]


class ResultWriter(Protocol):
    """Compare 结果落盘协议。

    runner 按桶逐行 feed，再调 `finalize()` 落盘 + 拿 manifest。
    """

    def write_bucket_row(self, bucket: str, row: dict[str, Any]) -> None: ...

    def finalize(self) -> ResultManifest: ...


class JsonResultWriter:
    """切片 A 等价实现：四个桶在内存攒齐，`finalize` 时写 `<run_id>.json` + Excel。

    跟旧路径完全等价（同样的 JSON shape / 同样的 Excel）—— 切片 A 只是把 IO
    走 writer 层，不改任何用户可见行为。

    切片 B+ 会换成 `ParquetResultWriter` 走目录形态。
    """

    def __init__(
        self,
        result_path: Path,
        excel_path: Path,
        payload: dict[str, Any],
        excel_max_rows: int | None = None,
    ) -> None:
        # payload 不含 buckets；finalize 时 merge buckets 再写
        self.result_path = result_path
        self.excel_path = excel_path
        self.excel_max_rows = excel_max_rows
        self._payload = payload
        self._buckets: CompareBuckets = {name: [] for name in _BUCKET_NAMES}
        self._finalized = False

    def write_bucket_row(self, bucket: str, row: dict[str, Any]) -> None:
        if bucket not in self._buckets:
            raise ValueError(f"unknown bucket: {bucket!r}; expected one of {_BUCKET_NAMES}")
        if self._finalized:
            raise RuntimeError("cannot write after finalize()")
        self._buckets[bucket].append(row)

    def finalize(self) -> ResultManifest:
        if self._finalized:
            raise RuntimeError("finalize() called twice")
        self._finalized = True
        full_payload = {**self._payload, "buckets": self._buckets}
        write_result_json(self.result_path, full_payload)
        write_excel(self.excel_path, self._buckets, max_rows=self.excel_max_rows)
        return ResultManifest(
            result_path=self.result_path,
            result_filename=self.result_path.name,
            excel_path=self.excel_path,
            excel_filename=self.excel_path.name,
            bucket_counts={name: len(rows) for name, rows in self._buckets.items()},
        )


def feed_buckets(writer: ResultWriter, buckets: CompareBuckets) -> None:
    """把已经攒齐的 `CompareBuckets` 按桶逐行 feed 到 writer。

    切片 A 用：runner 仍走 `compare_rows`（返回 list），用这个 helper 一次性
    倒入 writer。切片 B 起 engine 直接 `write_bucket_row`，runner 不再用这个
    helper。
    """
    for bucket_name in _BUCKET_NAMES:
        for row in buckets.get(bucket_name, []):
            writer.write_bucket_row(bucket_name, row)
