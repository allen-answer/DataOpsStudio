"""切片 E：对比结果 Excel 异步导出。

老路径（slice A 起）：runner 同步写 Excel —— 大结果 OOM + 慢，且 parquet 模式
（slice B）下 runner 完全不产 Excel。

本模块提供两个能力：
1. `build_excel_for_run(run_id, target_path, max_rows)` —— 同步实现，把指定
   run 的 4 桶组装成 `CompareBuckets` dict 后调老 `exporter.write_excel`。
   legacy json 直接读 `<run_id>.json`；parquet 读 meta.json + 4 个 `.parquet`
   文件 + same 桶的 count_only sample 兜底。
2. `submit_excel_export(run_id)` —— 走 `services/jobs.py` 的 ThreadPoolExecutor
   把 `build_excel_for_run` 放后台跑；返 JobInfo（`kind="excel_export"`）；
   完成后 `result.download_url` 给前端拉。

导出文件位置：
- parquet runs：`results/<run_id>/export.xlsx`
- legacy runs：`results/<run_id>.xlsx` 仍由 runner 同步产出，本模块的异步路径
  对 legacy runs 是 no-op（端点直接返回已经存在的文件路径）。

并发 / 取消：复用 jobs.py 的 `_executor`（max_workers=2）+ `cancel_requested`
标志。导出任务通常 < 60s，不优先支持中间状态 callback。
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.compare.engine import CompareBuckets
from app.services.exporter import write_excel, write_excel_streaming
from app.services.run_result import (
    RunNotFound,
    detect_format,
    iter_bucket_rows,
    load_run_meta,
)
from app.utils.paths import RESULTS_DIR


logger = logging.getLogger(__name__)

_BUCKET_NAMES: tuple[str, ...] = ("only_source", "only_target", "diff", "same")


class ExcelExportError(RuntimeError):
    """build_excel_for_run 内部不可恢复错误（reader 读不到 / writer 失败）。"""


def export_excel_path(run_id: str) -> Path:
    """新格式（parquet）导出落 `results/<run_id>/export.xlsx`；老格式仍走
    `results/<run_id>.xlsx`（runner 已经写好）。同一个函数负责决定路径，
    避免端点 / 前端各算一遍。"""
    fmt = detect_format(run_id)
    if fmt == "parquet":
        return RESULTS_DIR / run_id / "export.xlsx"
    # legacy / missing 都走老位置（missing 时 path 不存在，调用方自己判）
    return RESULTS_DIR / f"{run_id}.xlsx"


def _load_buckets_from_legacy(run_id: str) -> CompareBuckets:
    """legacy <run_id>.json 已经把 4 桶全量塞在 buckets 字段里，直接吐出。"""
    import json
    path = RESULTS_DIR / f"{run_id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    buckets_data = data.get("buckets") or {}
    return {name: list(buckets_data.get(name, [])) for name in _BUCKET_NAMES}


def _load_buckets_from_parquet(
    run_id: str,
    *,
    max_rows: int | None = None,
) -> CompareBuckets:
    """parquet 走 meta.json 找各桶路径 + pyarrow `iter_batches` 按需 take。

    P1 加 `max_rows` 上限：传非 None 时按 `write_excel._limit_buckets` 同样
    的桶顺序 (diff / only_source / only_target / same) 分配预算，每桶用
    row group 迭代，避免一次 `read_table().to_pylist()` 把整文件载入内存。
    same 桶 count_only 时拿 meta.json 的 sample（无 parquet 文件）。

    `max_rows=None` 仍走全量读回 —— 给单测 / 小结果用；正式 endpoint 走
    `build_excel_for_run` 总会先落到 `limits.export_max_rows` 的非空兜底值。
    完全流式 Excel 写出（write_only）留给切片 F+。
    """
    import pyarrow.parquet as pq

    run_dir = RESULTS_DIR / run_id
    meta = load_run_meta(run_id)
    by_name = {b["name"]: b for b in meta.get("buckets") or []}
    buckets: CompareBuckets = {}
    # write_excel 的 _limit_buckets 按 (diff, only_source, only_target, same)
    # 顺序消耗 max_rows —— 这里按同样顺序分配，保证内存峰值 ≤ 实际写出量
    order = ("diff", "only_source", "only_target", "same")
    remaining = max_rows if max_rows is not None else None
    for name in order:
        bucket_meta = by_name.get(name) or {}
        mode = bucket_meta.get("mode") or "full"
        if mode == "count_only":
            sample = list(bucket_meta.get("sample") or [])
            if remaining is not None:
                sample = sample[: max(remaining, 0)]
                remaining -= len(sample)
            buckets[name] = sample
            continue
        parquet_path_name = bucket_meta.get("path")
        if not parquet_path_name:
            buckets[name] = []
            continue
        parquet_path = run_dir / parquet_path_name
        if not parquet_path.exists():
            buckets[name] = []
            continue
        if remaining is None:
            buckets[name] = pq.read_table(parquet_path).to_pylist()
            continue
        if remaining <= 0:
            buckets[name] = []
            continue
        out: list[dict[str, object]] = []
        pq_file = pq.ParquetFile(parquet_path)
        for batch in pq_file.iter_batches(batch_size=min(remaining, 5000)):
            for row in batch.to_pylist():
                out.append(row)
                if len(out) >= remaining:
                    break
            if len(out) >= remaining:
                break
        buckets[name] = out
        remaining -= len(out)
    return buckets


def _resolve_default_max_rows(run_id: str) -> int | None:
    """从 run envelope 的 `limits.export_max_rows` 拿默认值。缺失 / 非正数
    返回 None（不限）；正常 task 走 RunLimits 默认 50_000 一定有非 None 值。"""
    try:
        meta = load_run_meta(run_id)
    except RunNotFound:
        return None
    limits = meta.get("limits") or {}
    raw = limits.get("export_max_rows")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def build_excel_for_run(
    run_id: str,
    *,
    target_path: Path | None = None,
    max_rows: int | None = None,
) -> Path:
    """同步实现：从 run 的存储格式读出 4 桶 → 老 `write_excel` → 落到目标路径。

    `target_path=None` 时自动选择（见 `export_excel_path`）。

    `max_rows=None` 时从 run envelope 的 `limits.export_max_rows` 兜底 ——
    P1 修复：旧实现传 None 给 write_excel 就是"无限"，对千万级行的 parquet
    run 会让 reader 把整桶 parquet 加载到内存。现在 endpoint 不带 max_rows
    走默认会被 envelope.limits.export_max_rows 兜住（RunLimits 默认 50_000）。
    显式传 0 或 None 兜底仍解析不到非正数才真的不限（CLI / 单测路径）。

    抛 RunNotFound / ExcelExportError 让调用方决定 4xx vs 5xx。
    """
    fmt = detect_format(run_id)
    if fmt == "missing":
        raise RunNotFound(run_id)

    if max_rows is None:
        max_rows = _resolve_default_max_rows(run_id)

    target_path = target_path or export_excel_path(run_id)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        if fmt == "parquet":
            # 切片 F.4：parquet 路径走 streaming Excel —— 不再 _load_buckets_*
            # 攒 4 桶 dict，writer 走 openpyxl write_only 行级 append。
            # 内存上限 ≈ O(batch_size × col_width) 而非 O(max_rows × col_width)。
            _write_excel_streaming_for_parquet(run_id, target_path, max_rows)
        else:
            buckets = _load_buckets_from_legacy(run_id)
            write_excel(target_path, buckets, max_rows=max_rows)
    except RunNotFound:
        raise
    except Exception as exc:
        raise ExcelExportError(f"failed to write excel for {run_id}: {exc}") from exc
    return target_path


def _write_excel_streaming_for_parquet(
    run_id: str, target_path: Path, max_rows: int | None,
) -> None:
    """切片 F.4：parquet run → Excel write_only 流式写出。

    1. 读 meta.json 拿每桶 path / mode / sample
    2. 用 pyarrow `ParquetFile.schema_arrow` 抽 source / target struct 字段名
       （不解码 row group 数据）写入 `bucket_columns`
    3. `iter_bucket_rows` 工厂喂 `write_excel_streaming` 行级 append
    """
    meta = load_run_meta(run_id)
    bucket_columns = _collect_bucket_columns_from_meta(run_id, meta)

    def factory(bucket_name: str):
        # `max_rows=None` 让 writer 自己按总额度跨桶分配
        return iter_bucket_rows(run_id, bucket_name, max_rows=None)

    write_excel_streaming(
        target_path,
        bucket_iter_factory=factory,
        bucket_columns=bucket_columns,
        max_rows=max_rows,
    )


def _collect_bucket_columns_from_meta(
    run_id: str, meta: dict[str, Any],
) -> dict[str, dict[str, list[str]]]:
    """从 meta.json 各桶的 parquet schema / sample 抽 source / target 字段名。

    parquet 桶 (mode=full + path 非空)：走 pyarrow schema，找 struct 字段
        "source" / "target" 的子字段名（不读 row group 数据）。**字段顺序跟
        pyarrow `struct.field(i)` 的内部顺序一致**（写入时由首批 dict 推
        schema 时锁定）—— 跟老 write_excel 按行 dict 插入顺序略有差异，对
        Excel 用户表现为列顺序可能跟 JsonResultWriter 模式不一样，但稳定。
    count_only 桶（path=null）：从 sample 第一行 dict.keys() 推（sample 是
        in-memory 列表，廉价）。
    空桶：返 {"source": [], "target": []}。

    嵌套 schema 缺 source / target struct 时（不该发生但兜底）返空列表。
    """
    import pyarrow.parquet as pq

    run_dir = RESULTS_DIR / run_id
    out: dict[str, dict[str, list[str]]] = {}
    for bucket_meta in meta.get("buckets") or []:
        name = bucket_meta.get("name")
        if name not in _BUCKET_NAMES:
            continue
        cols: dict[str, list[str]] = {"source": [], "target": []}
        path_name = bucket_meta.get("path")
        if path_name:
            parquet_path = run_dir / path_name
            if parquet_path.exists():
                schema = pq.ParquetFile(parquet_path).schema_arrow
                for side in ("source", "target"):
                    idx = schema.get_field_index(side)
                    if idx >= 0:
                        struct_type = schema.field(idx).type
                        if hasattr(struct_type, "num_fields"):
                            cols[side] = [
                                struct_type.field(i).name
                                for i in range(struct_type.num_fields)
                            ]
        else:
            # count_only / 空桶：sample 推断
            for row in bucket_meta.get("sample") or []:
                if isinstance(row.get("source"), dict):
                    cols["source"] = list(row["source"].keys())
                if isinstance(row.get("target"), dict):
                    cols["target"] = list(row["target"].keys())
                if cols["source"] or cols["target"]:
                    break
        out[name] = cols
    # 保证 4 个桶都有 entry（write_excel_streaming layout 假设）
    for name in _BUCKET_NAMES:
        out.setdefault(name, {"source": [], "target": []})
    return out


# ─── 异步 job 封装（走 jobs.py 的 _executor / _jobs 状态机） ─────────────────


def submit_excel_export(run_id: str) -> dict[str, Any]:
    """把 build_excel_for_run 放后台跑。返回 JobInfo（dict）。

    JobInfo.kind="excel_export"，task_id 字段复用存 run_id（兼容现有 JobInfo
    schema 不动；前端按 kind 解读 task_id 的语义）。完成后 `result` =
    `{filename, download_url}`。
    """
    from app.services import jobs as jobs_module
    from app.services.jobs import (
        _executor,
        _futures,
        _iso,
        _lock,
        _patch_job,
        _set_job,
        cleanup_jobs,
    )

    cleanup_jobs()
    job_id = uuid4().hex
    now = datetime.now()
    _set_job(
        job_id,
        {
            "job_id": job_id,
            "kind": "excel_export",
            "task_id": run_id,         # 复用 task_id 字段存目标 run_id
            "status": "queued",
            "stage": "queued",
            "message": "queued",
            "created_at": _iso(now),
            "updated_at": _iso(now),
            "expires_at": "",
            "retry_count": 0,
            "max_retries": 0,
            "result": None,
            "error": "",
            "cancel_requested": False,
        },
    )
    future = _executor.submit(_run_excel_export_job, job_id, run_id)
    with _lock:
        _futures[job_id] = future
    from app.services.jobs import get_job
    return get_job(job_id)


def _run_excel_export_job(job_id: str, run_id: str) -> None:
    from app.services.jobs import _is_cancel_requested, _patch_job

    try:
        if _is_cancel_requested(job_id):
            _patch_job(
                job_id, status="cancelled", stage="cancelled",
                message="cancelled", error="cancelled before start",
            )
            return
        _patch_job(
            job_id, status="running", stage="exporting",
            message=f"exporting excel for {run_id}",
        )
        path = build_excel_for_run(run_id)
        rel = path.relative_to(RESULTS_DIR).as_posix()
        _patch_job(
            job_id,
            status="success",
            stage="done",
            message="excel ready",
            result={
                "run_id": run_id,
                "filename": path.name,
                "relative_path": rel,
                "download_url": f"/results/{rel}",
            },
        )
        logger.info("excel export ready job=%s run=%s path=%s", job_id, run_id, rel)
    except RunNotFound:
        _patch_job(
            job_id, status="failed", stage="failed",
            message="run not found", error=f"run_id {run_id} not found",
        )
    except ExcelExportError as exc:
        _patch_job(
            job_id, status="failed", stage="failed",
            message="export failed", error=str(exc),
        )
    except Exception as exc:  # 兜底
        logger.exception("excel export crashed job=%s run=%s", job_id, run_id)
        _patch_job(
            job_id, status="failed", stage="failed",
            message="export crashed", error=str(exc),
        )
