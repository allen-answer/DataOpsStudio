from __future__ import annotations

import json

from app.lineage.analyzer import analyze_sql_lineage
from app.services import lineage_ai, lineage_service


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
