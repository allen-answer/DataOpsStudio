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


# ─── Kimi K2.6 兼容性回归（temperature + thinking + reasoning_content） ────────


class _FakeResp:
    def __init__(self, body: dict):
        self._body = json.dumps(body).encode("utf-8")
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def read(self): return self._body


def _setup_kimi_env(monkeypatch):
    monkeypatch.setenv("DATAOPS_LINEAGE_AI_PROVIDER", "openai")
    monkeypatch.setenv("DATAOPS_LINEAGE_AI_BASE_URL", "https://api.moonshot.cn/v1")
    monkeypatch.setenv("DATAOPS_LINEAGE_AI_MODEL", "kimi-k2-0905-preview")
    monkeypatch.setenv("DATAOPS_LINEAGE_AI_API_KEY", "sk-x")


def test_temperature_defaults_to_nonzero(monkeypatch):
    """Kimi K2.6 不接受 temperature=0；默认应 > 0。"""
    _setup_kimi_env(monkeypatch)
    captured: dict = {}

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return _FakeResp({"choices": [{"message": {"content": "{}"}}]})

    monkeypatch.setattr(lineage_ai.urllib.request, "urlopen", fake_urlopen)
    lineage_ai.enrich_lineage_result({"graph_edges": [], "report": {"summary": {}}}, enabled=True)
    assert captured["payload"]["temperature"] > 0, "Kimi 不接受 0；必须 > 0"


def test_temperature_env_override(monkeypatch):
    _setup_kimi_env(monkeypatch)
    monkeypatch.setenv("DATAOPS_LINEAGE_AI_TEMPERATURE", "0.7")
    captured: dict = {}

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return _FakeResp({"choices": [{"message": {"content": "{}"}}]})

    monkeypatch.setattr(lineage_ai.urllib.request, "urlopen", fake_urlopen)
    lineage_ai.enrich_lineage_result({"graph_edges": [], "report": {"summary": {}}}, enabled=True)
    assert captured["payload"]["temperature"] == 0.7


def test_enable_thinking_false_by_default(monkeypatch):
    """默认 disable_thinking=true → body 里发 enable_thinking=false 让 Kimi 跳过思考。"""
    _setup_kimi_env(monkeypatch)
    captured: dict = {}

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return _FakeResp({"choices": [{"message": {"content": "{}"}}]})

    monkeypatch.setattr(lineage_ai.urllib.request, "urlopen", fake_urlopen)
    lineage_ai.enrich_lineage_result({"graph_edges": [], "report": {"summary": {}}}, enabled=True)
    assert captured["payload"].get("enable_thinking") is False


def test_enable_thinking_can_be_disabled_by_env(monkeypatch):
    """env DATAOPS_LINEAGE_AI_DISABLE_THINKING=false → body 不带 enable_thinking 字段。"""
    _setup_kimi_env(monkeypatch)
    monkeypatch.setenv("DATAOPS_LINEAGE_AI_DISABLE_THINKING", "false")
    captured: dict = {}

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return _FakeResp({"choices": [{"message": {"content": "{}"}}]})

    monkeypatch.setattr(lineage_ai.urllib.request, "urlopen", fake_urlopen)
    lineage_ai.enrich_lineage_result({"graph_edges": [], "report": {"summary": {}}}, enabled=True)
    assert "enable_thinking" not in captured["payload"]


def test_reasoning_content_fallback_when_content_empty(monkeypatch):
    """thinking 模式下 content 为空 / 仅 reasoning_content 有 JSON → 仍能解析。"""
    _setup_kimi_env(monkeypatch)

    def fake_urlopen(request, timeout):
        return _FakeResp({"choices": [{"message": {
            "content": "",
            "reasoning_content": json.dumps({"summary": "from-thinking"}),
        }}]})

    monkeypatch.setattr(lineage_ai.urllib.request, "urlopen", fake_urlopen)
    result = lineage_ai.enrich_lineage_result({"graph_edges": [], "report": {"summary": {}}}, enabled=True)
    assert result["ai_enrichment"]["summary"] == "from-thinking"
