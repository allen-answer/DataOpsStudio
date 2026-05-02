"""数据源 CRUD + 连通性测试。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.dbclients.factory import test_connection
from app.models import ConnectionTestResult, DataSource, DataSourceCreate, OkResponse
from app.services.repositories import datasource_store


router = APIRouter()


@router.get("/api/datasources", response_model=list[DataSource])
def list_datasources():
    return datasource_store.list()


@router.post("/api/datasources", response_model=DataSource)
def create_datasource(payload: DataSourceCreate):
    return datasource_store.create(payload)


@router.put("/api/datasources/{datasource_id}", response_model=DataSource)
def update_datasource(datasource_id: str, payload: DataSourceCreate):
    try:
        return datasource_store.update(datasource_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Datasource not found") from exc


@router.delete("/api/datasources/{datasource_id}", response_model=OkResponse)
def delete_datasource(datasource_id: str):
    try:
        datasource_store.delete(datasource_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Datasource not found") from exc
    return {"ok": True}


@router.post("/api/datasources/{datasource_id}/test", response_model=ConnectionTestResult)
def test_datasource(datasource_id: str):
    datasource = datasource_store.get(datasource_id)
    if datasource is None:
        raise HTTPException(status_code=404, detail="Datasource not found")
    try:
        return test_connection(datasource)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"连接失败：{exc}") from exc
