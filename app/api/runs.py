"""异步任务（compare / workflow run）状态查询 + 对比结果读取分页（切片 C）。

`/api/runs/{job_id}` 是 async job 状态；`/api/runs/{run_id}/meta` 是 compare
结果 envelope；两套用同一 path prefix 是历史命名，job_id 跟 run_id 在路由层
区分（带 `/meta` 后缀走 result reader）。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api._authz import compare_result_project_id, require_project_access
from app.models import JobInfo, User
from app.services.auth import get_current_user, require_role
from app.services.jobs import cancel_job, get_job
from app.services.run_result import (
    BucketNotAvailable,
    RunNotFound,
    load_run_meta,
    read_bucket,
)


router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get("/api/runs/{job_id}", response_model=JobInfo)
def run_status_api(job_id: str):
    try:
        return get_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Run not found") from exc


@router.post("/api/runs/{job_id}/cancel", response_model=JobInfo)
def cancel_run_api(job_id: str, _: object = Depends(require_role("editor"))):
    try:
        return cancel_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Run not found") from exc


# ─── 切片 C：对比结果 reader API（meta + bucket 分页） ───────────────────────


def _check_run_project_access(run_id: str, current: User) -> None:
    """读 run 前的项目级授权 —— 同 /results/* 下载的归属判定。

    无法归属（孤儿 / 文件缺失）一律 404 让上层快速失败。
    """
    project_id, resolved = compare_result_project_id(run_id)
    if not resolved:
        raise HTTPException(status_code=404, detail="Run not found")
    require_project_access(current, project_id, detail="无权访问该项目的对比结果")


@router.get("/api/runs/{run_id}/meta")
def get_run_meta_api(
    run_id: str,
    current: User = Depends(get_current_user),
) -> dict[str, Any]:
    """返回 run envelope（含 buckets 元清单 + summary）。

    legacy json 模式：把 buckets 转成 metadata 清单（每桶 mode=full + rows=count）。
    parquet 模式：直接返回 meta.json 原内容。

    本端点不返回任何 bucket 的完整行数据 —— 那个走 /buckets/<bucket>。
    """
    _check_run_project_access(run_id, current)
    try:
        return load_run_meta(run_id)
    except RunNotFound:
        raise HTTPException(status_code=404, detail="Run not found")


@router.get("/api/runs/{run_id}/buckets/{bucket}")
def get_run_bucket_api(
    run_id: str,
    bucket: str,
    offset: int = Query(0, ge=0, description="跳过前 N 行；0 = 从头"),
    limit: int = Query(100, ge=1, le=1000, description="单次返回最多 N 行"),
    current: User = Depends(get_current_user),
) -> dict[str, Any]:
    """按 (offset, limit) 分页读某桶的行。

    返回 `{rows, total, offset, limit, mode}`。
    - `mode=full` 时 rows 是 parquet/json 全量分片
    - `mode=count_only` 时 rows 来自 meta.json 的 sample（上限通常 100，
      offset 仍按数组 slice）
    """
    _check_run_project_access(run_id, current)
    try:
        return read_bucket(run_id, bucket, offset=offset, limit=limit)
    except RunNotFound:
        raise HTTPException(status_code=404, detail="Run not found")
    except BucketNotAvailable as exc:
        raise HTTPException(status_code=410, detail=f"bucket data missing: {exc}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
