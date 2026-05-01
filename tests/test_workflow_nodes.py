"""Tests for the workflow node runners (lineage, http).
The compare runner is exercised end-to-end via the API + integration tests;
here we focus on the new node types added in slice 2.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.workflow_nodes import run_http_node, run_lineage_node, run_excel_export_node


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


# --- excel_export runner ---

def test_excel_export_requires_at_least_one_enabled_sheet():
    with pytest.raises(ValueError, match="enabled sheet"):
        run_excel_export_node({"filename": "x.xlsx", "sheets": []}, {})

    with pytest.raises(ValueError, match="enabled sheet"):
        run_excel_export_node({"filename": "x.xlsx", "sheets": [{"id": "s1", "enabled": False}]}, {})


def test_excel_export_emits_sheet_descriptors():
    out = run_excel_export_node({
        "filename": "DataCompare_2026-05-01.xlsx",
        "sheets": [
            {"id": "summary", "enabled": True,  "sheet_name": "Summary", "source": "summary",       "max_rows": 100000},
            {"id": "diff",    "enabled": True,  "sheet_name": "Diff",    "source": "diff",          "max_rows": 50000},
            {"id": "skip",    "enabled": False, "sheet_name": "X",       "source": "only_source",   "max_rows": 1000},
        ],
    }, {})

    assert out["filename"] == "DataCompare_2026-05-01.xlsx"
    assert out["sheet_count"] == 2   # disabled sheet excluded
    assert [s["name"] for s in out["sheets"]] == ["Summary", "Diff"]
    assert out["_stub"] is True


def test_excel_export_rejects_non_list_sheets():
    with pytest.raises(ValueError, match="must be a list"):
        run_excel_export_node({"filename": "x.xlsx", "sheets": "not a list"}, {})


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
