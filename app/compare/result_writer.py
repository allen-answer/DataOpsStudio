"""Compare 结果落盘抽象。

切片 A：定义 `ResultWriter` 协议 + `JsonResultWriter` 等价实现 —— 行为完全
跟旧路径一致（单文件 `results/<run_id>.json` + Excel），但 runner 不再直接
调 `exporter.write_result_json` / `write_excel`，而是 feed writer。

切片 B（本切片）：`ParquetResultWriter` 接同一个协议落 `results/<run_id>/`
目录形式 —— `meta.json` + `{only_source,only_target,diff}.parquet`，`same` 桶
默认只在 meta.json 记 count + sample（`persist_same_bucket=True` 才全量落）。
runner 按 `task.limits.result_format` 选 writer。

切片 C+ 起读侧 API 按 meta.json 派发新老格式；本切片 reader 不动，PR1 用户
显式 opt-in `result_format="parquet"`。

设计来源：docs/COMPARE_RESULT_STORAGE.md
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from app.compare.engine import CompareBuckets
from app.services.exporter import write_excel, write_result_json


_BUCKET_NAMES: tuple[str, ...] = ("only_source", "only_target", "diff", "same")

# meta.json 的 format_version。读侧靠这个判断 schema —— 改动 schema 必须
# 跟 reader（PR2+）的 dispatch 协议同步 bump。
PARQUET_FORMAT_VERSION = 1


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


class ParquetResultWriter:
    """切片 B：把对比结果落成目录形态的 parquet + meta.json。

    输出布局（详见 docs/COMPARE_RESULT_STORAGE.md §4）：
        <run_dir>/
            meta.json                envelope + 4 桶清单 + sample
            only_source.parquet      mode=full
            only_target.parquet      mode=full
            diff.parquet             mode=full
            same.parquet             仅 persist_same_bucket=True 时存在

    设计取舍：
    - **不增量 flush，整桶 batch write**：切片 B 仍接 `compare_rows` 全量返回的
      dict，writer 内存里攒齐桶再 `pq.write_table` 一次性写。把 streaming
      写入（按 batch flush row group）留给切片 B+（engine 改成 generator 后）。
      PR1 的 same 桶 `count_only` 已经把最大头痛省下来；正式 streaming 写入
      等真有 10M only_source 场景再上。
    - **same 桶 count_only 时 sample 是任意 N 行**（dict 插入顺序，通常是第一批
      源端命中的行）。要"随机"或"按 key 排序"留给后续优化。
    - **schema 推断走 pyarrow `Table.from_pylist`**：靠第一批行的 dict shape
      推；空桶不写文件（reader 看 meta.json 的 count=0 / path=null 就知道）。
    - **嵌套字段（diff 的 changes / source / target dict）**：pyarrow struct
      原生支持；同名 key 缺失的 dict 走 NaN/None。
    - **Excel 不在本 writer 内写**：parquet 模式下 Excel 默认按需异步导（切片
      E），所以 `finalize()` 不产 xlsx 文件。Manifest 的 `excel_path` 仍返回
      `<run_id>.xlsx` 路径作为"将来导出去那里"，但文件并不存在。
    """

    def __init__(
        self,
        run_dir: Path,
        excel_path: Path,
        payload: dict[str, Any],
        *,
        persist_same_bucket: bool = False,
        same_sample_rows: int = 100,
    ) -> None:
        self.run_dir = run_dir
        self.excel_path = excel_path        # placeholder（本 writer 不写）
        self._payload = payload
        self._persist_same = persist_same_bucket
        self._sample_rows = same_sample_rows
        self._buckets: CompareBuckets = {name: [] for name in _BUCKET_NAMES}
        # same 桶 count_only 时单独留 sample；不污染 _buckets["same"] 避免
        # write 整桶时被当全量写入
        self._same_count = 0
        self._same_sample: list[dict[str, Any]] = []
        self._finalized = False

    def write_bucket_row(self, bucket: str, row: dict[str, Any]) -> None:
        if bucket not in self._buckets:
            raise ValueError(f"unknown bucket: {bucket!r}; expected one of {_BUCKET_NAMES}")
        if self._finalized:
            raise RuntimeError("cannot write after finalize()")
        if bucket == "same" and not self._persist_same:
            self._same_count += 1
            if len(self._same_sample) < self._sample_rows:
                self._same_sample.append(row)
            return
        self._buckets[bucket].append(row)

    def finalize(self) -> ResultManifest:
        if self._finalized:
            raise RuntimeError("finalize() called twice")
        self._finalized = True

        self.run_dir.mkdir(parents=True, exist_ok=True)
        bucket_metas: list[dict[str, Any]] = []
        bucket_counts: dict[str, int] = {}
        for name in _BUCKET_NAMES:
            if name == "same" and not self._persist_same:
                count = self._same_count
                bucket_counts[name] = count
                bucket_metas.append({
                    "name": name,
                    "path": None,
                    "rows": count,
                    "mode": "count_only",
                    "sample": list(self._same_sample),
                })
                continue
            rows = self._buckets[name]
            count = len(rows)
            bucket_counts[name] = count
            if count == 0:
                # 不写空文件 —— meta.json 里 path=null + rows=0 已经传达
                bucket_metas.append({"name": name, "path": None, "rows": 0, "mode": "full"})
                continue
            parquet_path = self.run_dir / f"{name}.parquet"
            _write_bucket_parquet(parquet_path, rows)
            bucket_metas.append({
                "name": name,
                "path": parquet_path.name,
                "rows": count,
                "mode": "full",
                "bytes": parquet_path.stat().st_size,
            })

        meta = {
            **self._payload,
            "buckets": bucket_metas,
            "format": "parquet",
            "format_version": PARQUET_FORMAT_VERSION,
            "written_at": datetime.now().isoformat(timespec="seconds"),
        }
        meta_path = self.run_dir / "meta.json"
        meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2, default=_json_default),
            encoding="utf-8",
        )

        return ResultManifest(
            result_path=meta_path,
            result_filename=f"{self.run_dir.name}/meta.json",
            excel_path=self.excel_path,  # 仅占位；切片 E 才生成
            excel_filename=self.excel_path.name,
            bucket_counts=bucket_counts,
        )


def _write_bucket_parquet(path: Path, rows: list[dict[str, Any]]) -> None:
    """单桶一次性 batch write。空 rows 不该到这（caller 已过滤）。

    pyarrow 缺省（不指定 schema）走 `from_pylist` 自动推；嵌套 dict 推成 struct
    type，list 推成 list type。对 diff 桶的 changes / source / target 嵌套
    友好。压缩走 snappy（速度 + 压缩比平衡）。
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.Table.from_pylist(rows)
    pq.write_table(table, path, compression="snappy")


def _json_default(obj: Any) -> Any:
    """meta.json 序列化兜底：sample 行里可能有 datetime / Decimal / bytes。"""
    from decimal import Decimal
    from datetime import date

    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, bytes):
        return obj.hex()
    return str(obj)
