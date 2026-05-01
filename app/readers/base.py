from __future__ import annotations

from typing import Any, Callable, Iterator, Protocol


class RowReader(Protocol):
    """Source-agnostic row reader for one side of a compare task.

    Implementations adapt SQL queries, Excel sheets, and future CSV/Parquet/etc.
    to the same dict-row shape that compare engine consumes.
    """

    def fetch_all(
        self,
        *,
        max_rows: int | None = None,
        chunk_size: int | None = None,
        progress_callback: Callable[[int], None] | None = None,
    ) -> list[dict[str, Any]]:
        ...

    def iter_rows(
        self,
        *,
        max_rows: int | None = None,
        chunk_size: int | None = None,
        progress_callback: Callable[[int], None] | None = None,
    ) -> Iterator[dict[str, Any]]:
        ...
