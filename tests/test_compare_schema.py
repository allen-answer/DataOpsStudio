from __future__ import annotations

from app.models import CompareRules
from app.services.compare_schema import build_schema_report, column_warnings, uniquify_columns


def test_uniquify_columns_preserves_duplicate_values():
    columns = uniquify_columns(["CUST_NO", "cust_no", "FUND_ACC", "CUST_NO"])

    assert columns == ["CUST_NO", "cust_no__2", "FUND_ACC", "CUST_NO__3"]


def test_column_warnings_reports_duplicates():
    raw = ["CUST_NO", "CUST_NO", "FUND_ACC"]
    unique = uniquify_columns(raw)

    warnings = column_warnings(raw, unique, side="source")

    assert warnings[0]["type"] == "duplicate_columns"
    assert warnings[0]["columns"][0]["columns"] == ["CUST_NO", "CUST_NO__2"]


def test_schema_report_uses_position_mapping_when_counts_differ():
    report = build_schema_report(
        ["id", "name", "etl_dt"],
        ["id2", "client_name"],
        ["id"],
        CompareRules(),
    )

    assert report["mapping_mode"] == "position"
    assert report["count_mismatch"] is True
    assert report["compared_columns"] == [
        {"source": "name", "target": "client_name", "mode": "position"},
        {"source": "etl_dt", "target": "", "mode": "source_only"},
    ]
    assert any(item["type"] == "schema_count_mismatch" for item in report["warnings"])


def test_schema_report_respects_manual_mapping():
    report = build_schema_report(
        ["id", "src_name"],
        ["target_id", "target_name"],
        ["id"],
        CompareRules(column_mappings={"id": "target_id", "src_name": "target_name"}),
    )

    assert report["mapping_mode"] == "manual"
    assert report["compared_columns"] == [
        {"source": "src_name", "target": "target_name", "mode": "mapped"}
    ]
    assert report["has_schema_mismatch"] is False
