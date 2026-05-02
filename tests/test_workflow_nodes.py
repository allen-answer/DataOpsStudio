"""Tests for the workflow node runners (lineage, http).
The compare runner is exercised end-to-end via the API + integration tests;
here we focus on the new node types added in slice 2.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.workflow_nodes import run_http_node, run_lineage_node, run_excel_export_node, run_params_node


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


# --- params runner ---

def test_params_node_resolves_fixed_and_relative_dates():
    out = run_params_node({
        "parameters": [
            {"name": "system_code", "type": "fixed",         "default": "CRM"},
            {"name": "biz_date",    "type": "relative_date", "source": "yesterday"},
            {"name": "channels",    "type": "multi_value",   "default": ["A", "B"]},
        ],
    }, {})
    from datetime import date, timedelta
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    assert out["system_code"] == "CRM"
    assert out["biz_date"] == yesterday
    assert out["channels"] == ["A", "B"]


def test_params_node_caller_overrides_default():
    out = run_params_node({
        "parameters": [{"name": "biz_date", "type": "relative_date", "source": "yesterday"}],
    }, {"biz_date": "2026-12-31"})
    assert out["biz_date"] == "2026-12-31"


def test_params_node_skips_anonymous_entries():
    out = run_params_node({
        "parameters": [
            {"name": "x", "type": "fixed", "default": "1"},
            {"type": "fixed", "default": "ignored"},        # missing name
            {"name": "  ", "type": "fixed", "default": "ws"},  # blank name
        ],
    }, {})
    assert out == {"x": "1"}


# --- excel_export runner ---

def test_excel_export_requires_at_least_one_enabled_sheet():
    with pytest.raises(ValueError, match="enabled sheet"):
        run_excel_export_node({"sheets": []}, {})

    with pytest.raises(ValueError, match="enabled sheet"):
        run_excel_export_node({"sheets": [{"id": "s1", "enabled": False}]}, {})


def test_excel_export_writes_real_xlsx_with_upstream_rows(tmp_path, monkeypatch):
    """Real run_excel_export_node should pull rows from outputs[source_node]
    via dot-path source_field, write them as an actual .xlsx, and return
    real rows_written / file_size."""
    import openpyxl
    monkeypatch.setattr("app.utils.paths.RESULTS_DIR", tmp_path)
    upstream = {
        "compare1": {
            "summary": {"only_source": 1, "only_target": 0, "diff": 2, "same": 5},
            "samples": {
                "diff": [
                    {"id": 1, "old": "a", "new": "b"},
                    {"id": 2, "old": "x", "new": "y"},
                ],
                "only_source": [{"id": 99, "name": "ghost"}],
                "only_target": [],
                "same": [],
            },
        },
    }
    out = run_excel_export_node({
        "sheets": [
            {"id": "diff", "enabled": True, "sheet_name": "差异",
             "source_node": "compare1", "source_field": "samples.diff", "max_rows": 100},
            {"id": "stats", "enabled": True, "sheet_name": "汇总",
             "source_node": "compare1", "source_field": "summary"},
            {"id": "skip", "enabled": False, "sheet_name": "ignored",
             "source_node": "compare1", "source_field": "samples.same"},
        ],
    }, {}, outputs=upstream)

    assert out["sheet_count"] == 2
    file_path = tmp_path / out["filename"]
    assert file_path.exists()
    assert out["file_size"] > 0
    assert out["total_rows_written"] == 3   # 2 diff rows + 1 summary row

    book = openpyxl.load_workbook(file_path)
    assert set(book.sheetnames) == {"差异", "汇总"}
    diff_sheet = book["差异"]
    rows = list(diff_sheet.values)
    assert rows[0] == ("id", "old", "new")
    assert rows[1] == (1, "a", "b")
    assert rows[2] == (2, "x", "y")
    summary_sheet = book["汇总"]
    summary_rows = list(summary_sheet.values)
    assert "diff" in summary_rows[0]   # 表头里至少有 diff 字段
    assert summary_rows[1][summary_rows[0].index("diff")] == 2


def test_excel_export_max_rows_truncation(tmp_path, monkeypatch):
    monkeypatch.setattr("app.utils.paths.RESULTS_DIR", tmp_path)
    upstream = {"src": {"rows": [{"id": i} for i in range(150)]}}
    out = run_excel_export_node({
        "sheets": [{"id": "s", "enabled": True, "sheet_name": "S",
                    "source_node": "src", "source_field": "rows", "max_rows": 10}],
    }, {}, outputs=upstream)
    assert out["sheets"][0]["truncated"] is True
    assert out["sheets"][0]["rows_written"] == 10


def test_excel_export_defaults_source_node_to_first_upstream(tmp_path, monkeypatch):
    """Sheet 没指定 source_node → 用 depends_on 的第一个完成的上游节点。
    覆盖 90% 的"单上游 compare → excel_export"场景，用户不用每个 sheet 都填 source_node。"""
    monkeypatch.setattr("app.utils.paths.RESULTS_DIR", tmp_path)
    upstream = {"compare1": {"samples": {"diff": [{"id": 1, "v": "a"}, {"id": 2, "v": "b"}]}}}
    out = run_excel_export_node({
        "sheets": [
            {"id": "diff", "enabled": True, "sheet_name": "差异",
             "source_field": "samples.diff", "max_rows": 100},   # 注意：没填 source_node
        ],
    }, {}, outputs=upstream, depends_on=["compare1"])
    assert out["sheets"][0]["source_node"] == "compare1"   # 自动用了 depends_on[0]
    assert out["sheets"][0]["rows_written"] == 2
    assert out["sheets"][0]["source_resolved"] is True


def test_excel_export_explicit_source_node_overrides_default(tmp_path, monkeypatch):
    """显式指定 source_node 时不会被 depends_on 缺省覆盖。"""
    monkeypatch.setattr("app.utils.paths.RESULTS_DIR", tmp_path)
    upstream = {
        "c1": {"summary": {"diff": 7}},
        "c2": {"summary": {"diff": 99}},
    }
    out = run_excel_export_node({
        "sheets": [
            {"id": "s", "enabled": True, "sheet_name": "S",
             "source_node": "c2", "source_field": "summary"},
        ],
    }, {}, outputs=upstream, depends_on=["c1", "c2"])
    assert out["sheets"][0]["source_node"] == "c2"
    # 验证读到的是 c2 的 summary 而不是 c1
    import openpyxl
    book = openpyxl.load_workbook(tmp_path / out["filename"])
    rows = list(book["S"].values)
    assert rows[1][rows[0].index("diff")] == 99


def test_excel_export_unresolved_source_writes_empty_sheet(tmp_path, monkeypatch):
    """source_node 不存在或 source_field 路径错 → 空 sheet + source_resolved=False。
    用户能在节点 output 里看到为啥没数据，但整个 export 不会因此 FAILED。"""
    monkeypatch.setattr("app.utils.paths.RESULTS_DIR", tmp_path)
    out = run_excel_export_node({
        "sheets": [
            {"id": "missing", "enabled": True, "sheet_name": "Missing",
             "source_node": "no_such_node", "source_field": "x.y"},
        ],
    }, {}, outputs={})
    assert out["sheets"][0]["source_resolved"] is False
    assert out["sheets"][0]["rows_written"] == 0


def test_excel_export_rejects_non_list_sheets():
    with pytest.raises(ValueError, match="must be a list"):
        run_excel_export_node({"sheets": "not a list"}, {}, outputs={})


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
