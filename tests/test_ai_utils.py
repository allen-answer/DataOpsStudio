"""通用 AI 工具 endpoint 测试（错误翻译 / 字段映射推荐）。"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.services.lineage_ai import LineageAIConfig


# `client` fixture 来自 conftest.py（admin-authed）。本文件原 _login() helper
# 仍保留供个别测试直接拿 token 用（如把 header 设到自定义 request）。


def _login(client) -> str:
    """从 admin client 上反查 token（兼容老测试调用方式）。"""
    auth_header = client.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]
    # 兜底：未带 token（不应发生）走一次 login
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    return r.json()["access_token"]


class _FakeResp:
    def __init__(self, body: dict):
        self._body = json.dumps(body).encode("utf-8")
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def read(self): return self._body


def _setup_fake_openai(monkeypatch, body: dict):
    def fake_urlopen(req, timeout):
        return _FakeResp({"choices": [{"message": {"content": json.dumps(body, ensure_ascii=False)}}]})
    monkeypatch.setattr("app.services.lineage_ai.urllib.request.urlopen", fake_urlopen)


def _enable_kimi(monkeypatch):
    """让 _config 返回启用了 Kimi 的配置。"""
    fake = LineageAIConfig(provider="openai", model="kimi-k2.6", api_key="sk", base_url="https://api.x/v1", timeout_seconds=5)
    monkeypatch.setattr("app.api.ai_utils._ai_config", lambda: fake)


# ─── translate-error ──────────────────────────────────────────────────────────


def test_translate_error_requires_text(client):
    token = _login(client)
    r = client.post("/api/ai/translate-error", json={}, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 400


def test_translate_error_provider_off_returns_disabled(client):
    token = _login(client)
    r = client.post("/api/ai/translate-error", json={"error_text": "ORA-00942"},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "未启用" in body.get("error", "") or "off" in body.get("provider", "")


def test_translate_error_success(client, monkeypatch):
    token = _login(client)
    _enable_kimi(monkeypatch)
    _setup_fake_openai(monkeypatch, {
        "translation": "ORA-00942：表或视图 xx 不存在",
        "suggestions": ["核对表名拼写", "检查权限", "确认 schema"],
    })
    r = client.post("/api/ai/translate-error",
                    json={"error_text": "ORA-00942: table or view does not exist",
                          "sql_excerpt": "SELECT * FROM xx", "db_type": "oracle"},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "不存在" in body["translation"]
    assert len(body["suggestions"]) == 3


def test_translate_error_provider_exception_graceful(client, monkeypatch):
    token = _login(client)
    _enable_kimi(monkeypatch)
    monkeypatch.setattr(
        "app.services.lineage_ai.urllib.request.urlopen",
        lambda req, timeout: (_ for _ in ()).throw(ConnectionError("nope")),
    )
    r = client.post("/api/ai/translate-error",
                    json={"error_text": "some error"},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "nope" in body.get("error", "")


# ─── suggest-column-mapping ───────────────────────────────────────────────────


def test_suggest_mapping_requires_fields(client):
    token = _login(client)
    r = client.post("/api/ai/suggest-column-mapping", json={"source_fields": []},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 400


def test_suggest_mapping_filters_non_whitelist(client, monkeypatch):
    """AI 给的 source / target 不在传入的白名单里 → filtered。"""
    token = _login(client)
    _enable_kimi(monkeypatch)
    _setup_fake_openai(monkeypatch, {
        "mappings": [
            {"source": "user_id", "target": "userId", "confidence": "high", "reason": "命名风格匹配"},
            {"source": "ghost",   "target": "userId", "confidence": "low",  "reason": "AI 幻觉"},
            {"source": "user_id", "target": "phantom", "confidence": "low", "reason": "AI 幻觉"},
        ],
    })
    r = client.post("/api/ai/suggest-column-mapping", json={
        "source_fields": ["user_id", "name", "amount"],
        "target_fields": ["userId", "fullName", "balance"],
    }, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert len(body["mappings"]) == 1
    assert body["mappings"][0]["source"] == "user_id"
    assert body["mappings"][0]["target"] == "userId"
    assert body["filtered_count"] == 2
    assert "name" in body["unmatched"]
    assert "amount" in body["unmatched"]


def test_suggest_mapping_dedupes_same_source(client, monkeypatch):
    """AI 同一个 source 给多条 → 只取第一条。"""
    token = _login(client)
    _enable_kimi(monkeypatch)
    _setup_fake_openai(monkeypatch, {
        "mappings": [
            {"source": "id", "target": "tid", "confidence": "high", "reason": "first"},
            {"source": "id", "target": "tno", "confidence": "low",  "reason": "duplicate"},
        ],
    })
    r = client.post("/api/ai/suggest-column-mapping", json={
        "source_fields": ["id"],
        "target_fields": ["tid", "tno"],
    }, headers={"Authorization": f"Bearer {token}"})
    body = r.json()
    assert len(body["mappings"]) == 1
    assert body["mappings"][0]["target"] == "tid"
    assert body["filtered_count"] == 1
