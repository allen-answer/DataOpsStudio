from __future__ import annotations

import uuid
import logging
import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.compare.engine import compare_rows, compare_sorted_row_iterators
from app.models import CompareResult, CompareSummary, CompareTask, SourceKind, SqlMode
from app.readers import ExcelReader, RowReader, SqlReader
from app.services.excel_uploads import resolve_excel_path
from app.services.exporter import write_excel, write_result_json
from app.services.repositories import datasource_store, task_store
from app.utils.sql_guard import validate_readonly_sql
from app.utils.paths import RESULTS_DIR


logger = logging.getLogger(__name__)


def run_task(task_id: str, status_callback: Any | None = None) -> CompareResult:
    start = time.perf_counter()
    started_at = datetime.now()
    task = task_store.get(task_id)
    if task is None:
        raise KeyError(f"Task not found: {task_id}")

    logger.info(
        "task start task_id=%s task_name=%s source_id=%s target_id=%s sql_mode=%s keys=%s",
        task.id,
        task.name,
        task.source_id,
        task.target_id,
        task.sql_mode.value,
        ",".join(task.key_columns),
    )

    try:
        _notify(status_callback, "validating", "校验输入")
        source_reader = _build_reader(task, "source")
        target_reader = _build_reader(task, "target")

        progress = lambda side: lambda count: _notify(status_callback, f"querying_{side}", f"读取{('源' if side == 'source' else '目标')}数据：已 {count} 行")

        if task.limits.stream_compare:
            _notify(status_callback, "querying_source", "准备流式分块对比：请确保两边数据已按主键排序")
            source_rows_iter = source_reader.iter_rows(
                max_rows=task.limits.max_rows,
                chunk_size=task.limits.fetch_chunk_size,
                progress_callback=progress("source"),
            )
            target_rows_iter = target_reader.iter_rows(
                max_rows=task.limits.max_rows,
                chunk_size=task.limits.fetch_chunk_size,
                progress_callback=progress("target"),
            )
            _notify(status_callback, "comparing", "执行流式归并对比")
            buckets = compare_sorted_row_iterators(source_rows_iter, target_rows_iter, task.key_columns, task.rules)
            source_rows_count, target_rows_count = _bucket_row_counts(buckets)
        else:
            _notify(status_callback, "querying_source", "读取源数据")
            source_rows = source_reader.fetch_all(
                max_rows=task.limits.max_rows,
                chunk_size=task.limits.fetch_chunk_size,
                progress_callback=progress("source"),
            )
            _notify(status_callback, "querying_target", "读取目标数据")
            target_rows = target_reader.fetch_all(
                max_rows=task.limits.max_rows,
                chunk_size=task.limits.fetch_chunk_size,
                progress_callback=progress("target"),
            )
            _notify(status_callback, "comparing", "执行对比")
            buckets = compare_rows(source_rows, target_rows, task.key_columns, task.rules)
            source_rows_count = len(source_rows)
            target_rows_count = len(target_rows)
    except Exception:
        logger.exception("task failed task_id=%s task_name=%s", task.id, task.name)
        raise

    _notify(status_callback, "exporting", "写入结果")
    run_id = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"
    result_path = RESULTS_DIR / f"{run_id}.json"
    excel_path = RESULTS_DIR / f"{run_id}.xlsx"
    summary = CompareSummary(**{name: len(rows) for name, rows in buckets.items()})
    elapsed_seconds = round(time.perf_counter() - start, 3)
    payload = {
        "run_id": run_id,
        "task_id": task.id,
        "task_name": task.name,
        "started_at": started_at.isoformat(timespec="seconds"),
        "elapsed_seconds": elapsed_seconds,
        "source_rows": source_rows_count,
        "target_rows": target_rows_count,
        "summary": summary.model_dump(),
        "rules": task.rules.model_dump(),
        "limits": task.limits.model_dump(),
        "buckets": buckets,
    }

    write_result_json(result_path, payload)
    write_excel(excel_path, buckets, max_rows=task.limits.export_max_rows)
    logger.info(
        "task success task_id=%s task_name=%s only_source=%s only_target=%s diff=%s same=%s result=%s excel=%s elapsed=%.3fs",
        task.id,
        task.name,
        summary.only_source,
        summary.only_target,
        summary.diff,
        summary.same,
        result_path.name,
        excel_path.name,
        elapsed_seconds,
    )

    return CompareResult(
        run_id=run_id,
        task_id=task.id,
        summary=summary,
        result_path=str(result_path),
        result_filename=result_path.name,
        excel_path=str(excel_path),
        excel_filename=excel_path.name,
        task_name=task.name,
        started_at=started_at.isoformat(timespec="seconds"),
        elapsed_seconds=elapsed_seconds,
        source_rows=source_rows_count,
        target_rows=target_rows_count,
        samples={name: [_json_safe(row) for row in rows[:20]] for name, rows in buckets.items()},
    )


def _build_reader(task: CompareTask, side: str) -> RowReader:
    """Pick a SqlReader or ExcelReader for the given side based on task.{side}_kind.

    For SQL single mode, target reuses source SQL — preserves existing behavior.
    """
    if side == "source":
        kind = task.source_kind
    else:
        kind = task.target_kind

    if kind == SourceKind.SQL:
        datasource_id = task.source_id if side == "source" else task.target_id
        datasource = datasource_store.get(datasource_id)
        if datasource is None:
            raise KeyError(f"{side} datasource not found: {datasource_id}")
        if side == "source":
            sql = task.source_sql
        else:
            sql = task.source_sql if task.sql_mode == SqlMode.SINGLE else task.target_sql
        validate_readonly_sql(sql)
        return SqlReader(datasource=datasource, sql=sql)

    if kind == SourceKind.EXCEL:
        if side == "source":
            return ExcelReader(
                file_path=resolve_excel_path(task.source_excel_path),
                sheet=task.source_sheet,
                header_row=task.source_header_row,
            )
        return ExcelReader(
            file_path=resolve_excel_path(task.target_excel_path),
            sheet=task.target_sheet,
            header_row=task.target_header_row,
        )

    raise ValueError(f"unsupported source kind: {kind}")


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def _notify(callback: Any | None, status: str, message: str) -> None:
    if callback is not None:
        callback(status, message)


def _bucket_row_counts(buckets: dict[str, list[dict[str, Any]]]) -> tuple[int, int]:
    same = len(buckets.get("same", []))
    diff = len(buckets.get("diff", []))
    return (
        same + diff + len(buckets.get("only_source", [])),
        same + diff + len(buckets.get("only_target", [])),
    )
