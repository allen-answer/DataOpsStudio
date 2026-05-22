"""对比结果读取层 —— 双格式（legacy json / parquet 目录）归一化。

切片 C：reader 检测 `results/<run_id>/meta.json` 走 parquet 路径，
否则回落到 `results/<run_id>.json` 走 legacy 路径。两路返回同一形态
envelope dict（带 buckets 元清单），让上层 API / history 服务无感知。

跟 writer 协议（`app/compare/result_writer.py`）成对：parquet writer 落什么
shape，本模块读回什么 shape。改 meta.json schema 必须同步 bump
`PARQUET_FORMAT_VERSION` + 改这里的 dispatch。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.utils.paths import RESULTS_DIR


_BUCKET_NAMES: tuple[str, ...] = ("only_source", "only_target", "diff", "same")


class RunNotFound(KeyError):
    """run_id 既无 legacy json 也无 parquet 目录。"""


class BucketNotAvailable(KeyError):
    """bucket 在 meta 里是 count_only / 行数 0 / path=null，没有可读 parquet。

    调用方应根据 meta 提前判断，避免对没有 parquet 的桶调 read_bucket。
    单独抛出方便上层把 4xx 跟"run 不存在"(404) 区分开。
    """


def _run_dir(run_id: str) -> Path:
    """`results/<run_id>/`；不保证存在。"""
    return RESULTS_DIR / run_id


def _legacy_path(run_id: str) -> Path:
    """`results/<run_id>.json`；不保证存在。"""
    return RESULTS_DIR / f"{run_id}.json"


def detect_format(run_id: str) -> str:
    """返回 "parquet" / "json" / "missing"。

    parquet 优先（同一 run_id 同时存在两种是异常但不抛——按 parquet 走）。
    """
    if (_run_dir(run_id) / "meta.json").exists():
        return "parquet"
    if _legacy_path(run_id).exists():
        return "json"
    return "missing"


def load_run_meta(run_id: str) -> dict[str, Any]:
    """读 run envelope（不含完整 buckets，只含 buckets 元数据 + 旧格式的 samples）。

    parquet 格式：直接返回 meta.json（已是 envelope + buckets 清单）。
    json 格式：把 legacy buckets 转成 parquet 风格的 buckets 清单（每桶
    `mode="full"` + `rows=count` + 不带 path），让前端 / API 不用 detect format。

    抛 RunNotFound 表示 run_id 既无 parquet 目录也无 legacy json。
    """
    fmt = detect_format(run_id)
    if fmt == "missing":
        raise RunNotFound(run_id)
    if fmt == "parquet":
        meta_path = _run_dir(run_id) / "meta.json"
        return json.loads(meta_path.read_text(encoding="utf-8"))
    # json 格式：把 buckets list 折叠成 metadata 清单
    data = json.loads(_legacy_path(run_id).read_text(encoding="utf-8"))
    buckets_data = data.get("buckets") or {}
    bucket_metas = [
        {
            "name": name,
            "path": None,                  # legacy 无独立文件
            "rows": len(buckets_data.get(name, [])),
            "mode": "full",                # legacy 整桶在 buckets[name]
        }
        for name in _BUCKET_NAMES
    ]
    envelope = {k: v for k, v in data.items() if k != "buckets"}
    envelope["buckets"] = bucket_metas
    envelope["format"] = "json"
    envelope["format_version"] = 0
    return envelope


def read_bucket(
    run_id: str,
    bucket: str,
    *,
    offset: int = 0,
    limit: int = 100,
) -> dict[str, Any]:
    """按桶分页读行。返回 `{rows, total, offset, limit, mode}`。

    `mode` 是该桶在 meta 里的模式：
    - `"full"`：parquet 全量落盘（或 legacy 全量在 json buckets[name]）
    - `"count_only"`：仅 count + sample；rows 返回 sample（offset 忽略，
       limit clip）

    parquet 路径走 pyarrow `iter_batches`，按 batch 顺序扫过累计 skip
    到 offset 再 take limit。**第一阶段不承诺真随机访问**——大 offset
    仍要解码前面的 row group（设计文档 §7 已注明）。

    bucket 是 mode=count_only 且 sample 列表存在时仍能"读"，返回 sample
    内容。如果 mode=full 但 parquet 文件不存在（rows=0 空桶不写文件），
    返回空 rows + total=0。
    """
    if bucket not in _BUCKET_NAMES:
        raise ValueError(f"unknown bucket: {bucket!r}; expected one of {_BUCKET_NAMES}")
    if offset < 0 or limit <= 0:
        raise ValueError("offset must be >= 0, limit must be > 0")

    fmt = detect_format(run_id)
    if fmt == "missing":
        raise RunNotFound(run_id)

    if fmt == "json":
        data = json.loads(_legacy_path(run_id).read_text(encoding="utf-8"))
        all_rows = (data.get("buckets") or {}).get(bucket, [])
        return {
            "rows": all_rows[offset : offset + limit],
            "total": len(all_rows),
            "offset": offset,
            "limit": limit,
            "mode": "full",
        }

    # parquet 格式：先看 meta 决定 path / mode
    meta = load_run_meta(run_id)
    bucket_meta = next((b for b in meta["buckets"] if b["name"] == bucket), None)
    if bucket_meta is None:
        return {"rows": [], "total": 0, "offset": offset, "limit": limit, "mode": "full"}

    mode = bucket_meta.get("mode") or "full"
    total = int(bucket_meta.get("rows") or 0)

    if mode == "count_only":
        sample = list(bucket_meta.get("sample") or [])
        # count_only 时 sample 已经在内存里，offset 仍按数组 slice
        # （sample 通常 < 200 行，offset 偏大就空数组）
        return {
            "rows": sample[offset : offset + limit],
            "total": total,
            "offset": offset,
            "limit": limit,
            "mode": mode,
        }

    parquet_path_name = bucket_meta.get("path")
    if not parquet_path_name:
        # mode=full 但 path=null —— 空桶不写文件的约定，total 应当也是 0
        return {"rows": [], "total": total, "offset": offset, "limit": limit, "mode": mode}

    import pyarrow.parquet as pq

    parquet_path = _run_dir(run_id) / parquet_path_name
    if not parquet_path.exists():
        raise BucketNotAvailable(f"{run_id}/{bucket}")

    pq_file = pq.ParquetFile(parquet_path)
    out: list[dict[str, Any]] = []
    skipped = 0
    # 按 row group 顺序扫；行 group 大小由 writer 决定。
    # batch_size 取 min(limit, 1000) 控制单次 decode 内存。
    for batch in pq_file.iter_batches(batch_size=max(min(limit, 1000), 1)):
        rows = batch.to_pylist()
        if skipped + len(rows) <= offset:
            skipped += len(rows)
            continue
        # 切到本 batch 内的起点
        start = max(0, offset - skipped)
        for row in rows[start:]:
            out.append(row)
            if len(out) >= limit:
                break
        skipped += len(rows)
        if len(out) >= limit:
            break
    return {
        "rows": out,
        "total": total,
        "offset": offset,
        "limit": limit,
        "mode": mode,
    }


def iter_bucket_rows(
    run_id: str,
    bucket: str,
    *,
    max_rows: int | None = None,
):
    """切片 F.4：行级流式迭代器 —— 给 Excel write_only 流式导出用。

    yield 行 dict（跟 read_bucket 返回的行 shape 一致），不一次性把整桶
    decode 到内存。`max_rows` 是硬上限，到点 break。

    legacy json 路径：data["buckets"][bucket] 已经在内存（json.loads 时整
    文件都解了），仍逐行 yield 让上层 caller 体验一致；要真正不持完整 dict
    得切 ijson 之类 streaming json parser，老格式已是 legacy 暂不优化。

    parquet 路径：走 `pq.ParquetFile.iter_batches`，按 row group 顺序 decode
    一个 batch yield 完再 decode 下一个；caller 一旦 break，剩下的 batch
    完全不读。

    same 桶 count_only 时 yield meta.json 里的 sample 行；其它 mode 同理。

    抛 RunNotFound / BucketNotAvailable / ValueError 跟 read_bucket 同语义。
    """
    if bucket not in _BUCKET_NAMES:
        raise ValueError(f"unknown bucket: {bucket!r}; expected one of {_BUCKET_NAMES}")

    fmt = detect_format(run_id)
    if fmt == "missing":
        raise RunNotFound(run_id)

    yielded = 0

    def _bound() -> bool:
        return max_rows is not None and yielded >= max_rows

    if fmt == "json":
        data = json.loads(_legacy_path(run_id).read_text(encoding="utf-8"))
        for row in (data.get("buckets") or {}).get(bucket, []):
            if _bound():
                return
            yield row
            yielded += 1
        return

    # parquet：先看 meta 决定 path / mode
    meta = load_run_meta(run_id)
    bucket_meta = next((b for b in meta["buckets"] if b["name"] == bucket), None)
    if bucket_meta is None:
        return

    mode = bucket_meta.get("mode") or "full"
    if mode == "count_only":
        for row in bucket_meta.get("sample") or []:
            if _bound():
                return
            yield row
            yielded += 1
        return

    parquet_path_name = bucket_meta.get("path")
    if not parquet_path_name:
        return
    parquet_path = _run_dir(run_id) / parquet_path_name
    if not parquet_path.exists():
        raise BucketNotAvailable(f"{run_id}/{bucket}")

    import pyarrow.parquet as pq

    pq_file = pq.ParquetFile(parquet_path)
    # batch_size=5000 跟 ParquetResultWriter 默认 batch flush 一致，
    # 一次解码一个 row group 喂下游
    for batch in pq_file.iter_batches(batch_size=5000):
        for row in batch.to_pylist():
            if _bound():
                return
            yield row
            yielded += 1


def delete_run(run_id: str) -> None:
    """删 run 的所有产物。两种格式都试一遍，跟 detect 解耦避免漏文件。

    parquet 路径：rmtree 整目录 + 单独删 `<run_id>.xlsx`（writer 占位仍可能落盘）。
    legacy 路径：删 `<run_id>.json` + `<run_id>.xlsx`。

    至少删了一个文件才算成功，否则抛 KeyError 让上层返回 404。
    """
    import shutil

    deleted = False
    run_dir = _run_dir(run_id).resolve()
    results_dir = RESULTS_DIR.resolve()
    # 防 path traversal：必须落在 RESULTS_DIR 下
    if results_dir in run_dir.parents and run_dir.is_dir():
        shutil.rmtree(run_dir)
        deleted = True
    for suffix in (".json", ".xlsx"):
        path = (RESULTS_DIR / f"{run_id}{suffix}").resolve()
        if results_dir in path.parents and path.exists():
            path.unlink()
            deleted = True
    if not deleted:
        raise KeyError(run_id)
