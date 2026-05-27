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
    compare_sorted_row_events,
    compare_sorted_row_iterators,
)
from app.dbclients.factory import query_timeout_override
from app.services.resource_guard import (
    DiskWatermarkExceeded,
    RunQuotaExceeded,
    check_disk_critical,
    check_run_quota,
)
from app.compare.result_writer import (
    JsonResultWriter,
    ParquetResultWriter,
    ResultWriter,
    feed_buckets,
)


_BUCKET_NAMES: tuple[str, ...] = ("only_source", "only_target", "diff", "same")
_SAMPLE_ROWS_PER_BUCKET = 20

# Phase 13: streaming compare 每写 N 行查一次磁盘水位 —— admission control
# 只在任务进入时查一次,长 run 跑到一半把盘写爆是空档。命中阈值就抛
# `DiskWatermarkExceeded`,caller(此文件下方)cleanup 临时 parquet 目录后再 raise。
_DISK_WATERMARK_CHECK_INTERVAL = 5000
from app.models import CompareResult, CompareSummary, CompareTask, SourceKind, SqlMode
from app.readers import CsvReader, ExcelReader, ParquetReader, RowReader, SqlReader
from app.services.compare_schema import build_schema_report
from app.services.excel_uploads import resolve_excel_path, resolve_uploaded_path
from app.services.repositories import datasource_store, task_store
from app.utils.sql_guard import validate_readonly_sql
from app.utils.paths import RESULTS_DIR


logger = logging.getLogger(__name__)


def run_task(
    task_id: str,
    status_callback: Any | None = None,
    *,
    owner_user_id: str = "",
    workflow_run_id: str = "",
    job_id: str = "",
) -> CompareResult:
    """统一 compare 入口(Wave 3 #13)。

    所有路径(sync API / async job / workflow node)都从这进入,函数内统一:
    - 提前分配 run_id 并 run_index.reserve() —— 进入 admission 即登记
    - mark_running 在 worker 真正开始时
    - finally finalize 确保终态写回(success / failed / aborted_guard)
    """
    from app.services import run_index as _run_index_mod

    start = time.perf_counter()
    started_at = datetime.now()
    task = task_store.get(task_id)
    if task is None:
        raise KeyError(f"Task not found: {task_id}")

    # #17 Wave 4:大任务自动 promote 到 stream_compare + parquet。基于估算输入
    # 字节(max_rows × 估算列宽 256B)做 admission decision。env 阈值可配:
    # DATAOPS_COMPARE_AUTO_STREAM_BYTES(默认 1 GiB → promote);超 5 GiB 直接 deny。
    task, promote_reason = _maybe_promote_large_task(task)

    # 提前分配 run_id + reservation。run_id 形态保留时间戳 + 8 hex 兼容老代码,
    # 但通过 run_index.task_id 字段可靠反查(不再靠时间戳 prefix 猜 task)。
    run_id = f"{started_at.strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"
    _run_index_mod.reserve(
        run_id=run_id,
        task_id=task.id,
        job_id=job_id,
        workflow_run_id=workflow_run_id,
        project_id=task.project_id or "",
        owner_user_id=owner_user_id,
        source_ds_id=task.source_id or "",
        target_ds_id=task.target_id or "",
        result_format=task.limits.result_format,
        stream_compare=task.limits.stream_compare,
        max_rows=task.limits.max_rows,
    )
    if promote_reason:
        # 把 promote 原因记进 run_index.guard_reason(终态 finalize 时再 setattr 会覆盖,
        # 这里走 update_disk_bytes 不影响,直接 update guard_reason 字段)
        try:
            from app.services.sqlite_store import connect as _sqc
            with _sqc() as _conn:
                _conn.execute(
                    "UPDATE run_index SET guard_reason=? WHERE run_id=?",
                    (promote_reason, run_id),
                )
        except Exception:
            pass

    # #15 Wave 4:run 级 memory guard。observe/enforce 模式都会在关键点采
    # 样 RSS;enforce 模式达 hard_ratio 即抛 MemoryBudgetExceeded → finalize
    # 走 aborted_guard 路径 + guard_reason='memory_hard_limit'。
    from app.services.memory_guard import MemoryGuard, MemoryBudgetExceeded
    mem_guard = MemoryGuard()

    # #22 Wave 5:compare 通道 metrics(终态时打点)
    from app.services import metrics as _metrics

    try:
        # Phase 13:单任务 query_timeout 覆盖。task.limits.query_timeout_seconds 显
        # 式设了就 push 进 ContextVar,下游 fetch_rows / iter_rows 路径自动取这个
        # 值而非全局 env。None / 不设 → 走 env 默认(行为不变)。
        with query_timeout_override(task.limits.query_timeout_seconds):
            _run_index_mod.mark_running(run_id)
            result = _run_task_inner(
                task, started_at, start, status_callback,
                run_id=run_id, mem_guard=mem_guard,
            )
        # 成功 finalize
        final_disk = _safe_run_disk_bytes(run_id)
        peak_mb = mem_guard.peak_rss_mb()
        _run_index_mod.finalize(
            run_id, status="success",
            disk_bytes=final_disk,
            peak_rss_mb=peak_mb,
            result_path=str(RESULTS_DIR / run_id),
        )
        # #22 metric
        _metrics.compare_runs_total.inc(status="success")
        _metrics.compare_disk_bytes.observe(float(final_disk), result_format=task.limits.result_format)
        _metrics.compare_peak_rss_mb.observe(peak_mb, result_format=task.limits.result_format)
        return result
    except MemoryBudgetExceeded as exc:
        peak_mb = mem_guard.peak_rss_mb()
        _run_index_mod.finalize(
            run_id, status="aborted_guard",
            guard_reason="memory_hard_limit",
            error=str(exc),
            disk_bytes=_safe_run_disk_bytes(run_id),
            peak_rss_mb=peak_mb,
        )
        _metrics.compare_runs_total.inc(status="aborted_guard")
        _metrics.compare_guard_aborts_total.inc(reason="memory_hard_limit")
        raise
    except (DiskWatermarkExceeded, RunQuotaExceeded) as exc:
        peak_mb = mem_guard.peak_rss_mb()
        reason = type(exc).__name__
        _run_index_mod.finalize(
            run_id, status="aborted_guard",
            guard_reason=reason,
            error=str(exc),
            disk_bytes=_safe_run_disk_bytes(run_id),
            peak_rss_mb=peak_mb,
        )
        _metrics.compare_runs_total.inc(status="aborted_guard")
        _metrics.compare_guard_aborts_total.inc(reason=reason)
        raise
    except Exception as exc:
        _run_index_mod.finalize(
            run_id, status="failed",
            error=str(exc)[:1000],
            disk_bytes=_safe_run_disk_bytes(run_id),
            peak_rss_mb=mem_guard.peak_rss_mb(),
        )
        _metrics.compare_runs_total.inc(status="failed")
        raise


# #17 Wave 4:估算列宽近似(每列平均 256 字节,文本 / JSON 列偏宽场景偏低估,
# int / 短字符串场景偏高估 —— 用于 admission 量级判断,不追求精确)
_ESTIMATED_ROW_BYTES = 256


def _maybe_promote_large_task(task: CompareTask) -> tuple[CompareTask, str]:
    """根据 task.limits.max_rows 估算输入字节,大任务自动促级。

    阈值(env 可配,默认):
    - `< auto_stream_bytes`(1 GiB) → 不动
    - `auto_stream_bytes ~ deny_bytes`(1 GiB ~ 5 GiB):若非 stream+parquet,强制切换
    - `>= deny_bytes`(5 GiB) → raise ValueError(超出 run_budget,拒绝执行)

    返回 (新 task, promote_reason)。reason 空 = 没动。
    """
    import os as _os
    auto_stream_bytes = int(_os.getenv("DATAOPS_COMPARE_AUTO_STREAM_BYTES", str(1 << 30)))
    deny_bytes = int(_os.getenv("DATAOPS_COMPARE_DENY_BYTES", str(5 * (1 << 30))))

    estimated = max(0, int(task.limits.max_rows)) * _ESTIMATED_ROW_BYTES

    if estimated >= deny_bytes:
        raise ValueError(
            f"Task estimated input {estimated / (1 << 30):.1f}GiB exceeds run budget "
            f"{deny_bytes / (1 << 30):.1f}GiB. Lower max_rows or split task. "
            f"(env: DATAOPS_COMPARE_DENY_BYTES)"
        )

    if estimated < auto_stream_bytes:
        return task, ""

    # 已经是 stream + parquet → 无需 promote
    if task.limits.stream_compare and task.limits.result_format == "parquet":
        return task, ""

    new_limits = task.limits.model_copy(update={
        "stream_compare": True,
        "result_format": "parquet",
    })
    new_task = task.model_copy(update={"limits": new_limits})
    reason = (
        f"auto_streaming_promoted(estimated={estimated // (1 << 20)}MiB,"
        f"threshold={auto_stream_bytes // (1 << 20)}MiB)"
    )
    logger.warning(
        "task %s promoted to stream_compare+parquet due to estimated input size: %s",
        task.id, reason,
    )
    return new_task, reason


def _safe_run_disk_bytes(run_id: str) -> int:
    """run_id 对应 results 目录 / 文件累计字节,best-effort 不抛错。"""
    try:
        from pathlib import Path
        candidates: list[Path] = []
        run_dir = RESULTS_DIR / run_id
        if run_dir.exists():
            candidates.append(run_dir)
        for suffix in (".json", ".xlsx"):
            f = RESULTS_DIR / f"{run_id}{suffix}"
            if f.exists():
                candidates.append(f)
        total = 0
        for p in candidates:
            if p.is_file():
                total += p.stat().st_size
            else:
                for f in p.rglob("*"):
                    if f.is_file():
                        total += f.stat().st_size
        return total
    except Exception:
        return 0


def _run_task_inner(
    task: CompareTask,
    started_at: datetime,
    start: float,
    status_callback: Any | None,
    *,
    run_id: str | None = None,
    mem_guard: Any | None = None,
) -> CompareResult:
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
        # 切片 G：stream_compare=True + result_format=parquet 时走 sorted_events
        # → writer，归并归并阶段也不再持完整 buckets dict（真正 O(batch) 内存）。
        use_streaming_writer = (
            task.limits.result_format == "parquet" and not task.limits.stream_compare
        )
        use_stream_compare_to_writer = (
            task.limits.result_format == "parquet" and task.limits.stream_compare
        )

        buckets: dict[str, list[dict[str, Any]]] | None = None
        source_rows: list[dict[str, Any]] | None = None
        target_rows: list[dict[str, Any]] | None = None
        source_rows_iter = None
        target_rows_iter = None

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
            if use_stream_compare_to_writer:
                # 切片 G：iterator 延迟到 writer 创建后走 events → writer 消费。
                # source/target 行数在 events 循环里 tally，先占位避免引用未定义。
                source_rows_count = 0
                target_rows_count = 0
            else:
                # stream_compare + json：仍走老 compare_sorted_row_iterators 攒
                # buckets dict，给 JsonResultWriter 写整文件。
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
            # #15 在 source 加载完后采样一次 — 这是 fetch_all 路径首个 OOM 高峰点
            if mem_guard is not None:
                mem_guard.check(stage="fetch_all.source", rows=len(source_rows) if source_rows else 0)
            _notify(status_callback, "querying_target", "读取目标数据")
            target_rows = target_reader.fetch_all(
                max_rows=task.limits.max_rows,
                chunk_size=task.limits.fetch_chunk_size,
                progress_callback=progress("target"),
            )
            if mem_guard is not None:
                mem_guard.check(stage="fetch_all.target", rows=len(target_rows) if target_rows else 0)
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
    # Wave 3:run_id 由 wrapper(run_task)提前分配 + reserve;此处若 inner 被
    # 旧路径直接调(目前没有但留兜底)再生成。
    if run_id is None:
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

    # P1：samples 收口到 writer.manifest.samples —— runner 不再维护
    # samples_buffer。stream_compare+parquet 路径仍要 tally source/target
    # 行数（writer 看不到 bucket 归属维度的"哪边贡献"），但 sample 本身
    # 不再重复存。

    if use_stream_compare_to_writer:
        # 切片 G：stream_compare + parquet —— sorted_events → writer，归并阶段
        # 也不持完整 buckets。source/target 行数通过事件桶归属累加：
        # source 贡献 only_source / diff / same；target 贡献 only_target /
        # diff / same。等价于 _bucket_row_counts 在完整 dict 上算出来的值。
        _notify(status_callback, "comparing", "执行流式归并对比 + 增量落 parquet")
        assert source_rows_iter is not None and target_rows_iter is not None
        src_count = 0
        tgt_count = 0
        rows_written = 0
        try:
            for bucket, row in compare_sorted_row_events(
                source_rows_iter, target_rows_iter, task.key_columns, task.rules,
            ):
                writer.write_bucket_row(bucket, row)
                if bucket in ("only_source", "diff", "same"):
                    src_count += 1
                if bucket in ("only_target", "diff", "same"):
                    tgt_count += 1
                rows_written += 1
                if rows_written % _DISK_WATERMARK_CHECK_INTERVAL == 0:
                    _check_mid_run_disk(writer, task, rows_written)
        except DiskWatermarkExceeded:
            _cleanup_partial_parquet(writer)
            raise
        source_rows_count = src_count
        target_rows_count = tgt_count
        # 回填 payload 让 ParquetResultWriter.finalize 写正确 envelope 字段
        payload["source_rows"] = src_count
        payload["target_rows"] = tgt_count
    elif use_streaming_writer:
        _notify(status_callback, "comparing", "执行流式对比 + 增量落 parquet")
        assert source_rows is not None and target_rows is not None
        rows_written = 0
        try:
            for bucket, row in compare_rows_streaming(
                source_rows, target_rows, task.key_columns, task.rules,
            ):
                writer.write_bucket_row(bucket, row)
                rows_written += 1
                if rows_written % _DISK_WATERMARK_CHECK_INTERVAL == 0:
                    _check_mid_run_disk(writer, task, rows_written)
        except DiskWatermarkExceeded:
            _cleanup_partial_parquet(writer)
            raise
    else:
        # json 模式或 parquet+stream_compare（已被上面分支接管）—— 走 buckets dict
        assert buckets is not None
        feed_buckets(writer, buckets)

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
        samples={
            name: [_json_safe(row) for row in rows]
            for name, rows in manifest.samples.items()
        },
    )


def _check_mid_run_disk(writer: ResultWriter, task: CompareTask, rows_written: int) -> None:
    """每 _DISK_WATERMARK_CHECK_INTERVAL 行调一次:主机磁盘水位 + 单 run 配额。

    都属于 mid-run 中止 —— `DiskWatermarkExceeded` / `RunQuotaExceeded` 走同一
    cleanup 路径(caller `except DiskWatermarkExceeded` 一并接住)。
    """
    critical, reason = check_disk_critical()
    if critical:
        raise DiskWatermarkExceeded(
            f"mid-run 磁盘水位达 critical:{reason} "
            f"(已写 {rows_written} 行,主动中止防止把盘写爆)"
        )
    run_dir = getattr(writer, "run_dir", None)
    over_quota, quota_reason = check_run_quota(
        run_dir, task.limits.run_disk_quota_mb,
    )
    if over_quota:
        raise RunQuotaExceeded(
            f"mid-run 单 run 配额超额:{quota_reason} "
            f"(已写 {rows_written} 行,主动中止防止单 run 吃光配额)"
        )


def _cleanup_partial_parquet(writer: ResultWriter) -> None:
    """mid-run 磁盘水位中止时:把 ParquetResultWriter 的临时 run 目录 rmtree。

    JsonResultWriter 不在 streaming 路径里(本切片只覆盖两个 parquet 流式分支),
    所以仅检查 `run_dir` 属性存在即清理。删失败不抛(防 cleanup 报错掩盖原始
    DiskWatermarkExceeded)。
    """
    run_dir = getattr(writer, "run_dir", None)
    if run_dir is None:
        return
    try:
        import shutil
        shutil.rmtree(run_dir, ignore_errors=True)
        logger.warning("cleaned up partial parquet run dir=%s after disk watermark abort", run_dir)
    except Exception:
        logger.exception("failed to cleanup partial parquet run dir=%s", run_dir)


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
