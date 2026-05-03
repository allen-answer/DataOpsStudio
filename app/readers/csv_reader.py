from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator


@dataclass
class CsvReader:
    """RowReader backed by a CSV / TSV file.

    依赖 stdlib `csv`，不引入 pandas。常见参数：
      - encoding：默认 utf-8-sig（自动剥 BOM）；中文老 ETL 文件用 'gbk'
      - delimiter：默认 ','；TSV 用 '\\t'
      - header_row：1-indexed，header 之上的行被跳过（极少数文件首部有元数据）

    跟 ExcelReader 行为一致：丢未命名列、跳全空行、`max_rows` 超出 raise。
    """

    file_path: Path
    encoding: str = "utf-8-sig"
    delimiter: str = ","
    header_row: int = 1
    quotechar: str = '"'

    def fetch_all(
        self,
        *,
        max_rows: int | None = None,
        chunk_size: int | None = None,
        progress_callback: Callable[[int], None] | None = None,
    ) -> list[dict[str, Any]]:
        return list(
            self.iter_rows(
                max_rows=max_rows,
                chunk_size=chunk_size,
                progress_callback=progress_callback,
            )
        )

    def iter_rows(
        self,
        *,
        max_rows: int | None = None,
        chunk_size: int | None = None,
        progress_callback: Callable[[int], None] | None = None,
    ) -> Iterator[dict[str, Any]]:
        # newline="" 让 csv 模块自己处理 CRLF 换行
        with open(self.file_path, mode="r", encoding=self.encoding, newline="") as handle:
            reader = csv.reader(handle, delimiter=self.delimiter, quotechar=self.quotechar)
            headers: list[str] = []
            for index, raw_row in enumerate(reader, start=1):
                if index == self.header_row:
                    headers = [self._normalize_header(cell) or "" for cell in raw_row]
                    break
            if not any(headers):
                return

            kept_count = 0
            for raw_row in reader:
                # 行长 < headers：补 None；行长 > headers：尾部多余列丢掉（跟 Excel 行为一致）
                values = list(raw_row) + [None] * max(0, len(headers) - len(raw_row))
                row_dict = {
                    header: self._normalize_value(values[i])
                    for i, header in enumerate(headers)
                    if header
                }
                if all(value is None or value == "" for value in row_dict.values()):
                    continue
                kept_count += 1
                if max_rows is not None and kept_count > max_rows:
                    raise RuntimeError(f"CSV file exceeds max_rows={max_rows}")
                if progress_callback is not None and chunk_size and kept_count % chunk_size == 0:
                    progress_callback(kept_count)
                yield row_dict
            if progress_callback is not None:
                progress_callback(kept_count)

    @staticmethod
    def _normalize_header(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        # 剥可能的 BOM —— `utf-8-sig` 已经处理 BOM，但有些工具用 utf-8 + 显式 BOM
        if text.startswith("﻿"):
            text = text[1:]
        return text or None

    @staticmethod
    def _normalize_value(value: Any) -> Any:
        # CSV 一律是 str；空字符串保留为 ""（compare engine 的 empty_as_null
        # 选项决定是否当 None 处理）。None 仅在行长 < headers 补齐时出现。
        return value


def list_columns(
    file_path: Path,
    encoding: str = "utf-8-sig",
    delimiter: str = ",",
    header_row: int = 1,
    quotechar: str = '"',
) -> list[str]:
    with open(file_path, mode="r", encoding=encoding, newline="") as handle:
        reader = csv.reader(handle, delimiter=delimiter, quotechar=quotechar)
        for index, raw_row in enumerate(reader, start=1):
            if index == header_row:
                return [
                    str(cell).strip().lstrip("﻿")
                    for cell in raw_row
                    if cell is not None and str(cell).strip()
                ]
        return []
