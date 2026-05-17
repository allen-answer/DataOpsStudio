"""数据源 CRUD + 连通性测试。

API 层 password 始终脱敏（返回空串）—— 内部 store 仍持明文供连接 / test 用。
update 收到 password='' 视为"保持原值不变"，避免前端编辑表单时 password
被空覆盖。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.dbclients import pool as _pool
from app.dbclients.factory import test_connection
from app.models import ConnectionTestResult, DataSource, DataSourceCreate, OkResponse
from app.services.auth import get_current_user, require_role
from app.services.repositories import datasource_store


# router 级默认：未登录直接 401。mutation endpoint 单独覆盖 require_role("editor")。
router = APIRouter(dependencies=[Depends(get_current_user)])


def _redact(ds: DataSource) -> DataSource:
    """API 出口脱敏：password 清空。原对象不动（store 仍要明文跑 test）。"""
    return ds.model_copy(update={"password": ""})


@router.get("/api/datasources", response_model=list[DataSource])
def list_datasources(project_id: str = ""):
    items = datasource_store.list()
    if project_id:
        items = [ds for ds in items if ds.project_id == project_id or not ds.project_id]
    return [_redact(ds) for ds in items]


@router.post("/api/datasources", response_model=DataSource)
def create_datasource(payload: DataSourceCreate, _: object = Depends(require_role("editor"))):
    return _redact(datasource_store.create(payload))


@router.put("/api/datasources/{datasource_id}", response_model=DataSource)
def update_datasource(datasource_id: str, payload: DataSourceCreate, _: object = Depends(require_role("editor"))):
    # password 留空 = 保持原值（前端编辑表单不强制重输密码）
    if not payload.password:
        existing = datasource_store.get(datasource_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Datasource not found")
        payload = payload.model_copy(update={"password": existing.password})
    try:
        updated = datasource_store.update(datasource_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Datasource not found") from exc
    # 改 host / 密码 / db_type 后旧池连接全失效，清掉避免后续查询打到旧凭证
    _pool.invalidate(datasource_id)
    return _redact(updated)


@router.delete("/api/datasources/{datasource_id}", response_model=OkResponse)
def delete_datasource(datasource_id: str, _: object = Depends(require_role("editor"))):
    try:
        datasource_store.delete(datasource_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Datasource not found") from exc
    _pool.invalidate(datasource_id)
    return {"ok": True}


@router.post("/api/datasources/{datasource_id}/test", response_model=ConnectionTestResult)
def test_datasource(datasource_id: str, _: object = Depends(require_role("editor"))):
    datasource = datasource_store.get(datasource_id)
    if datasource is None:
        raise HTTPException(status_code=404, detail="Datasource not found")
    try:
        return test_connection(datasource)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"连接失败：{exc}") from exc
