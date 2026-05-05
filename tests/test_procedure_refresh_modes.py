"""S2.C：procedure-level refresh_mode 增量测试。

semantic._classify_procedure_refresh_mode 之前对纯清理型过程（只 truncate /
只 delete，无后续 insert）会落到 None / mixed —— 不利于"哪些 procedure 是
清理任务"的过滤。新加 truncate_only / delete_only / cleanup_mixed 三个 label。

测试覆盖：
- 新增 3 个 label 的判定
- 老 label（truncate_insert / delete_insert / merge / append / mixed）回归
- 通过 _build_procedures 完整 stack 验证（不只是单元 classifier）
"""
from __future__ import annotations

from app.lineage.semantic import _classify_procedure_refresh_mode, _build_procedures


# ─── 单元：_classify_procedure_refresh_mode ─────────────────────────────────


def test_truncate_only_no_insert():
    assert _classify_procedure_refresh_mode(
        insert_count=0, update_count=0, merge_count=0,
        delete_before_insert=False, truncate_before_insert=False,
        has_full_delete=False, truncate_count=2,
    ) == "truncate_only"


def test_delete_only_full_with_no_insert():
    assert _classify_procedure_refresh_mode(
        insert_count=0, update_count=0, merge_count=0,
        delete_before_insert=False, truncate_before_insert=False,
        has_full_delete=True, delete_count=1,
    ) == "delete_only"


def test_delete_only_partial_with_where():
    assert _classify_procedure_refresh_mode(
        insert_count=0, update_count=0, merge_count=0,
        delete_before_insert=False, truncate_before_insert=False,
        has_full_delete=False, delete_count=3,
    ) == "delete_only_partial"


def test_cleanup_mixed_truncate_and_delete_no_insert():
    assert _classify_procedure_refresh_mode(
        insert_count=0, update_count=0, merge_count=0,
        delete_before_insert=False, truncate_before_insert=False,
        has_full_delete=False,
        truncate_count=1, delete_count=2,
    ) == "cleanup_mixed"


# ─── 老 label 回归（确保新 case 不影响）─────────────────────────────────────


def test_truncate_insert_still_wins_over_truncate_only():
    """有后续 insert → 走 truncate_insert，不走 truncate_only。"""
    assert _classify_procedure_refresh_mode(
        insert_count=1, update_count=0, merge_count=0,
        delete_before_insert=False, truncate_before_insert=True,
        has_full_delete=False, truncate_count=1,
    ) == "truncate_insert"


def test_delete_insert_still_wins():
    assert _classify_procedure_refresh_mode(
        insert_count=1, update_count=0, merge_count=0,
        delete_before_insert=True, truncate_before_insert=False,
        has_full_delete=True, delete_count=1,
    ) == "delete_insert"


def test_merge_only_returns_merge():
    assert _classify_procedure_refresh_mode(
        insert_count=0, update_count=0, merge_count=2,
        delete_before_insert=False, truncate_before_insert=False,
        has_full_delete=False,
    ) == "merge"


def test_append_only_inserts():
    assert _classify_procedure_refresh_mode(
        insert_count=3, update_count=0, merge_count=0,
        delete_before_insert=False, truncate_before_insert=False,
        has_full_delete=False,
    ) == "append"


def test_mixed_when_insert_plus_update():
    assert _classify_procedure_refresh_mode(
        insert_count=1, update_count=1, merge_count=0,
        delete_before_insert=False, truncate_before_insert=False,
        has_full_delete=False,
    ) == "mixed"


def test_none_when_no_dml():
    assert _classify_procedure_refresh_mode(
        insert_count=0, update_count=0, merge_count=0,
        delete_before_insert=False, truncate_before_insert=False,
        has_full_delete=False,
    ) is None


# ─── 集成：_build_procedures 走 SQL 段产 procedure 元数据 ────────────────────


def test_build_procedures_produces_truncate_only_for_cleanup_proc():
    """模拟一个清理过程：两个 TRUNCATE 段，无 insert。"""
    segments = [
        {"procedure_name": "p_cleanup", "procedure_kind": "PROCEDURE",
         "segment_index": "1", "sql": "TRUNCATE TABLE staging.t1",
         "parse_status": "parsed"},
        {"procedure_name": "p_cleanup", "procedure_kind": "PROCEDURE",
         "segment_index": "2", "sql": "TRUNCATE TABLE staging.t2",
         "parse_status": "parsed"},
    ]
    procs = _build_procedures(segments)
    assert len(procs) == 1
    proc = procs[0]
    modes = set(proc["refresh_modes"])
    # 每个 target 是 truncate_only
    assert modes == {"truncate_only"}


def test_build_procedures_truncate_then_insert_to_same_target_is_truncate_insert():
    segments = [
        {"procedure_name": "p_refresh", "procedure_kind": "PROCEDURE",
         "segment_index": "1", "sql": "TRUNCATE TABLE dwd.t",
         "parse_status": "parsed"},
        {"procedure_name": "p_refresh", "procedure_kind": "PROCEDURE",
         "segment_index": "2", "sql": "INSERT INTO dwd.t SELECT * FROM ods.t",
         "parse_status": "parsed"},
    ]
    procs = _build_procedures(segments)
    assert procs[0]["refresh_modes"] == ["truncate_insert"]


def test_build_procedures_delete_only_partial_with_where():
    segments = [
        {"procedure_name": "p_purge_old", "procedure_kind": "PROCEDURE",
         "segment_index": "1",
         "sql": "DELETE FROM logs.t WHERE created_at < SYSDATE - 30",
         "parse_status": "parsed"},
    ]
    procs = _build_procedures(segments)
    assert procs[0]["refresh_modes"] == ["delete_only_partial"]


def test_build_procedures_cleanup_mixed_truncate_plus_delete():
    """truncate 一张、delete 另一张，无 insert → 都各自 truncate_only / delete_only_partial。
    refresh_modes 是去重 sorted 的多种 mode。"""
    segments = [
        {"procedure_name": "p_multi_clean", "procedure_kind": "PROCEDURE",
         "segment_index": "1", "sql": "TRUNCATE TABLE staging.t1",
         "parse_status": "parsed"},
        {"procedure_name": "p_multi_clean", "procedure_kind": "PROCEDURE",
         "segment_index": "2", "sql": "DELETE FROM staging.t2 WHERE x = 1",
         "parse_status": "parsed"},
    ]
    procs = _build_procedures(segments)
    modes = set(procs[0]["refresh_modes"])
    assert "truncate_only" in modes
    assert "delete_only_partial" in modes
