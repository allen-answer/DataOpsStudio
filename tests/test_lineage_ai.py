from __future__ import annotations

import json

import pytest

from app.lineage.analyzer import analyze_sql_lineage
from app.services import lineage_ai, lineage_service
from app.services.lineage_ai_config import (
    get_effective_lineage_ai_config,
    get_public_lineage_ai_config,
    save_lineage_ai_config,
)
from app.services.secret_crypto import is_encrypted


@pytest.fixture(autouse=True)
def isolated_ai_config(tmp_path, monkeypatch):
    cfg = tmp_path / "lineage_ai.json"
    key = tmp_path / ".dataops_secret.key"
    from app.services import lineage_ai_config as lineage_ai_config_svc, secret_crypto as secret_crypto_svc

    monkeypatch.setattr(lineage_ai_config_svc, "LINEAGE_AI_CONFIG_FILE", cfg)
    monkeypatch.setattr(secret_crypto_svc, "LOCAL_SECRET_KEY_FILE", key)
    for name in [
        "DATAOPS_LINEAGE_AI_PROVIDER",
        "DATAOPS_LINEAGE_AI_MODEL",
        "DATAOPS_LINEAGE_AI_BASE_URL",
        "DATAOPS_LINEAGE_AI_API_KEY",
        "DATAOPS_LINEAGE_AI_TIMEOUT_SECONDS",
        "DATAOPS_LINEAGE_AI_INCLUDE_RAW",
    ]:
        monkeypatch.delenv(name, raising=False)
    yield


def test_lineage_ai_default_disabled(monkeypatch):
    monkeypatch.delenv("DATAOPS_LINEAGE_AI_PROVIDER", raising=False)
    result = analyze_sql_lineage("insert into dwd.t select id from ods.s", dialect="mysql")

    enriched = lineage_ai.enrich_lineage_result(result, sql_text="select 1", enabled=False)

    assert enriched["ai_enrichment"]["enabled"] is False
    assert enriched["ai_enrichment"]["status"] == "disabled"


def test_lineage_ai_status_is_non_sensitive(monkeypatch):
    monkeypatch.setenv("DATAOPS_LINEAGE_AI_PROVIDER", "openai")
    monkeypatch.setenv("DATAOPS_LINEAGE_AI_MODEL", "lineage-model")
    monkeypatch.setenv("DATAOPS_LINEAGE_AI_API_KEY", "secret")

    status = lineage_ai.lineage_ai_status()

    assert status["enabled"] is True
    assert status["configured"] is True
    assert status["provider"] == "openai"
    assert status["model"] == "lineage-model"
    assert "api_key" not in status


def test_lineage_ai_mock_provider_is_additive(monkeypatch):
    monkeypatch.setenv("DATAOPS_LINEAGE_AI_PROVIDER", "mock")
    result = analyze_sql_lineage("insert into dwd.t select id from ods.s", dialect="mysql")
    before_edges = list(result["graph_edges"])

    enriched = lineage_ai.enrich_lineage_result(result, sql_text="select 1", enabled=True)

    assert enriched["graph_edges"] == before_edges
    assert enriched["ai_enrichment"]["enabled"] is True
    assert enriched["ai_enrichment"]["provider"] == "mock"
    assert enriched["ai_enrichment"]["suggestions"]


def test_lineage_ai_provider_configured_but_ui_switch_off(monkeypatch):
    monkeypatch.setenv("DATAOPS_LINEAGE_AI_PROVIDER", "mock")
    result = analyze_sql_lineage("insert into dwd.t select id from ods.s", dialect="mysql")

    enriched = lineage_ai.enrich_lineage_result(result, sql_text="select 1", enabled=False)

    assert enriched["ai_enrichment"]["enabled"] is False
    assert enriched["ai_enrichment"]["status"] == "disabled"


def test_lineage_ai_ui_switch_on_but_provider_off(monkeypatch):
    monkeypatch.setenv("DATAOPS_LINEAGE_AI_PROVIDER", "off")
    result = analyze_sql_lineage("insert into dwd.t select id from ods.s", dialect="mysql")

    enriched = lineage_ai.enrich_lineage_result(result, sql_text="select 1", enabled=True)

    assert enriched["ai_enrichment"]["enabled"] is False
    assert enriched["ai_enrichment"]["status"] == "disabled"


def test_lineage_ai_openai_compatible_provider(monkeypatch):
    captured: dict[str, object] = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return json.dumps({
                "choices": [{
                    "message": {
                        "content": json.dumps({
                            "summary": "ok",
                            "suggestions": [{"message": "review dynamic sql"}],
                        })
                    }
                }]
            }).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["auth"] = request.headers.get("Authorization")
        return FakeResponse()

    monkeypatch.setenv("DATAOPS_LINEAGE_AI_PROVIDER", "openai")
    monkeypatch.setenv("DATAOPS_LINEAGE_AI_BASE_URL", "http://ai.local/v1")
    monkeypatch.setenv("DATAOPS_LINEAGE_AI_MODEL", "lineage-model")
    monkeypatch.setenv("DATAOPS_LINEAGE_AI_API_KEY", "secret")
    monkeypatch.setattr(lineage_ai.urllib.request, "urlopen", fake_urlopen)

    result = lineage_ai.enrich_lineage_result({"graph_edges": [], "report": {"summary": {}}}, enabled=True)

    assert captured["url"] == "http://ai.local/v1/chat/completions"
    assert captured["auth"] == "Bearer secret"
    assert captured["payload"]["model"] == "lineage-model"
    assert result["ai_enrichment"]["summary"] == "ok"
    assert result["ai_enrichment"]["suggestions"] == [{"message": "review dynamic sql"}]


def test_lineage_ai_accepts_full_or_bare_openai_compatible_urls(monkeypatch):
    captured_urls: list[str] = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return json.dumps({
                "choices": [{
                    "message": {"content": json.dumps({"summary": "ok"})}
                }]
            }).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured_urls.append(request.full_url)
        return FakeResponse()

    monkeypatch.setenv("DATAOPS_LINEAGE_AI_PROVIDER", "openai")
    monkeypatch.setenv("DATAOPS_LINEAGE_AI_MODEL", "lineage-model")
    monkeypatch.setenv("DATAOPS_LINEAGE_AI_API_KEY", "secret")
    monkeypatch.setattr(lineage_ai.urllib.request, "urlopen", fake_urlopen)

    for base_url in [
        "https://api.moonshot.ai",
        "https://api.moonshot.ai/v1",
        "https://api.moonshot.ai/v1/chat/completions",
    ]:
        monkeypatch.setenv("DATAOPS_LINEAGE_AI_BASE_URL", base_url)
        result = lineage_ai.enrich_lineage_result({"graph_edges": [], "report": {"summary": {}}}, enabled=True)
        assert result["ai_enrichment"]["summary"] == "ok"

    assert captured_urls == [
        "https://api.moonshot.ai/v1/chat/completions",
        "https://api.moonshot.ai/v1/chat/completions",
        "https://api.moonshot.ai/v1/chat/completions",
    ]


def test_lineage_service_attaches_ai_enrichment_disabled(monkeypatch):
    monkeypatch.delenv("DATAOPS_LINEAGE_AI_PROVIDER", raising=False)

    result = lineage_service.analyze_json({
        "sql": "insert into dwd.t select id from ods.s",
        "dialect": "mysql",
    })

    assert result["ai_enrichment"]["status"] == "disabled"


def test_lineage_service_can_enable_mock_ai(monkeypatch):
    monkeypatch.setenv("DATAOPS_LINEAGE_AI_PROVIDER", "mock")

    result = lineage_service.analyze_json({
        "sql": "insert into dwd.t select id from ods.s",
        "dialect": "mysql",
        "ai_enabled": "true",
    })

    assert result["ai_enrichment"]["status"] == "success"
    assert result["ai_enrichment"]["provider"] == "mock"


def test_lineage_ai_config_encrypts_saved_api_key():
    public = save_lineage_ai_config({
        "provider": "openai",
        "model": "lineage-model",
        "base_url": "http://ai.local/v1",
        "api_key": "super-secret-key",
    })

    from app.services import lineage_ai_config as lineage_ai_config_svc

    raw = json.loads(lineage_ai_config_svc.LINEAGE_AI_CONFIG_FILE.read_text(encoding="utf-8"))
    assert "super-secret-key" not in lineage_ai_config_svc.LINEAGE_AI_CONFIG_FILE.read_text(encoding="utf-8")
    assert is_encrypted(raw["api_key_encrypted"])
    assert public["api_key_set"] is True
    assert public["api_key_encrypted"] is True
    assert "api_key" not in public
    assert get_effective_lineage_ai_config()["api_key"] == "super-secret-key"


def test_lineage_ai_config_blank_key_preserves_and_clear_removes():
    save_lineage_ai_config({"provider": "openai", "model": "m1", "api_key": "first-key"})

    save_lineage_ai_config({"provider": "openai", "model": "m2", "api_key": ""})
    assert get_effective_lineage_ai_config()["api_key"] == "first-key"
    assert get_public_lineage_ai_config()["model"] == "m2"

    public = save_lineage_ai_config({"provider": "openai", "model": "m2", "clear_api_key": True})
    assert public["api_key_set"] is False
    assert get_effective_lineage_ai_config()["api_key"] == ""


def test_lineage_ai_connection_test_uses_in_memory_override():
    result = lineage_ai.test_lineage_ai_connection({
        "provider": "mock",
        "model": "demo-model",
        "api_key": "not-saved",
    })

    assert result["ok"] is True
    assert result["provider"] == "mock"
    assert get_public_lineage_ai_config()["api_key_set"] is False


def test_lineage_ai_config_api_is_admin_only_and_non_sensitive(isolated_storage):
    from app.services import auth as auth_svc
    from fastapi.testclient import TestClient
    from main import app

    auth_svc.bootstrap_default_admin()
    client = TestClient(app)

    assert client.get("/api/lineage/ai/config").status_code == 401
    token = client.post("/api/auth/login", json={"username": "admin", "password": "admin"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    response = client.put(
        "/api/lineage/ai/config",
        headers=headers,
        json={"provider": "openai", "model": "lineage-model", "api_key": "api-secret"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["api_key_set"] is True
    assert "api_key" not in payload
    assert "api-secret" not in response.text

    status = client.get("/api/lineage/ai/status").json()
    assert status["api_key_set"] is True
    assert "api_key" not in status
    assert "api-secret" not in json.dumps(status)
