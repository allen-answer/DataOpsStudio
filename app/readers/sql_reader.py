from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterator

from app.dbclients.factory import fetch_rows, iter_rows
from app.models import DataSource


@dataclass
class SqlReader:
    """RowReader backed by a database query."""

    datasource: DataSource
    sql: str

    def fetch_all(
        self,
        *,
        max_rows: int | None = None,
        chunk_size: int | None = None,
        progress_callback: Callable[[int], None] | None = None,
    ) -> list[dict[str, Any]]:
        return fetch_rows(
            self.datasource,
            self.sql,
            max_rows=max_rows,
            chunk_size=chunk_size,
            progress_callback=progress_callback,
        )

    def iter_rows(
        self,
        *,
        max_rows: int | None = None,
        chunk_size: int | None = None,
        progress_callback: Callable[[int], None] | None = None,
    ) -> Iterator[dict[str, Any]]:
        return iter_rows(
            self.datasource,
            self.sql,
            max_rows=max_rows,
            chunk_size=chunk_size,
            progress_callback=progress_callback,
        )
