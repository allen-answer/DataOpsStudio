from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator


def _require_pyarrow():
    try:
        import pyarrow.parquet as pq  # noqa: F401

        return pq
    except ImportError as exc:
        raise RuntimeError(
            "Parquet 支持需要 pyarrow —— 请在容器/虚拟环境中执行 `pip install pyarrow`"
        ) from exc


@dataclass
class ParquetReader:
    """RowReader backed by a Parquet file via pyarrow.

    Parquet 是列存储格式，按行迭代意义不大；ParquetReader 一次性读到
    pyarrow Table 后再展开成 dict 行。fetch_all / iter_rows 接口跟 ExcelReader
    一致以兼容 compare engine。

    跟 stream_compare 互斥：parquet reader 不保证按主键排序输出，且没有
    "流式按行送" 的语义（pyarrow.parquet.iter_batches 还是分批送，不是
    主键有序）。compare engine 用 stream_compare 时不应选 parquet。
    """

    file_path: Path
    columns: list[str] | None = None  # 可选：仅读指定列（性能/内存优化）

    def fetch_all(
        self,
        *,
        max_rows: int | None = None,
        chunk_size: int | None = None,
        progress_callback: Callable[[int], None] | None = None,
    ) -> list[dict[str, Any]]:
        rows = list(self.iter_rows(max_rows=max_rows, chunk_size=chunk_size, progress_callback=progress_callback))
        return rows

    def iter_rows(
        self,
        *,
        max_rows: int | None = None,
        chunk_size: int | None = None,
        progress_callback: Callable[[int], None] | None = None,
    ) -> Iterator[dict[str, Any]]:
        pq = _require_pyarrow()
        parquet_file = pq.ParquetFile(self.file_path)
        batch_size = max(1, int(chunk_size or 5000))
        kept_count = 0
        for batch in parquet_file.iter_batches(batch_size=batch_size, columns=self.columns):
            cols = batch.column_names
            arrs = [batch.column(c).to_pylist() for c in cols]
            for row_idx in range(batch.num_rows):
                row_dict = {cols[i]: arrs[i][row_idx] for i in range(len(cols))}
                if all(value is None or value == "" for value in row_dict.values()):
                    continue
                kept_count += 1
                if max_rows is not None and kept_count > max_rows:
                    raise RuntimeError(f"Parquet file exceeds max_rows={max_rows}")
                if progress_callback is not None and kept_count % batch_size == 0:
                    progress_callback(kept_count)
                yield row_dict
        if progress_callback is not None:
            progress_callback(kept_count)


def list_columns(file_path: Path) -> list[str]:
    pq = _require_pyarrow()
    parquet_file = pq.ParquetFile(file_path)
    return list(parquet_file.schema.names)
