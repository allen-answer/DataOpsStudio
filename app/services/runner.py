from __future__ import annotations

import uuid
import logging
import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.compare.engine import (
    compare_rows,
    compare_rows_streaming,
    compare_sorted_row_iterators,
)
from app.compare.result_writer import (
    JsonResultWriter,
    ParquetResultWriter,
    ResultWriter,
    feed_buckets,
)


_BUCKET_NAMES: tuple[str, ...] = ("only_source", "only_target", "diff", "same")
_SAMPLE_ROWS_PER_BUCKET = 20
from app.models import CompareResult, CompareSummary, CompareTask, SourceKind, SqlMode
from app.readers import CsvReader, ExcelReader, ParquetReader, RowReader, SqlReader
from app.services.compare_schema import build_schema_report
from app.services.excel_uploads import resolve_excel_path, resolve_uploaded_path
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
        source_reader = build_reader(task, "source")
        target_reader = build_reader(task, "target")
        schema_report: dict[str, Any] = {}

        progress = lambda side: lambda count: _notify(status_callback, f"querying_{side}", f"读取{('源' if side == 'source' else '目标')}数据：已 {count} 行")

        # 切片 F.3：result_format=parquet + 非 stream_compare 时走 streaming 路径——
        # 不再 compare_rows 攒 buckets dict，直接 events → writer。
        use_streaming_writer = (
            task.limits.result_format == "parquet" and not task.limits.stream_compare
        )

        buckets: dict[str, list[dict[str, Any]]] | None = None
        source_rows: list[dict[str, Any]] | None = None
        target_rows: list[dict[str, Any]] | None = None

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
            schema_report = build_schema_report(
                list(source_rows[0]) if source_rows else [],
                list(target_rows[0]) if target_rows else [],
                task.key_columns,
                task.rules,
            )
            if task.rules.schema_policy == "strict" and schema_report.get("has_schema_mismatch"):
                messages = [item.get("message", "") for item in schema_report.get("warnings", []) if item.get("message")]
                raise ValueError("Schema mismatch: " + "；".join(messages))
            source_rows_count = len(source_rows)
            target_rows_count = len(target_rows)
            if not use_streaming_writer:
                # json 路径或 stream_compare —— 必须先攒 buckets dict 给 JsonResultWriter
                _notify(status_callback, "comparing", "执行对比")
                buckets = compare_rows(source_rows, target_rows, task.key_columns, task.rules)
            # 否则推迟到 writer 选定后走 streaming events
    except Exception:
        logger.exception("task failed task_id=%s task_name=%s", task.id, task.name)
        raise

    _notify(status_callback, "exporting", "写入结果")
    run_id = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"
    excel_path = RESULTS_DIR / f"{run_id}.xlsx"
    elapsed_seconds = round(time.perf_counter() - start, 3)
    # payload 不含 buckets —— writer.finalize 时 merge 再写；summary 也推迟到
    # manifest.bucket_counts 出来后再算（streaming 路径里 dict 已经不持有）
    payload = {
        "run_id": run_id,
        "task_id": task.id,
        "task_name": task.name,
        "started_at": started_at.isoformat(timespec="seconds"),
        "elapsed_seconds": elapsed_seconds,
        "source_rows": source_rows_count,
        "target_rows": target_rows_count,
        # summary 先占位，finalize 后回填到 meta.json 的 envelope 字段
        "summary": {name: 0 for name in _BUCKET_NAMES},
        "rules": task.rules.model_dump(),
        "limits": task.limits.model_dump(),
        "schema_report": schema_report,
    }

    # 切片 B：按 task.limits.result_format 选 writer。
    # - "json"（默认）：JsonResultWriter，老格式向后兼容
    # - "parquet"：ParquetResultWriter，目录形态 + same 桶 count_only
    # 切片 F.3：parquet + 非 stream_compare 直接 events → writer，不持完整 buckets
    writer: ResultWriter
    if task.limits.result_format == "parquet":
        run_dir = RESULTS_DIR / run_id
        writer = ParquetResultWriter(
            run_dir=run_dir,
            excel_path=excel_path,
            payload=payload,
            persist_same_bucket=task.limits.persist_same_bucket,
            same_sample_rows=task.limits.same_sample_rows,
        )
    else:
        result_path = RESULTS_DIR / f"{run_id}.json"
        writer = JsonResultWriter(
            result_path=result_path,
            excel_path=excel_path,
            payload=payload,
            excel_max_rows=task.limits.export_max_rows,
        )

    # samples 在 feed/event loop 里同步收集，runner 不再依赖完整 buckets dict
    samples_buffer: dict[str, list[dict[str, Any]]] = {name: [] for name in _BUCKET_NAMES}

    if use_streaming_writer:
        _notify(status_callback, "comparing", "执行流式对比 + 增量落 parquet")
        assert source_rows is not None and target_rows is not None
        for bucket, row in compare_rows_streaming(
            source_rows, target_rows, task.key_columns, task.rules,
        ):
            writer.write_bucket_row(bucket, row)
            if len(samples_buffer[bucket]) < _SAMPLE_ROWS_PER_BUCKET:
                samples_buffer[bucket].append(row)
    else:
        # json 模式或 parquet+stream_compare —— buckets dict 已经在上面攒齐
        assert buckets is not None
        feed_buckets(writer, buckets)
        for name in _BUCKET_NAMES:
            samples_buffer[name] = list(buckets.get(name, []))[:_SAMPLE_ROWS_PER_BUCKET]

    manifest = writer.finalize()
    summary = CompareSummary(**manifest.bucket_counts)
    logger.info(
        "task success task_id=%s task_name=%s only_source=%s only_target=%s diff=%s same=%s result=%s excel=%s elapsed=%.3fs",
        task.id,
        task.name,
        summary.only_source,
        summary.only_target,
        summary.diff,
        summary.same,
        manifest.result_filename,
        manifest.excel_filename,
        elapsed_seconds,
    )

    return CompareResult(
        run_id=run_id,
        task_id=task.id,
        summary=summary,
        result_path=str(manifest.result_path),
        result_filename=manifest.result_filename,
        excel_path=str(manifest.excel_path),
        excel_filename=manifest.excel_filename,
        task_name=task.name,
        started_at=started_at.isoformat(timespec="seconds"),
        elapsed_seconds=elapsed_seconds,
        source_rows=source_rows_count,
        target_rows=target_rows_count,
        schema_report=schema_report,
        samples={name: [_json_safe(row) for row in rows] for name, rows in samples_buffer.items()},
    )


def build_reader(task: CompareTask, side: str) -> RowReader:
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

    _CSV_SUFFIXES = {".csv", ".tsv", ".txt"}
    _PARQUET_SUFFIXES = {".parquet", ".pq"}

    if kind == SourceKind.CSV:
        # 复用 excel uploads 目录 + 通用 path resolve（限 csv/tsv/txt suffix）
        path_field = task.source_file_path if side == "source" else task.target_file_path
        encoding = (task.source_file_encoding if side == "source" else task.target_file_encoding) or "utf-8-sig"
        delimiter = (task.source_csv_delimiter if side == "source" else task.target_csv_delimiter) or ","
        header_row = task.source_header_row if side == "source" else task.target_header_row
        return CsvReader(
            file_path=resolve_uploaded_path(path_field, allowed_suffixes=_CSV_SUFFIXES, label="CSV file"),
            encoding=encoding,
            delimiter=delimiter,
            header_row=header_row,
        )

    if kind == SourceKind.PARQUET:
        path_field = task.source_file_path if side == "source" else task.target_file_path
        return ParquetReader(
            file_path=resolve_uploaded_path(path_field, allowed_suffixes=_PARQUET_SUFFIXES, label="Parquet file"),
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
