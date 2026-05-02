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


def test_excel_export_writes_real_xlsx_with_compare_dataset_shorthand(tmp_path, monkeypatch):
    """dataset='diff' / 'summary' 是 compare 节点的 short-name，runner 自动映射到
    samples.diff / summary。文件落到 results/workflow_runs/<run_id>/exports/。"""
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
             "source_type": "node_output", "node_id": "compare1", "dataset": "diff", "max_rows": 100},
            {"id": "stats", "enabled": True, "sheet_name": "汇总",
             "source_type": "node_output", "node_id": "compare1", "dataset": "summary"},
            {"id": "skip", "enabled": False, "sheet_name": "ignored",
             "source_type": "node_output", "node_id": "compare1", "dataset": "same"},
        ],
    }, {}, outputs=upstream, run_id="testrun123")

    assert out["sheet_count"] == 2
    expected_dir = tmp_path / "workflow_runs" / "testrun123" / "exports"
    file_path = expected_dir / out["filename"]
    assert file_path.exists()
    assert out["relative_path"].startswith("workflow_runs/testrun123/exports/")
    assert out["file_size"] > 0
    assert out["total_rows_written"] == 3   # 2 diff rows + 1 summary row

    book = openpyxl.load_workbook(file_path)
    assert set(book.sheetnames) == {"差异", "汇总"}
    diff_sheet = book["差异"]
    rows = list(diff_sheet.values)
    assert rows[0] == ("id", "old", "new")
    assert rows[1] == (1, "a", "b")
    assert rows[2] == (2, "x", "y")


def test_excel_export_max_rows_truncation(tmp_path, monkeypatch):
    monkeypatch.setattr("app.utils.paths.RESULTS_DIR", tmp_path)
    upstream = {"src": {"rows": [{"id": i} for i in range(150)]}}
    out = run_excel_export_node({
        "sheets": [{"id": "s", "enabled": True, "sheet_name": "S",
                    "node_id": "src", "dataset": "rows", "max_rows": 10}],
    }, {}, outputs=upstream, run_id="r1")
    assert out["sheets"][0]["truncated"] is True
    assert out["sheets"][0]["rows_written"] == 10


def test_excel_export_defaults_node_id_to_first_upstream(tmp_path, monkeypatch):
    """Sheet 没指定 node_id → 用 depends_on 的第一个完成的上游节点。"""
    monkeypatch.setattr("app.utils.paths.RESULTS_DIR", tmp_path)
    upstream = {"compare1": {"samples": {"diff": [{"id": 1, "v": "a"}, {"id": 2, "v": "b"}]}}}
    out = run_excel_export_node({
        "sheets": [
            {"id": "diff", "enabled": True, "sheet_name": "差异",
             "dataset": "diff", "max_rows": 100},   # 没填 node_id
        ],
    }, {}, outputs=upstream, depends_on=["compare1"], run_id="r1")
    assert out["sheets"][0]["node_id"] == "compare1"   # 自动用了 depends_on[0]
    assert out["sheets"][0]["rows_written"] == 2
    assert out["sheets"][0]["source_resolved"] is True


def test_excel_export_explicit_node_id_overrides_default(tmp_path, monkeypatch):
    """显式指定 node_id 时不会被 depends_on 缺省覆盖。"""
    monkeypatch.setattr("app.utils.paths.RESULTS_DIR", tmp_path)
    upstream = {
        "c1": {"summary": {"diff": 7}},
        "c2": {"summary": {"diff": 99}},
    }
    out = run_excel_export_node({
        "sheets": [
            {"id": "s", "enabled": True, "sheet_name": "S",
             "node_id": "c2", "dataset": "summary"},
        ],
    }, {}, outputs=upstream, depends_on=["c1", "c2"], run_id="r1")
    assert out["sheets"][0]["node_id"] == "c2"
    import openpyxl
    book = openpyxl.load_workbook(tmp_path / "workflow_runs" / "r1" / "exports" / out["filename"])
    rows = list(book["S"].values)
    assert rows[1][rows[0].index("diff")] == 99


def test_excel_export_unresolved_source_writes_empty_sheet(tmp_path, monkeypatch):
    """node_id 不存在 → 空 sheet + source_resolved=False，整个 export 不 FAILED。"""
    monkeypatch.setattr("app.utils.paths.RESULTS_DIR", tmp_path)
    out = run_excel_export_node({
        "sheets": [
            {"id": "missing", "enabled": True, "sheet_name": "Missing",
             "node_id": "no_such_node", "dataset": "x"},
        ],
    }, {}, outputs={}, run_id="r1")
    assert out["sheets"][0]["source_resolved"] is False
    assert out["sheets"][0]["rows_written"] == 0


def test_excel_export_legacy_field_names_still_work(tmp_path, monkeypatch):
    """老配置的 source_node / source_field 应继续工作（向后兼容迁移）。"""
    monkeypatch.setattr("app.utils.paths.RESULTS_DIR", tmp_path)
    upstream = {"c1": {"samples": {"diff": [{"id": 1}]}}}
    out = run_excel_export_node({
        "sheets": [{"id": "s", "enabled": True, "sheet_name": "S",
                    "source_node": "c1", "source_field": "samples.diff"}],
    }, {}, outputs=upstream, run_id="r1")
    assert out["sheets"][0]["source_resolved"] is True
    assert out["sheets"][0]["rows_written"] == 1
    # 输出已经规范化到新字段名
    assert out["sheets"][0]["node_id"] == "c1"
    assert out["sheets"][0]["dataset"] == "samples.diff"


def test_excel_export_history_run_mode_reads_past_run(tmp_path, monkeypatch):
    """source_type='history_run' + run_id → 从 workflow_history 读历史 run 的
    outputs，不依赖当前 run 的 completed_outputs。"""
    monkeypatch.setattr("app.utils.paths.RESULTS_DIR", tmp_path)
    historical = {
        "n1": {
            "summary": {"diff": 999},
            "samples": {"diff": [{"id": 1, "x": "old"}, {"id": 2, "x": "old"}]},
        },
    }
    monkeypatch.setattr(
        "app.services.workflow_history.get_workflow_run",
        lambda rid: {"nodes": [{"node_id": k, "output": v} for k, v in historical.items()]} if rid == "past_run_xyz" else None,
    )
    out = run_excel_export_node({
        "sheets": [
            {"id": "h", "enabled": True, "sheet_name": "历史差异",
             "source_type": "history_run", "run_id": "past_run_xyz",
             "node_id": "n1", "dataset": "diff"},
        ],
    }, {}, outputs={}, run_id="current_run")  # outputs 是空的，验证不依赖
    assert out["sheets"][0]["source_resolved"] is True
    assert out["sheets"][0]["rows_written"] == 2
    assert out["sheets"][0]["run_id"] == "past_run_xyz"


def test_excel_export_history_run_missing_returns_empty_sheet(tmp_path, monkeypatch):
    """指向的 history run 不存在 → 空 sheet，不 FAILED。"""
    monkeypatch.setattr("app.utils.paths.RESULTS_DIR", tmp_path)
    monkeypatch.setattr("app.services.workflow_history.get_workflow_run", lambda _: None)
    out = run_excel_export_node({
        "sheets": [
            {"id": "h", "enabled": True, "sheet_name": "Missing",
             "source_type": "history_run", "run_id": "ghost",
             "node_id": "n1", "dataset": "diff"},
        ],
    }, {}, outputs={}, run_id="r")
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
