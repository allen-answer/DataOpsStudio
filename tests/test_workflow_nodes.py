"""Tests for the workflow node runners (lineage, http).
The compare runner is exercised end-to-end via the API + integration tests;
here we focus on the new node types added in slice 2.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.workflow_nodes import run_http_node, run_lineage_node


# --- lineage runner ---

def test_lineage_node_requires_sql():
    with pytest.raises(ValueError, match="config.sql"):
        run_lineage_node({}, {})


def test_lineage_node_calls_analyze_json_with_sql():
    captured: dict = {}

    def fake_analyze_json(payload):
        captured["payload"] = payload
        return {"sources": ["t1"], "targets": ["t2"], "edges": []}

    with patch("app.services.lineage_service.analyze_json", side_effect=fake_analyze_json):
        out = run_lineage_node({"sql": "SELECT * FROM t1", "dialect": "mysql"}, {})

    assert captured["payload"]["sql"] == "SELECT * FROM t1"
    assert captured["payload"]["dialect"] == "mysql"
    assert out["sources"] == ["t1"]


# --- http runner — happy path ---

class _FakeResponse:
    def __init__(self, *, status=200, body=b'{"ok": true}', headers=None):
        self.status = status
        self._body = body
        self.headers = _FakeHeaders(headers or {"Content-Type": "application/json"})

    def read(self, n=None):
        if n is None:
            return self._body
        return self._body[:n]

    def __enter__(self): return self
    def __exit__(self, *exc): return False


class _FakeHeaders:
    def __init__(self, items):
        self._items = list(items.items()) if isinstance(items, dict) else list(items)

    def items(self): return self._items


def test_http_node_returns_status_body_and_parsed_json():
    with patch("urllib.request.urlopen", return_value=_FakeResponse()):
        out = run_http_node({"url": "https://example.com/hook", "method": "POST"}, {})

    assert out["status"] == 200
    assert out["json"] == {"ok": True}
    assert out["body"] == '{"ok": true}'
    assert out["truncated"] is False


def test_http_node_rejects_non_http_scheme():
    with pytest.raises(ValueError, match="http\\(s\\)"):
        run_http_node({"url": "file:///etc/passwd"}, {})


def test_http_node_requires_url():
    with pytest.raises(ValueError, match="config.url"):
        run_http_node({}, {})


def test_http_node_expect_status_mismatch_raises():
    with patch("urllib.request.urlopen", return_value=_FakeResponse(status=500)):
        with pytest.raises(ValueError, match="expected status 200"):
            run_http_node({"url": "https://x.io", "expect_status": 200}, {})


def test_http_node_expect_status_match_succeeds():
    with patch("urllib.request.urlopen", return_value=_FakeResponse(status=204, body=b"")):
        out = run_http_node({"url": "https://x.io", "expect_status": 204}, {})
    assert out["status"] == 204


def test_http_node_surfaces_4xx_body_without_raising():
    """An HTTPError (4xx/5xx) shouldn't kill the node — surface the body so
    users can branch on the response. Hard fail only on transport errors."""
    import urllib.error

    err = urllib.error.HTTPError(
        url="https://x.io", code=404, msg="Not Found",
        hdrs=_FakeHeaders({"X-Foo": "bar"}), fp=None,
    )
    err.read = lambda n=None: b'{"error": "not found"}'

    with patch("urllib.request.urlopen", side_effect=err):
        out = run_http_node({"url": "https://x.io"}, {})

    assert out["status"] == 404
    assert "not found" in out["body"]
    assert "error" in out
