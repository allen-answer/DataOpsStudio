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


def _estimate_row_bytes(row: dict[str, Any]) -> int:
    """近似估算一行序列化字节(给 #16 双阈值 flush 用)。

    不追求精确,只要单调上升即可:value 转 str 后取 len(utf-8) + 16 字节 per
    key 的容器 overhead。None / int / float 等都 str 化。`bytes` 直接 len()。
    """
    total = 32  # 字典本身基础开销
    for k, v in row.items():
        total += len(str(k)) + 16
        if v is None:
            total += 4
        elif isinstance(v, (bytes, bytearray)):
            total += len(v)
        elif isinstance(v, (int, float, bool)):
            total += 8
        else:
            try:
                total += len(str(v))
            except Exception:
                total += 64
    return total


@dataclass
class ResultManifest:
    """`writer.finalize()` 返回值 —— runner 拿这个拼 `CompareResult` 响应。

    P1 收口：`samples` 字段把每桶前 N 行抽样交给 writer 维护，runner 不再
    自维护 `samples_buffer`。each writer 决定怎么填（JsonResultWriter 整桶
    在内存所以最后 slice [:N]；ParquetResultWriter 用专门的 sample buffer
    在 write_bucket_row 时累积，避免被 batch flush 清空）。
    """

    result_path: Path
    result_filename: str
    excel_path: Path
    excel_filename: str
    bucket_counts: dict[str, int]
    samples: dict[str, list[dict[str, Any]]]


_SAMPLE_ROWS_DEFAULT = 20


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
            # 整桶在内存，直接 slice 前 N 行
            samples={
                name: list(rows[:_SAMPLE_ROWS_DEFAULT])
                for name, rows in self._buckets.items()
            },
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
    切片 F.2：每桶按 `batch_size` 增量 flush row group —— writer 内存
    上限 = O(batch_size × bucket 数)，不依赖单桶总行数。

    输出布局（详见 docs/COMPARE_RESULT_STORAGE.md §4）：
        <run_dir>/
            meta.json                envelope + 4 桶清单 + sample
            only_source.parquet      mode=full
            only_target.parquet      mode=full
            diff.parquet             mode=full
            same.parquet             仅 persist_same_bucket=True 时存在

    设计取舍（含切片 F.2 修订）：
    - **按 batch flush row group**：每桶维护一个 buffer，长度达 batch_size
      时调 `pyarrow.parquet.ParquetWriter.write_table` 增量写一个 row group。
      finalize 时把所有剩余 buffer flush 完 + close writer。详见
      `docs/STREAMING_COMPARE_WRITER.md`。
    - **schema 锁定**：每桶第一行用 `pa.Table.from_pylist([row])` 推 schema；
      后续 batch 用同 schema build。pyarrow 对额外字段的处理较宽松（可能静默
      coerce），明显的类型冲突 / 嵌套 struct 字段不匹配会抛
      `ValueError("row schema drift in <bucket>")`。要真正严格 schema 校验
      需要单独的 row dict 检查层，留 slice F+。
    - **空桶不开 ParquetWriter**：第一行才 lazy open；零行桶 finalize 时
      meta.json 仍写 `path=null + rows=0`，跟切片 B 行为兼容。
    - **same 桶 count_only 时 sample 是任意 N 行**（dict 插入顺序，通常是第一批
      源端命中的行）。要"随机"或"按 key 排序"留给后续优化。
    - **嵌套字段（diff 的 changes / source / target dict）**：pyarrow struct
      原生支持。
    - **Excel 不在本 writer 内写**：parquet 模式下 Excel 默认按需异步导（切片
      E），所以 `finalize()` 不产 xlsx 文件。Manifest 的 `excel_path` 仍返回
      `<run_id>.xlsx` 路径作为"将来导出去那里"，但文件并不存在。
    """

    DEFAULT_BATCH_SIZE = 5000
    # #16 Wave 4:除了固定行数,加按字节阈值 flush。宽行 / 大文本场景下 5000 行
    # 可能 = 几 GB,单批次内存峰值不可控。env 可配。
    DEFAULT_FLUSH_BYTES = 16 * 1024 * 1024  # 16 MiB

    def __init__(
        self,
        run_dir: Path,
        excel_path: Path,
        payload: dict[str, Any],
        *,
        persist_same_bucket: bool = False,
        same_sample_rows: int = 100,
        batch_size: int | None = None,
        flush_bytes: int | None = None,
    ) -> None:
        self.run_dir = run_dir
        self.excel_path = excel_path        # placeholder（本 writer 不写）
        self._payload = payload
        self._persist_same = persist_same_bucket
        self._sample_rows = same_sample_rows
        self._batch_size = max(int(batch_size or self.DEFAULT_BATCH_SIZE), 1)
        import os as _os
        env_bytes = int(_os.getenv("DATAOPS_COMPARE_WRITER_FLUSH_BYTES", "0") or 0)
        self._flush_bytes = max(
            int(flush_bytes or env_bytes or self.DEFAULT_FLUSH_BYTES), 64 * 1024,  # 至少 64KB
        )

        # 每桶状态：buffer 是当前未 flush 的 batch；writer 是 lazy 打开的
        # ParquetWriter；schema 是首批推出的 pyarrow.Schema；count 是总行数。
        # parquet_paths 给 finalize 写 meta.json 的 bytes 字段用。
        self._bucket_buffers: dict[str, list[dict[str, Any]]] = {
            name: [] for name in _BUCKET_NAMES
        }
        # #16:每桶累计字节估算(buffer 内未 flush 部分)。flush 时清零。
        self._bucket_buffer_bytes: dict[str, int] = {name: 0 for name in _BUCKET_NAMES}
        self._bucket_writers: dict[str, Any] = {name: None for name in _BUCKET_NAMES}
        self._bucket_schemas: dict[str, Any] = {name: None for name in _BUCKET_NAMES}
        self._bucket_counts: dict[str, int] = {name: 0 for name in _BUCKET_NAMES}
        self._bucket_paths: dict[str, Path | None] = {name: None for name in _BUCKET_NAMES}

        # same 桶 count_only 走单独路径：只累计 count + 头 N 行 sample，
        # 不进 buffer / writer。
        self._same_count = 0
        self._same_sample: list[dict[str, Any]] = []
        # P1：runner samples_buffer 收口到 writer —— 每桶前 _sample_rows_for_manifest
        # 行单独留一份，不受 batch flush 清 buffer 影响。same 桶 count_only 直接
        # 复用 _same_sample，不重复存。
        self._sample_rows_for_manifest = _SAMPLE_ROWS_DEFAULT
        self._samples: dict[str, list[dict[str, Any]]] = {
            name: [] for name in _BUCKET_NAMES
        }
        self._finalized = False

    def write_bucket_row(self, bucket: str, row: dict[str, Any]) -> None:
        if bucket not in self._bucket_buffers:
            raise ValueError(f"unknown bucket: {bucket!r}; expected one of {_BUCKET_NAMES}")
        if self._finalized:
            raise RuntimeError("cannot write after finalize()")
        if bucket == "same" and not self._persist_same:
            self._same_count += 1
            if len(self._same_sample) < self._sample_rows:
                self._same_sample.append(row)
            return
        self._bucket_buffers[bucket].append(row)
        self._bucket_counts[bucket] += 1
        # #16:估算 row bytes 累计;触发任一阈值即 flush
        self._bucket_buffer_bytes[bucket] += _estimate_row_bytes(row)
        # 单独捕获 manifest sample，避免被 batch flush 清空
        if len(self._samples[bucket]) < self._sample_rows_for_manifest:
            self._samples[bucket].append(row)
        if (len(self._bucket_buffers[bucket]) >= self._batch_size
                or self._bucket_buffer_bytes[bucket] >= self._flush_bytes):
            self._flush_bucket(bucket)

    def _flush_bucket(self, bucket: str) -> None:
        """把 bucket buffer 写一个 row group 出去。lazy open ParquetWriter
        + 锁定 schema。空 buffer no-op。"""
        buffer = self._bucket_buffers[bucket]
        if not buffer:
            return
        import pyarrow as pa
        import pyarrow.parquet as pq

        if self._bucket_writers[bucket] is None:
            self.run_dir.mkdir(parents=True, exist_ok=True)
            path = self.run_dir / f"{bucket}.parquet"
            # 用首批推 schema 一次性锁定
            first_table = pa.Table.from_pylist(buffer)
            schema = first_table.schema
            writer = pq.ParquetWriter(path, schema, compression="snappy")
            writer.write_table(first_table)
            self._bucket_writers[bucket] = writer
            self._bucket_schemas[bucket] = schema
            self._bucket_paths[bucket] = path
        else:
            schema = self._bucket_schemas[bucket]
            try:
                table = pa.Table.from_pylist(buffer, schema=schema)
            except (pa.lib.ArrowInvalid, pa.lib.ArrowTypeError, pa.lib.ArrowNotImplementedError) as exc:
                raise ValueError(
                    f"row schema drift in bucket {bucket!r}: {exc}",
                ) from exc
            self._bucket_writers[bucket].write_table(table)
        # #16:flush 完清字节计数,准备下一批
        self._bucket_buffer_bytes[bucket] = 0
        buffer.clear()

    def finalize(self) -> ResultManifest:
        if self._finalized:
            raise RuntimeError("finalize() called twice")
        self._finalized = True

        self.run_dir.mkdir(parents=True, exist_ok=True)
        # 把所有桶残留 buffer flush + close writer
        for name in _BUCKET_NAMES:
            if self._bucket_buffers[name]:
                self._flush_bucket(name)
            writer = self._bucket_writers[name]
            if writer is not None:
                writer.close()
                self._bucket_writers[name] = None

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
            count = self._bucket_counts[name]
            bucket_counts[name] = count
            parquet_path = self._bucket_paths[name]
            if count == 0 or parquet_path is None:
                # 没行 / 没开过 writer —— meta.json path=null + rows=0
                bucket_metas.append({"name": name, "path": None, "rows": 0, "mode": "full"})
                continue
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

        # P1 收口：samples 走专门的 _samples buffer（write_bucket_row 累积，
        # 不受 batch flush 清 _bucket_buffers 影响）。same count_only 复用
        # _same_sample 但 clip 到 _sample_rows_for_manifest（meta.json 仍是
        # 完整 _same_sample 容量），保跟其它桶 manifest sample 上限一致。
        samples: dict[str, list[dict[str, Any]]] = {}
        for name in _BUCKET_NAMES:
            if name == "same" and not self._persist_same:
                samples[name] = list(self._same_sample[: self._sample_rows_for_manifest])
            else:
                samples[name] = list(self._samples[name])

        return ResultManifest(
            result_path=meta_path,
            result_filename=f"{self.run_dir.name}/meta.json",
            excel_path=self.excel_path,  # 仅占位；切片 E 才生成
            excel_filename=self.excel_path.name,
            bucket_counts=bucket_counts,
            samples=samples,
        )


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
