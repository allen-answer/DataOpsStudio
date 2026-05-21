"""项目级资源授权 helper —— 规则见 docs/PROJECT_AUTHORIZATION.md。

- admin：所有资源可见可改（accessible_project_ids 返回 None = 无限制）
- editor / viewer：只能访问自己作为 owner / member 的项目的资源
- project_id 为空的资源 = 全局资源，所有已登录用户可见

放在私有模块（`_authz`）下表明它不是对外 API 的一部分。对 project_store /
repositories / workflow_history 的 import 全部 lazy —— 避免与 `app.api.projects`
等模块的 import 顺序耦合。
"""
from __future__ import annotations

from typing import Callable, Iterable, TypeVar

from fastapi import HTTPException, status

from app.models import User

T = TypeVar("T")


def accessible_project_ids(user: User) -> set[str] | None:
    """该用户作为 owner / member 的 project_id 集合。

    admin 返回 None —— 语义是「无限制 / 全部可见」，调用方据此跳过过滤。
    """
    if user.role == "admin":
        return None
    from app.api.projects import project_store

    return {
        p.id
        for p in project_store.list()
        if p.owner_id == user.id or user.id in p.members
    }


def can_access_project(user: User, project_id: str) -> bool:
    """用户能否访问归属于 `project_id` 的资源。

    project_id 为空 = 全局资源，所有登录用户可访问。
    """
    if user.role == "admin":
        return True
    if not (project_id or ""):
        return True  # 全局资源
    allowed = accessible_project_ids(user)
    return allowed is None or project_id in allowed


def filter_by_project(
    items: Iterable[T],
    user: User,
    *,
    project_id_of: Callable[[T], str] = lambda x: getattr(x, "project_id", "") or "",
) -> list[T]:
    """按项目可见性过滤资源列表。空 project_id = 全局，所有人可见。

    admin 不过滤直接返回全部。非 admin 只算一次 accessible_project_ids，
    避免 per-item 重复展开 project_store。
    """
    if user.role == "admin":
        return list(items)
    allowed = accessible_project_ids(user) or set()
    return [
        it for it in items
        if not project_id_of(it) or project_id_of(it) in allowed
    ]


def require_project_access(
    user: User,
    project_id: str,
    *,
    detail: str = "无权访问该资源所在项目",
) -> None:
    """资源所属项目无权访问 → 403。role 门槛由 require_role 单独把关。"""
    if not can_access_project(user, project_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def require_datasource_access(
    current_user: User,
    datasource_id: str,
    *,
    detail: str = "无权访问该数据源",
):
    """直接接 `datasource_id` 的接口（SQL / EXPLAIN / 字段预览 / introspect）
    必须走这个 helper，否则任意持有 id 的登录用户都能跨项目读库。

    - datasource 不存在 → 404
    - datasource 存在但当前用户对 `datasource.project_id` 无权 → 403
    - 通过则返回 `DataSource` 对象，调用方复用，避免重复 `datasource_store.get`

    `detail` 参数仅作用于 403 文案；404 固定为 "数据源不存在"（既能避免被
    用户拿来枚举 id，也跟既有 require_project_access 的语义一致）。
    """
    from app.services.repositories import datasource_store

    datasource = datasource_store.get(datasource_id)
    if datasource is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="数据源不存在")
    if not can_access_project(current_user, datasource.project_id or ""):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)
    return datasource


# ─── 结果文件 → 归属项目解析 ──────────────────────────────────────────────────
# /results/* 下载不能只看登录态：先把文件解析回它所属项目再校验权限。


def compare_result_project_id(run_id: str) -> tuple[str, bool]:
    """解析 results/<run_id>.json 的 task_id → task.project_id。

    返回 (project_id, resolved)。resolved=False 表示无法归属（文件缺失 /
    无 task_id / task 已删的孤儿 run）。
    """
    import json

    from app.utils import paths

    results_dir = paths.RESULTS_DIR
    path = (results_dir / f"{run_id}.json").resolve()
    try:
        if results_dir.resolve() not in path.parents or not path.exists():
            return ("", False)
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ("", False)
    task_id = str(data.get("task_id") or "")
    if not task_id:
        return ("", False)
    from app.services.repositories import task_store

    task = task_store.get(task_id)
    if task is None:
        return ("", False)
    return (task.project_id or "", True)


def result_download_project_id(filename: str) -> tuple[str, bool]:
    """把 /results/{filename} 解析回归属项目。

    - `<run_id>.json` / `<run_id>.xlsx` → 对比 / 血缘结果，走 task 反查
    - `workflow_runs/<run_id>/...` → 作业流产物，走 workflow 反查
    - 其它（上传文件 / 未知）→ resolved=False，调用方回落到仅登录态

    返回 (project_id, resolved)。
    """
    parts = [p for p in filename.replace("\\", "/").split("/") if p]
    if not parts:
        return ("", False)

    if parts[0] == "workflow_runs" and len(parts) >= 2:
        run_id = parts[1]
        if run_id.endswith(".json"):
            run_id = run_id[:-5]
        from app.services.workflow_history import get_workflow_run

        run = get_workflow_run(run_id)
        if not run:
            return ("", False)
        from app.services.repositories import workflow_store

        workflow = workflow_store.get(str(run.get("workflow_id") or ""))
        if workflow is None:
            return ("", False)
        return (workflow.project_id or "", True)

    if len(parts) == 1:
        stem = parts[0].rsplit(".", 1)[0]
        return compare_result_project_id(stem)

    return ("", False)
