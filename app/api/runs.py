"""异步任务（compare / workflow run）状态查询 + 对比结果读取分页（切片 C）。

`/api/runs/{job_id}` 是 async job 状态；`/api/runs/{run_id}/meta` 是 compare
结果 envelope；两套用同一 path prefix 是历史命名，job_id 跟 run_id 在路由层
区分（带 `/meta` 后缀走 result reader）。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.api._authz import (
    compare_result_project_id,
    job_project_id,
    require_project_access,
)
from app.models import JobInfo, User
from app.services.auth import get_current_user, require_role
from app.services.download_token import issue_download_token
from app.services.excel_export import export_excel_path, submit_excel_export
from app.services.jobs import cancel_job, get_job
from app.services.run_result import (
    BucketNotAvailable,
    RunNotFound,
    load_run_meta,
    read_bucket,
)


router = APIRouter(dependencies=[Depends(get_current_user)])


def _gate_job_access(job: dict, current: User) -> None:
    """切片 F hardening：按 job.kind 反查归属项目；无权 403。

    无法归属（孤儿 / task 已删 / excel_export 指向已删 run）回落仅登录态放行——
    跟 /results/* 下载同语义，不因孤儿状态把现有调用方打爆。
    """
    project_id, resolved = job_project_id(job)
    if not resolved:
        return
    require_project_access(current, project_id, detail="无权查看该项目的异步任务")


@router.get("/api/runs/{job_id}", response_model=JobInfo)
def run_status_api(job_id: str, current: User = Depends(get_current_user)):
    try:
        job = get_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Run not found") from exc
    _gate_job_access(job, current)
    return job


@router.post("/api/runs/{job_id}/cancel", response_model=JobInfo)
def cancel_run_api(job_id: str, current: User = Depends(require_role("editor"))):
    try:
        job = get_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Run not found") from exc
    _gate_job_access(job, current)
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


@router.post("/api/runs/{run_id}/export-excel", response_model=JobInfo)
def export_run_excel_api(
    run_id: str,
    current: User = Depends(require_role("editor")),
) -> dict[str, Any]:
    """切片 E：把指定 run 的 4 桶结果异步导出成 Excel。

    返回 `JobInfo`（kind=excel_export）；前端 poll `/api/runs/{job_id}` 等
    job.status=success 后从 `job.result.download_url` 拉文件。

    - parquet runs：写到 `results/<run_id>/export.xlsx`
    - legacy runs：写到 `results/<run_id>.xlsx`（覆盖 runner 同步落的版本——
      用户场景里是「重新导一份带最新 max_rows 设置的 Excel」）
    """
    _check_run_project_access(run_id, current)
    return submit_excel_export(run_id)


# ─── 切片 P1：签名下载 token ────────────────────────────────────────────────


def _resolve_run_file(run_id: str, kind: str) -> str:
    """把 (run_id, kind) 解析成相对 RESULTS_DIR 的文件路径。文件不存在 → 404。

    走 `run_result` 模块的 RESULTS_DIR / detect_format —— 不在本模块顶层
    绑 RESULTS_DIR，避免又多一处要测试 monkeypatch 的路径引用。
    """
    from app.services import run_result

    results_dir = run_result.RESULTS_DIR
    fmt = run_result.detect_format(run_id)
    if fmt == "missing":
        raise HTTPException(status_code=404, detail="Run not found")
    if kind == "excel":
        path = export_excel_path(run_id)
    elif kind == "result":
        path = (
            results_dir / run_id / "meta.json" if fmt == "parquet"
            else results_dir / f"{run_id}.json"
        )
    else:
        raise HTTPException(status_code=400, detail="kind 必须是 result 或 excel")
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"该 run 没有可下载的 {kind} 文件")
    return path.relative_to(results_dir).as_posix()


@router.post("/api/runs/{run_id}/downloads")
def create_run_download(
    run_id: str,
    payload: dict[str, Any] | None = Body(None),
    current: User = Depends(require_role("editor")),
) -> dict[str, Any]:
    """为指定 run 的结果文件签发短时下载 token。

    body `{kind: "result" | "excel"}`（默认 result）。前端拿 `download_url`
    去 `GET /api/downloads/{token}` 拉文件 —— 取代直接拼可猜的 `/results/<path>`。
    """
    _check_run_project_access(run_id, current)
    kind = str((payload or {}).get("kind") or "result")
    rel = _resolve_run_file(run_id, kind)
    project_id, _ = compare_result_project_id(run_id)
    token, ttl = issue_download_token(
        run_id=run_id, relative_path=rel, project_id=project_id, user_id=current.id,
    )
    return {
        "token": token,
        "download_url": f"/api/downloads/{token}",
        "expires_in": ttl,
        "relative_path": rel,
    }
