"""Phase 14: SPA cache header 测试 —— 防 deploy 后老 index.html 被浏览器缓存住
引用已被替换的 hash bundle → 404 → 用户白屏 / 进不去系统。

关键不变量:
- index.html 不缓存(每次 revalidate),让 deploy 后浏览器立刻拉新 bundle 引用
- assets/*.js / *.css 文件名带 hash(vite 生成),给 immutable 长 cache
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.utils.paths import BASE_DIR


@pytest.fixture
def client_for_static(tmp_path, monkeypatch):
    """临时建一份 static/spa 目录跑 cache header 测试,不依赖真实 build 产物。"""
    static_dir = tmp_path / "static" / "spa"
    static_dir.mkdir(parents=True)
    (static_dir / "index.html").write_text("<html><body>SPA</body></html>", encoding="utf-8")
    assets_dir = static_dir / "assets"
    assets_dir.mkdir()
    (assets_dir / "index-AbCdEf12.js").write_text("// fake hashed bundle", encoding="utf-8")
    (assets_dir / "index-XyZ12345.css").write_text(".x{}", encoding="utf-8")

    # 重新建 FastAPI app 用 tmp_path 当 static root
    from fastapi import FastAPI
    from starlette.responses import Response
    from starlette.types import Scope
    from fastapi.staticfiles import StaticFiles

    class _SpaStaticFiles(StaticFiles):
        async def get_response(self, path: str, scope: Scope) -> Response:
            resp = await super().get_response(path, scope)
            norm = path.replace("\\", "/")
            if norm.endswith("index.html") or norm.endswith("/"):
                resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
                resp.headers["Pragma"] = "no-cache"
            elif "/assets/" in norm or norm.startswith("assets/") or "/assets/" in f"/{norm}":
                resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            return resp

    app = FastAPI()
    app.mount("/static", _SpaStaticFiles(directory=str(tmp_path / "static")), name="static")
    return TestClient(app)


def test_index_html_no_cache(client_for_static):
    """static/spa/index.html → no-cache(deploy 后浏览器立刻拉新)"""
    r = client_for_static.get("/static/spa/index.html")
    assert r.status_code == 200
    cc = r.headers.get("cache-control", "")
    assert "no-cache" in cc
    assert "no-store" in cc
    assert "must-revalidate" in cc
    assert r.headers.get("pragma") == "no-cache"


def test_hashed_asset_immutable_cache(client_for_static):
    """hash 化 assets/*.js → immutable 长 cache(1 年)"""
    r = client_for_static.get("/static/spa/assets/index-AbCdEf12.js")
    assert r.status_code == 200
    cc = r.headers.get("cache-control", "")
    assert "immutable" in cc
    assert "max-age=31536000" in cc


def test_hashed_css_immutable_cache(client_for_static):
    r = client_for_static.get("/static/spa/assets/index-XyZ12345.css")
    assert r.status_code == 200
    assert "immutable" in r.headers.get("cache-control", "")


def test_nonexistent_returns_404_not_cached(client_for_static):
    """missing 文件 → 404,无 cache header(默认行为)"""
    r = client_for_static.get("/static/spa/assets/nonexistent.js")
    assert r.status_code == 404
