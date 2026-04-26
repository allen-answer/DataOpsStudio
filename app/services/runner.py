from __future__ import annotations

import uuid
import logging
import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.compare.engine import compare_rows, compare_sorted_row_iterators
from app.dbclients.factory import fetch_rows, iter_rows
from app.models import CompareResult, CompareSummary, SqlMode
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

    source = datasource_store.get(task.source_id)
    target = datasource_store.get(task.target_id)
    if source is None:
        raise KeyError(f"Source datasource not found: {task.source_id}")
    if target is None:
        raise KeyError(f"Target datasource not found: {task.target_id}")

    target_sql = task.source_sql if task.sql_mode == SqlMode.SINGLE else task.target_sql
    try:
        _notify(status_callback, "validating", "校验 SQL")
        validate_readonly_sql(task.source_sql)
        validate_readonly_sql(target_sql)

        if task.limits.stream_compare:
            _notify(status_callback, "querying_source", "准备流式分块对比：请确保两边 SQL 已按主键排序")
            source_rows_iter = iter_rows(
                source,
                task.source_sql,
                max_rows=task.limits.max_rows,
                chunk_size=task.limits.fetch_chunk_size,
                progress_callback=lambda count: _notify(status_callback, "querying_source", f"查询源数据：已读取 {count} 行"),
            )
            target_rows_iter = iter_rows(
                target,
                target_sql,
                max_rows=task.limits.max_rows,
                chunk_size=task.limits.fetch_chunk_size,
                progress_callback=lambda count: _notify(status_callback, "querying_target", f"查询目标数据：已读取 {count} 行"),
            )
            _notify(status_callback, "comparing", "执行流式归并对比")
            buckets = compare_sorted_row_iterators(source_rows_iter, target_rows_iter, task.key_columns, task.rules)
            source_rows_count, target_rows_count = _bucket_row_counts(buckets)
        else:
            _notify(status_callback, "querying_source", "查询源数据")
            source_rows = fetch_rows(
                source,
                task.source_sql,
                max_rows=task.limits.max_rows,
                chunk_size=task.limits.fetch_chunk_size,
                progress_callback=lambda count: _notify(status_callback, "querying_source", f"查询源数据：已读取 {count} 行"),
            )
            _notify(status_callback, "querying_target", "查询目标数据")
            target_rows = fetch_rows(
                target,
                target_sql,
                max_rows=task.limits.max_rows,
                chunk_size=task.limits.fetch_chunk_size,
                progress_callback=lambda count: _notify(status_callback, "querying_target", f"查询目标数据：已读取 {count} 行"),
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
