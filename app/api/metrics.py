"""Prometheus /metrics 端点 —— text format v0.0.4。"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from app.services.metrics import render_prometheus


router = APIRouter()


@router.get("/metrics", response_class=PlainTextResponse)
def metrics_api() -> str:
    """Prometheus text format。

    无 auth gate —— 跟普遍约定一致（让 Prometheus scraper 不需要单独 token）。
    生产部署应靠网络层（k8s NetworkPolicy / nginx allowlist）限定访问。
    """
    return render_prometheus()
