from __future__ import annotations

import json
import time

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


def test_lineage_ai_disables_kimi_thinking_for_moonshot(monkeypatch):
    captured: dict[str, object] = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return json.dumps({
                "choices": [{
                    "message": {"content": json.dumps({"summary": "kimi ok"})}
                }]
            }).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setenv("DATAOPS_LINEAGE_AI_PROVIDER", "openai")
    monkeypatch.setenv("DATAOPS_LINEAGE_AI_BASE_URL", "https://api.moonshot.cn/v1")
    monkeypatch.setenv("DATAOPS_LINEAGE_AI_MODEL", "kimi-k2.6")
    monkeypatch.setenv("DATAOPS_LINEAGE_AI_API_KEY", "secret")
    monkeypatch.setattr(lineage_ai.urllib.request, "urlopen", fake_urlopen)

    result = lineage_ai.enrich_lineage_result({"graph_edges": [], "report": {"summary": {}}}, enabled=True)

    assert captured["payload"]["thinking"] == {"type": "disabled"}
    assert captured["payload"]["max_tokens"] == 1200
    assert "temperature" not in captured["payload"]
    assert "response_format" not in captured["payload"]
    assert result["ai_enrichment"]["summary"] == "kimi ok"


def test_lineage_ai_deepseek_reasoner_uses_deepseek_profile(monkeypatch):
    captured: dict[str, object] = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return json.dumps({
                "choices": [{
                    "message": {"content": json.dumps({"summary": "deepseek ok"})}
                }]
            }).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setenv("DATAOPS_LINEAGE_AI_PROVIDER", "openai")
    monkeypatch.setenv("DATAOPS_LINEAGE_AI_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("DATAOPS_LINEAGE_AI_MODEL", "deepseek-reasoner")
    monkeypatch.setenv("DATAOPS_LINEAGE_AI_API_KEY", "secret")
    monkeypatch.setattr(lineage_ai.urllib.request, "urlopen", fake_urlopen)

    result = lineage_ai.enrich_lineage_result({"graph_edges": [], "report": {"summary": {}}}, enabled=True)

    assert captured["payload"]["response_format"] == {"type": "json_object"}
    assert captured["payload"]["max_tokens"] == 4096
    assert "temperature" not in captured["payload"]
    assert "thinking" not in captured["payload"]
    assert result["ai_enrichment"]["summary"] == "deepseek ok"


def test_lineage_ai_empty_content_with_reasoning_is_error(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return json.dumps({
                "choices": [{
                    "finish_reason": "length",
                    "message": {
                        "content": "",
                        "reasoning_content": "reasoning consumed the budget",
                    },
                }]
            }).encode("utf-8")

    monkeypatch.setenv("DATAOPS_LINEAGE_AI_PROVIDER", "openai")
    monkeypatch.setenv("DATAOPS_LINEAGE_AI_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("DATAOPS_LINEAGE_AI_MODEL", "deepseek-reasoner")
    monkeypatch.setenv("DATAOPS_LINEAGE_AI_API_KEY", "secret")
    monkeypatch.setattr(lineage_ai.urllib.request, "urlopen", lambda request, timeout: FakeResponse())

    result = lineage_ai.enrich_lineage_result({"graph_edges": [], "report": {"summary": {}}}, enabled=True)

    assert result["ai_enrichment"]["status"] == "error"
    assert "empty content" in result["ai_enrichment"]["error"]


def test_lineage_ai_anthropic_compatible_provider(monkeypatch):
    captured: dict[str, object] = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return json.dumps({
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "summary": "anthropic ok",
                        "suggestions": [{"message": "review joins"}],
                    }),
                }]
            }).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["headers"] = {key.lower(): value for key, value in request.headers.items()}
        return FakeResponse()

    monkeypatch.setenv("DATAOPS_LINEAGE_AI_PROVIDER", "anthropic-compatible")
    monkeypatch.setenv("DATAOPS_LINEAGE_AI_BASE_URL", "https://api.deepseek.com/anthropic")
    monkeypatch.setenv("DATAOPS_LINEAGE_AI_MODEL", "deepseek-chat")
    monkeypatch.setenv("DATAOPS_LINEAGE_AI_API_KEY", "secret")
    monkeypatch.setattr(lineage_ai.urllib.request, "urlopen", fake_urlopen)

    result = lineage_ai.enrich_lineage_result({"graph_edges": [], "report": {"summary": {}}}, enabled=True)

    assert captured["url"] == "https://api.deepseek.com/anthropic/v1/messages"
    assert captured["headers"]["x-api-key"] == "secret"
    assert captured["headers"]["anthropic-version"] == "2023-06-01"
    assert captured["payload"]["model"] == "deepseek-chat"
    assert captured["payload"]["messages"][0]["content"][0]["type"] == "text"
    assert result["ai_enrichment"]["provider"] == "anthropic"
    assert result["ai_enrichment"]["summary"] == "anthropic ok"
    assert result["ai_enrichment"]["suggestions"] == [{"message": "review joins"}]


def test_lineage_ai_accepts_anthropic_base_or_full_message_urls(monkeypatch):
    captured_urls: list[str] = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return json.dumps({
                "content": [{"type": "text", "text": json.dumps({"summary": "ok"})}]
            }).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured_urls.append(request.full_url)
        return FakeResponse()

    monkeypatch.setenv("DATAOPS_LINEAGE_AI_PROVIDER", "anthropic")
    monkeypatch.setenv("DATAOPS_LINEAGE_AI_MODEL", "claude-test")
    monkeypatch.setenv("DATAOPS_LINEAGE_AI_API_KEY", "secret")
    monkeypatch.setattr(lineage_ai.urllib.request, "urlopen", fake_urlopen)

    for base_url in [
        "https://api.anthropic.com",
        "https://api.anthropic.com/v1",
        "https://api.anthropic.com/v1/messages",
        "https://api.deepseek.com/anthropic",
        "https://api.deepseek.com/anthropic/v1",
        "https://api.deepseek.com/anthropic/v1/messages",
    ]:
        monkeypatch.setenv("DATAOPS_LINEAGE_AI_BASE_URL", base_url)
        result = lineage_ai.enrich_lineage_result({"graph_edges": [], "report": {"summary": {}}}, enabled=True)
        assert result["ai_enrichment"]["summary"] == "ok"

    assert captured_urls == [
        "https://api.anthropic.com/v1/messages",
        "https://api.anthropic.com/v1/messages",
        "https://api.anthropic.com/v1/messages",
        "https://api.deepseek.com/anthropic/v1/messages",
        "https://api.deepseek.com/anthropic/v1/messages",
        "https://api.deepseek.com/anthropic/v1/messages",
    ]


def test_lineage_ai_parses_anthropic_markdown_json(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return json.dumps({
                "content": [{
                    "type": "text",
                    "text": "```json\n{\"summary\":\"fenced ok\"}\n```",
                }]
            }).encode("utf-8")

    monkeypatch.setenv("DATAOPS_LINEAGE_AI_PROVIDER", "anthropic")
    monkeypatch.setenv("DATAOPS_LINEAGE_AI_MODEL", "claude-test")
    monkeypatch.setenv("DATAOPS_LINEAGE_AI_API_KEY", "secret")
    monkeypatch.setattr(lineage_ai.urllib.request, "urlopen", lambda request, timeout: FakeResponse())

    result = lineage_ai.enrich_lineage_result({"graph_edges": [], "report": {"summary": {}}}, enabled=True)

    assert result["ai_enrichment"]["summary"] == "fenced ok"


def test_lineage_ai_compacts_large_payload_before_provider(monkeypatch):
    captured: dict[str, object] = {}

    class FakeProvider:
        name = "fake"

        def enrich(self, payload, config):
            captured["payload"] = payload
            captured["chars"] = len(json.dumps(payload, ensure_ascii=False))
            return {"summary": "compact ok"}

    monkeypatch.setenv("DATAOPS_LINEAGE_AI_PROVIDER", "mock")
    monkeypatch.setattr(lineage_ai, "_provider_for", lambda _: FakeProvider())
    result = {
        "graph_edges": [],
        "report": {
            "summary": {},
            "inputs": [{"name": f"input_{i}", "desc": "x" * 1000} for i in range(80)],
            "outputs": [{"name": f"output_{i}", "desc": "y" * 1000} for i in range(80)],
            "table_edges": [{"source": f"s{i}", "target": f"t{i}", "reason": "z" * 1000} for i in range(90)],
            "column_edges": [{"source": f"s.c{i}", "target": f"t.c{i}", "transform": "w" * 1000} for i in range(150)],
        },
        "warnings": [{"message": "warn" * 500} for _ in range(60)],
        "parse_errors": [{"message": "err" * 500} for _ in range(60)],
    }

    enriched = lineage_ai.enrich_lineage_result(result, sql_text="select " + "a" * 10000, enabled=True)

    payload = captured["payload"]
    assert enriched["ai_enrichment"]["summary"] == "compact ok"
    assert len(payload["inputs"]) == 20
    assert len(payload["outputs"]) == 20
    assert len(payload["table_edges"]) == 25
    assert len(payload["column_edges"]) == 30
    assert len(payload["warnings"]) == 15
    assert len(payload["parse_errors"]) == 10
    assert captured["chars"] < 50000


def test_lineage_ai_compacts_kimi_payload_more_aggressively(monkeypatch):
    captured: dict[str, object] = {}

    class FakeProvider:
        name = "fake"

        def enrich(self, payload, config):
            captured["payload"] = payload
            captured["chars"] = len(json.dumps(payload, ensure_ascii=False))
            return {"summary": "compact kimi ok"}

    monkeypatch.setenv("DATAOPS_LINEAGE_AI_PROVIDER", "openai")
    monkeypatch.setenv("DATAOPS_LINEAGE_AI_BASE_URL", "https://api.moonshot.cn/v1")
    monkeypatch.setenv("DATAOPS_LINEAGE_AI_MODEL", "kimi-k2.6")
    monkeypatch.setenv("DATAOPS_LINEAGE_AI_API_KEY", "secret")
    monkeypatch.setattr(lineage_ai, "_provider_for", lambda _: FakeProvider())
    result = {
        "graph_edges": [],
        "report": {
            "summary": {},
            "inputs": [{"name": f"input_{i}", "desc": "x" * 1000} for i in range(80)],
            "outputs": [{"name": f"output_{i}", "desc": "y" * 1000} for i in range(80)],
            "table_edges": [{"source": f"s{i}", "target": f"t{i}", "reason": "z" * 1000} for i in range(90)],
            "column_edges": [{"source": f"s.c{i}", "target": f"t.c{i}", "transform": "w" * 1000} for i in range(150)],
        },
        "warnings": [{"message": "warn" * 500} for _ in range(60)],
        "parse_errors": [{"message": "err" * 500} for _ in range(60)],
    }

    enriched = lineage_ai.enrich_lineage_result(result, sql_text="select " + "a" * 10000, enabled=True)

    payload = captured["payload"]
    assert enriched["ai_enrichment"]["summary"] == "compact kimi ok"
    assert len(payload["inputs"]) == 12
    assert len(payload["outputs"]) == 12
    assert len(payload["table_edges"]) == 14
    assert len(payload["column_edges"]) == 18
    assert captured["chars"] < 20000


def test_lineage_ai_normalizes_string_items_and_rejects_empty_enrichment(monkeypatch):
    assert lineage_ai._normalize_enrichment(
        {"suggestions": ["review this"], "risks": ["risk one"], "column_hints": ["hint one"]},
        provider="mock",
        model="m",
        elapsed_seconds=1,
    )["suggestions"] == [{"message": "review this"}]

    empty = lineage_ai._normalize_enrichment({}, provider="mock", model="m", elapsed_seconds=1)
    assert empty["status"] == "error"
    assert "empty AI enrichment" in empty["error"]


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

    assert result["ai_enrichment"]["status"] == "pending"
    assert result["ai_enrichment"]["provider"] == "mock"

    job_id = result["ai_enrichment"]["job_id"]
    for _ in range(20):
        job = lineage_ai.get_lineage_ai_job(job_id)
        if job and job["status"] != "pending":
            break
        time.sleep(0.05)

    assert job["status"] == "success"
    assert job["provider"] == "mock"


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
