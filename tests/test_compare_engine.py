from __future__ import annotations

from decimal import Decimal

import pytest

from app.compare.engine import (
    compare_rows,
    compare_rows_streaming,
    compare_sorted_row_events,
    compare_sorted_row_iterators,
)
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


# ─── 切片 F.1：compare_rows_streaming 事件流契约 ───────────────────────────────


def _events_to_buckets(events):
    out = {"only_source": [], "only_target": [], "diff": [], "same": []}
    for bucket, row in events:
        out[bucket].append(row)
    return out


def test_streaming_events_equivalent_to_compare_rows_simple():
    """全 same / 全 diff / mix 三个场景 streaming 收口后跟 compare_rows 等价。"""
    cases = [
        # 全 same
        ([{"id": 1, "v": "a"}], [{"id": 1, "v": "a"}]),
        # 全 diff
        ([{"id": 1, "v": "a"}], [{"id": 1, "v": "b"}]),
        # mix: only_source + diff + same + only_target
        (
            [{"id": 1, "v": "a"}, {"id": 2, "v": "b"}, {"id": 3, "v": "c"}],
            [{"id": 2, "v": "b"}, {"id": 3, "v": "X"}, {"id": 4, "v": "d"}],
        ),
    ]
    for src, tgt in cases:
        from_streaming = _events_to_buckets(
            compare_rows_streaming(src, tgt, ["id"])
        )
        from_compare = compare_rows(src, tgt, ["id"])
        assert from_streaming == from_compare, f"divergence on {src} vs {tgt}"


def test_streaming_is_a_generator_not_list():
    """compare_rows_streaming 必须是 generator —— 否则 streaming 假命题。"""
    import types
    gen = compare_rows_streaming([], [], ["id"])
    assert isinstance(gen, types.GeneratorType)


def test_streaming_yields_only_source_diff_same_then_only_target():
    """事件产出顺序：先 source-side（only_source / diff / same 混合按 source_index
    序），再 only_target。runner 样本采集 + reader fixture 都依赖这个顺序稳定。"""
    src = [{"id": 1}, {"id": 2}, {"id": 3}]
    tgt = [{"id": 2}, {"id": 3}, {"id": 4}]  # 4 是 only_target
    events = list(compare_rows_streaming(src, tgt, ["id"]))
    # 倒数那个一定是 only_target
    assert events[-1][0] == "only_target"
    assert events[-1][1]["key"] == [4]
    # 前面的全是 source-side
    for bucket, _ in events[:-1]:
        assert bucket in {"only_source", "diff", "same"}


def test_streaming_respects_rules_column_mappings():
    """rules.column_mappings 在 streaming 路径下生效（跟 compare_rows 一致）。"""
    src = [{"id": 1, "amt_src": 10}]
    tgt = [{"id": 1, "amt_tgt": 10}]
    rules = CompareRules(column_mappings={"amt_src": "amt_tgt"})
    streaming_buckets = _events_to_buckets(
        compare_rows_streaming(src, tgt, ["id"], rules)
    )
    compare_buckets = compare_rows(src, tgt, ["id"], rules)
    assert streaming_buckets == compare_buckets
    assert len(streaming_buckets["same"]) == 1


def test_streaming_compare_rows_now_uses_streaming_internally():
    """compare_rows 切到 streaming 后，跟历史 dict 行为完全兼容（这条断言已经
    被前面 26 条 test_compare_engine 隐式覆盖；这里显式标记一下回归约束）。"""
    src = [{"id": i, "v": i * 2} for i in range(50)]
    tgt = [{"id": i, "v": i * 2 if i % 3 else i * 99} for i in range(45, 60)]
    buckets = compare_rows(src, tgt, ["id"])
    # 跟 streaming 一致
    via_stream = _events_to_buckets(compare_rows_streaming(src, tgt, ["id"]))
    assert buckets == via_stream


# ─── 切片 G：compare_sorted_row_events 事件流契约 ───────────────────────────


def _sorted_events_to_buckets(events):
    out = {"only_source": [], "only_target": [], "diff": [], "same": []}
    for bucket, row in events:
        out[bucket].append(row)
    return out


def test_sorted_events_equivalent_to_iterators_simple():
    """全 same / 全 diff / 混合 3 个场景，events 收口后跟 iterators 完全等价。"""
    cases = [
        ([{"id": 1, "v": "a"}, {"id": 2, "v": "b"}],
         [{"id": 1, "v": "a"}, {"id": 2, "v": "b"}]),
        ([{"id": 1, "v": "a"}],
         [{"id": 1, "v": "z"}]),
        ([{"id": 1, "v": "a"}, {"id": 3, "v": "c"}, {"id": 5, "v": "e"}],
         [{"id": 1, "v": "a"}, {"id": 2, "v": "B"}, {"id": 5, "v": "E"}]),
    ]
    for src, tgt in cases:
        evt_buckets = _sorted_events_to_buckets(
            compare_sorted_row_events(iter(src), iter(tgt), ["id"])
        )
        iter_buckets = compare_sorted_row_iterators(iter(src), iter(tgt), ["id"])
        assert evt_buckets == iter_buckets, f"divergence on {src} vs {tgt}"


def test_sorted_events_is_generator():
    import types
    gen = compare_sorted_row_events(iter([]), iter([]), ["id"])
    assert isinstance(gen, types.GeneratorType)


def test_sorted_events_classification_diff_vs_same():
    """仅源 / 仅目标 / 同 key 同值 (same) / 同 key 异值 (diff) 都各自归桶。"""
    src = [{"id": 1, "v": "a"}, {"id": 2, "v": "b"}, {"id": 3, "v": "c"}]
    tgt = [{"id": 2, "v": "b"}, {"id": 3, "v": "X"}, {"id": 4, "v": "d"}]
    events = list(compare_sorted_row_events(iter(src), iter(tgt), ["id"]))
    by_bucket = {b: [] for b in ("only_source", "only_target", "diff", "same")}
    for b, r in events:
        by_bucket[b].append(r["key"][0])
    assert by_bucket["only_source"] == [1]
    assert by_bucket["only_target"] == [4]
    assert by_bucket["same"] == [2]
    assert by_bucket["diff"] == [3]


def test_sorted_events_source_unsorted_raises():
    src = iter([{"id": 2}, {"id": 1}])
    tgt = iter([{"id": 1}, {"id": 2}])
    with pytest.raises(ValueError, match="sorted"):
        list(compare_sorted_row_events(src, tgt, ["id"]))


def test_sorted_events_target_unsorted_raises():
    src = iter([{"id": 1}, {"id": 2}])
    tgt = iter([{"id": 2}, {"id": 1}])
    with pytest.raises(ValueError, match="sorted"):
        list(compare_sorted_row_events(src, tgt, ["id"]))


def test_sorted_events_source_duplicate_key_raises():
    src = iter([{"id": 1}, {"id": 1}])
    tgt = iter([{"id": 1}])
    with pytest.raises(ValueError, match="duplicate key"):
        list(compare_sorted_row_events(src, tgt, ["id"]))


def test_sorted_events_target_duplicate_key_raises():
    src = iter([{"id": 1}, {"id": 2}])
    tgt = iter([{"id": 1}, {"id": 1}])
    with pytest.raises(ValueError, match="duplicate key"):
        list(compare_sorted_row_events(src, tgt, ["id"]))


def test_sorted_events_column_mappings_aligns_renamed_target():
    """target 列名跟 source 不一样时 rules.column_mappings 透传。"""
    src = [{"id": 1, "amt_src": 10}, {"id": 2, "amt_src": 20}]
    tgt = [{"id": 1, "amt_tgt": 10}, {"id": 2, "amt_tgt": 99}]
    rules = CompareRules(column_mappings={"amt_src": "amt_tgt"})
    evt = _sorted_events_to_buckets(
        compare_sorted_row_events(iter(src), iter(tgt), ["id"], rules)
    )
    itr = compare_sorted_row_iterators(iter(src), iter(tgt), ["id"], rules)
    assert evt == itr
    assert len(evt["same"]) == 1
    assert len(evt["diff"]) == 1


def test_sorted_events_numeric_tolerance_within_range():
    src = [{"id": 1, "amount": 1.001}]
    tgt = [{"id": 1, "amount": 1.002}]
    rules = CompareRules(numeric_tolerance=0.01)
    evt = _sorted_events_to_buckets(
        compare_sorted_row_events(iter(src), iter(tgt), ["id"], rules)
    )
    assert len(evt["same"]) == 1
    assert evt["diff"] == []


def test_sorted_events_ignore_columns_skips_diff():
    src = [{"id": 1, "ts": "2024-01-01", "v": "x"}]
    tgt = [{"id": 1, "ts": "2024-12-31", "v": "x"}]
    rules = CompareRules(ignore_columns=["ts"])
    evt = _sorted_events_to_buckets(
        compare_sorted_row_events(iter(src), iter(tgt), ["id"], rules)
    )
    assert len(evt["same"]) == 1
    assert evt["diff"] == []


def test_sorted_iterators_now_uses_events_internally():
    """compare_sorted_row_iterators 切到 events 包装后行为兼容（前面 7 个
    test_stream_compare_* 隐式覆盖；这里显式标 contract）。"""
    src = [{"id": i, "v": i * 2} for i in range(20)]
    tgt = [{"id": i, "v": i * 2 if i % 4 else i * 99} for i in range(15, 30)]
    dict_from_iter = compare_sorted_row_iterators(iter(src), iter(tgt), ["id"])
    dict_from_events = _sorted_events_to_buckets(
        compare_sorted_row_events(iter(src), iter(tgt), ["id"])
    )
    assert dict_from_iter == dict_from_events
