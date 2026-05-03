from __future__ import annotations

from decimal import Decimal

import pytest

from app.compare.engine import compare_rows, compare_sorted_row_iterators
from app.models import CompareRules


# helpers
def _rows(*dicts):
    return list(dicts)


# --- compare_rows: basic buckets ---

def test_identical_rows_go_to_same():
    src = [{"id": 1, "v": "a"}]
    tgt = [{"id": 1, "v": "a"}]
    result = compare_rows(src, tgt, ["id"])
    assert result["same"] == [{"key": [1], "source": src[0], "target": tgt[0]}]
    assert result["diff"] == []
    assert result["only_source"] == []
    assert result["only_target"] == []


def test_changed_value_goes_to_diff():
    src = [{"id": 1, "v": "a"}]
    tgt = [{"id": 1, "v": "b"}]
    result = compare_rows(src, tgt, ["id"])
    assert len(result["diff"]) == 1
    assert result["diff"][0]["changes"]["v"]["source"] == "a"
    assert result["diff"][0]["changes"]["v"]["target"] == "b"


def test_missing_in_target_only_source():
    src = [{"id": 1}, {"id": 2}]
    tgt = [{"id": 1}]
    result = compare_rows(src, tgt, ["id"])
    assert len(result["only_source"]) == 1
    assert result["only_source"][0]["key"] == [2]


def test_extra_in_target_only_target():
    src = [{"id": 1}]
    tgt = [{"id": 1}, {"id": 3}]
    result = compare_rows(src, tgt, ["id"])
    assert len(result["only_target"]) == 1
    assert result["only_target"][0]["key"] == [3]


def test_empty_both_sides():
    result = compare_rows([], [], ["id"])
    assert all(len(v) == 0 for v in result.values())


def test_duplicate_source_key_raises():
    src = [{"id": 1, "v": "a"}, {"id": 1, "v": "b"}]
    tgt = [{"id": 1, "v": "a"}]
    with pytest.raises(ValueError, match="duplicate key"):
        compare_rows(src, tgt, ["id"])


# --- compare_rules: column_mappings ---

def test_column_mapping_matches_renamed_column():
    src = [{"id": 1, "src_val": 10}]
    tgt = [{"id": 1, "tgt_val": 10}]
    rules = CompareRules(column_mappings={"src_val": "tgt_val"})
    result = compare_rows(src, tgt, ["id"], rules)
    assert result["same"]
    assert result["diff"] == []


def test_column_mapping_detects_mismatch():
    src = [{"id": 1, "src_val": 10}]
    tgt = [{"id": 1, "tgt_val": 99}]
    rules = CompareRules(column_mappings={"src_val": "tgt_val"})
    result = compare_rows(src, tgt, ["id"], rules)
    assert result["diff"]


def test_positional_mapping_handles_unequal_column_counts():
    src = [{"id": 1, "name": "Alice", "etl_dt": "2026-05-04"}]
    tgt = [{"id2": 1, "client_name": "Alice"}]

    result = compare_rows(src, tgt, ["id"])

    assert result["diff"] == [
        {
            "key": [1],
            "source": src[0],
            "target": tgt[0],
            "changes": {
                "etl_dt": {
                    "source": "2026-05-04",
                    "target": None,
                    "target_column": "__missing_target_column_3_etl_dt",
                }
            },
        }
    ]
    assert result["same"] == []


# --- compare_rules: numeric tolerance ---

def test_numeric_tolerance_within_range():
    src = [{"id": 1, "amount": 1.001}]
    tgt = [{"id": 1, "amount": 1.002}]
    rules = CompareRules(numeric_tolerance=0.01)
    result = compare_rows(src, tgt, ["id"], rules)
    assert result["same"]


def test_numeric_tolerance_exceeded():
    src = [{"id": 1, "amount": 1.0}]
    tgt = [{"id": 1, "amount": 2.0}]
    rules = CompareRules(numeric_tolerance=0.5)
    result = compare_rows(src, tgt, ["id"], rules)
    assert result["diff"]


# --- compare_rules: string normalization ---

def test_trim_strings():
    src = [{"id": 1, "name": "  alice  "}]
    tgt = [{"id": 1, "name": "alice"}]
    rules = CompareRules(trim_strings=True)
    result = compare_rows(src, tgt, ["id"], rules)
    assert result["same"]


def test_case_insensitive():
    src = [{"id": 1, "name": "Alice"}]
    tgt = [{"id": 1, "name": "alice"}]
    rules = CompareRules(case_insensitive=True)
    result = compare_rows(src, tgt, ["id"], rules)
    assert result["same"]


def test_empty_as_null():
    src = [{"id": 1, "note": ""}]
    tgt = [{"id": 1, "note": None}]
    rules = CompareRules(empty_as_null=True)
    result = compare_rows(src, tgt, ["id"], rules)
    assert result["same"]


# --- compare_rules: ignore_columns ---

def test_ignore_columns_skips_diff():
    src = [{"id": 1, "ts": "2024-01-01", "v": "x"}]
    tgt = [{"id": 1, "ts": "2024-12-31", "v": "x"}]
    rules = CompareRules(ignore_columns=["ts"])
    result = compare_rows(src, tgt, ["id"], rules)
    assert result["same"]
    assert result["diff"] == []


# --- compare_sorted_row_iterators ---

def test_stream_compare_identical():
    src = [{"id": 1, "v": "a"}, {"id": 2, "v": "b"}]
    tgt = [{"id": 1, "v": "a"}, {"id": 2, "v": "b"}]
    result = compare_sorted_row_iterators(iter(src), iter(tgt), ["id"])
    assert len(result["same"]) == 2
    assert result["diff"] == []


def test_stream_compare_diff():
    src = [{"id": 1, "v": "a"}]
    tgt = [{"id": 1, "v": "z"}]
    result = compare_sorted_row_iterators(iter(src), iter(tgt), ["id"])
    assert result["diff"]


def test_stream_compare_only_source():
    src = [{"id": 1}, {"id": 2}]
    tgt = [{"id": 1}]
    result = compare_sorted_row_iterators(iter(src), iter(tgt), ["id"])
    assert len(result["only_source"]) == 1


def test_stream_compare_only_target():
    src = [{"id": 1}]
    tgt = [{"id": 1}, {"id": 2}]
    result = compare_sorted_row_iterators(iter(src), iter(tgt), ["id"])
    assert len(result["only_target"]) == 1


def test_stream_compare_unsorted_raises():
    src = [{"id": 2}, {"id": 1}]
    tgt = [{"id": 1}, {"id": 2}]
    with pytest.raises(ValueError, match="sorted"):
        compare_sorted_row_iterators(iter(src), iter(tgt), ["id"])


def test_stream_compare_duplicate_key_raises():
    src = [{"id": 1}, {"id": 1}]
    tgt = [{"id": 1}]
    with pytest.raises(ValueError, match="duplicate key"):
        compare_sorted_row_iterators(iter(src), iter(tgt), ["id"])


def test_stream_compare_empty_both():
    result = compare_sorted_row_iterators(iter([]), iter([]), ["id"])
    assert all(len(v) == 0 for v in result.values())


# --- Excel↔SQL type-equivalence (Phase 2 round 2) ---

def test_midnight_datetime_equals_date():
    """openpyxl returns date cells as datetime(y,m,d,0,0); MySQL DATE returns
    plain date(y,m,d). They must compare equal so Excel-vs-SQL doesn't false-diff."""
    from datetime import date, datetime
    src = [{"id": 1, "d": datetime(2024, 1, 15, 0, 0, 0)}]   # Excel side
    tgt = [{"id": 1, "d": date(2024, 1, 15)}]                # SQL side
    result = compare_rows(src, tgt, ["id"])
    assert len(result["same"]) == 1
    assert len(result["diff"]) == 0


def test_non_midnight_datetime_not_equal_to_date():
    """A datetime with non-zero time must NOT collapse to date — that would
    hide real time-of-day differences."""
    from datetime import date, datetime
    src = [{"id": 1, "d": datetime(2024, 1, 15, 13, 45, 0)}]
    tgt = [{"id": 1, "d": date(2024, 1, 15)}]
    result = compare_rows(src, tgt, ["id"])
    assert len(result["diff"]) == 1
    assert len(result["same"]) == 0


def test_datetime_keys_align_with_date_keys():
    """Same equivalence in key columns: a row keyed by datetime(y,m,d,0,0)
    must match the row keyed by date(y,m,d) on the other side."""
    from datetime import date, datetime
    src = [{"d": datetime(2024, 1, 15, 0, 0), "v": "x"}]
    tgt = [{"d": date(2024, 1, 15), "v": "x"}]
    result = compare_rows(src, tgt, ["d"])
    assert len(result["same"]) == 1
    assert result["only_source"] == [] and result["only_target"] == []


def test_int_decimal_equivalence():
    """openpyxl returns whole-number cells as int; MySQL DECIMAL returns
    Decimal. These already compare equal in Python — guard the behavior
    with a test so future normalization changes don't break it."""
    src = [{"id": 1, "salary": 15000}]              # Excel side: int
    tgt = [{"id": 1, "salary": Decimal("15000.00")}]  # SQL side: Decimal
    result = compare_rows(src, tgt, ["id"])
    assert len(result["same"]) == 1
