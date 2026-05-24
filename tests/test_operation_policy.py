"""Operation Policy 策略矩阵测试(Phase 14 #3)。

覆盖 11 个 Operation × 4 个 environment × 多个 allow_* 组合的关键路径。
不连数据库,纯单元测试 _decide / assert_operation_allowed。
"""
from __future__ import annotations

import pytest

from app.models.common import DatabaseType
from app.models.datasource import DataSource, make_sandbox_datasource_kwargs
from app.services.operation_policy import (
    Operation,
    OperationDenied,
    assert_operation_allowed,
)


def _ds(
    db_type: DatabaseType = DatabaseType.MYSQL,
    environment: str = "unknown",
    **flags,
) -> DataSource:
    """构造测试 ds。默认 unknown + 所有 allow_* False。"""
    return DataSource(
        id="t1", name="test-ds", db_type=db_type,
        host="x", port=3306,
        environment=environment,  # type: ignore[arg-type]
        **flags,
    )


def _expect_denied(ds, op, *, expect_text: str = ""):
    with pytest.raises(OperationDenied) as exc:
        assert_operation_allowed(None, ds, op)
    if expect_text:
        assert expect_text in exc.value.detail


def _expect_allowed(ds, op):
    # 不抛即为通过
    assert_operation_allowed(None, ds, op)


# ─── 1. unknown 环境(fail-safe)─────────────────────────────────────────────


def test_unknown_env_blocks_all_high_risk():
    """unknown 环境:除 STATIC_PREFLIGHT + AI_ENRICH 外全拒。"""
    ds = _ds(DatabaseType.MYSQL, environment="unknown")
    for op in (
        Operation.SQL_EXPLAIN_MYSQL,
        Operation.SCENARIO_MATERIALIZE,
        Operation.SCENARIO_RUN_ALL,
        Operation.SCENARIO_RECORD,
        Operation.SCHEMA_IMPORT_PREVIEW,
        Operation.SCHEMA_IMPORT_SAVE,
    ):
        _expect_denied(ds, op, expect_text="环境未确认")


def test_unknown_env_allows_static_only():
    """unknown 仍允许纯静态:preflight + AI 复核(不连数据库)。"""
    ds = _ds(DatabaseType.MYSQL, environment="unknown")
    _expect_allowed(ds, Operation.SQL_STATIC_PREFLIGHT)
    _expect_allowed(ds, Operation.SQL_AI_ENRICH)


# ─── 2. SQL EXPLAIN 三方言 × prod allow_* 矩阵 ───────────────────────────────


def test_prod_mysql_explain_denied_without_flag():
    ds = _ds(DatabaseType.MYSQL, environment="prod", allow_explain=False)
    _expect_denied(ds, Operation.SQL_EXPLAIN_MYSQL, expect_text="allow_explain")


def test_prod_mysql_explain_allowed_with_flag():
    ds = _ds(DatabaseType.MYSQL, environment="prod", allow_explain=True)
    _expect_allowed(ds, Operation.SQL_EXPLAIN_MYSQL)


def test_prod_dm_explain_denied_without_flag():
    ds = _ds(DatabaseType.DM, environment="prod", allow_dm_explain=False, allow_explain=False)
    _expect_denied(ds, Operation.SQL_EXPLAIN_DM, expect_text="allow_dm_explain")


def test_prod_dm_explain_allowed_with_dm_flag():
    """DM 优先看 allow_dm_explain"""
    ds = _ds(DatabaseType.DM, environment="prod", allow_dm_explain=True)
    _expect_allowed(ds, Operation.SQL_EXPLAIN_DM)


def test_prod_dm_explain_allowed_with_explain_flag():
    """DM fallback:allow_explain=True 也可以"""
    ds = _ds(DatabaseType.DM, environment="prod", allow_dm_explain=False, allow_explain=True)
    _expect_allowed(ds, Operation.SQL_EXPLAIN_DM)


def test_prod_oracle_plan_table_denied_without_flag():
    ds = _ds(DatabaseType.ORACLE, environment="prod", allow_oracle_plan_table=False)
    _expect_denied(
        ds, Operation.SQL_EXPLAIN_ORACLE_PLAN_TABLE,
        expect_text="PLAN_TABLE",
    )


def test_prod_oracle_plan_table_allowed_with_flag():
    ds = _ds(DatabaseType.ORACLE, environment="prod", allow_oracle_plan_table=True)
    _expect_allowed(ds, Operation.SQL_EXPLAIN_ORACLE_PLAN_TABLE)


def test_prod_oracle_error_message_mentions_diagnostic_write():
    """Oracle 拒绝文案必须告知 PLAN_TABLE 是诊断写入(非业务表)"""
    ds = _ds(DatabaseType.ORACLE, environment="prod", allow_oracle_plan_table=False)
    with pytest.raises(OperationDenied) as exc:
        assert_operation_allowed(None, ds, Operation.SQL_EXPLAIN_ORACLE_PLAN_TABLE)
    assert "诊断" in exc.value.detail
    assert "non-业务表" in exc.value.detail or "非业务表" in exc.value.detail or "PLAN_TABLE" in exc.value.detail


def test_db_type_mismatch_denied():
    """MySQL ds 走 SQL_EXPLAIN_DM 路径要拒绝(防 dispatch bug)"""
    kwargs = make_sandbox_datasource_kwargs()
    kwargs.pop("environment", None)  # 防跟 _ds 第二参数冲突
    ds = _ds(DatabaseType.MYSQL, environment="sandbox", **kwargs)
    _expect_denied(ds, Operation.SQL_EXPLAIN_DM, expect_text="db_type=mysql")


# ─── 3. SCENARIO 写入 — sandbox-only 红线 ───────────────────────────────────


def test_prod_materialize_denied():
    ds = _ds(DatabaseType.MYSQL, environment="prod", allow_scenario_write=True)
    _expect_denied(ds, Operation.SCENARIO_MATERIALIZE, expect_text="合规拒绝")


def test_prod_run_all_denied():
    ds = _ds(DatabaseType.MYSQL, environment="prod", allow_scenario_write=True)
    _expect_denied(ds, Operation.SCENARIO_RUN_ALL)


def test_prod_record_denied():
    ds = _ds(DatabaseType.MYSQL, environment="prod", allow_record_task=True)
    _expect_denied(ds, Operation.SCENARIO_RECORD)


def test_staging_materialize_denied():
    """staging 也拒(只放 sandbox)"""
    ds = _ds(DatabaseType.MYSQL, environment="staging", allow_scenario_write=True)
    _expect_denied(ds, Operation.SCENARIO_MATERIALIZE)


def test_sandbox_materialize_allowed_with_flag():
    ds = _ds(DatabaseType.MYSQL, environment="sandbox", allow_scenario_write=True)
    _expect_allowed(ds, Operation.SCENARIO_MATERIALIZE)


def test_sandbox_materialize_denied_without_flag():
    """sandbox 也要 allow_scenario_write=True"""
    ds = _ds(DatabaseType.MYSQL, environment="sandbox", allow_scenario_write=False)
    _expect_denied(ds, Operation.SCENARIO_MATERIALIZE, expect_text="allow_scenario_write")


def test_sandbox_record_allowed_with_flag():
    ds = _ds(DatabaseType.MYSQL, environment="sandbox", allow_record_task=True)
    _expect_allowed(ds, Operation.SCENARIO_RECORD)


def test_scenario_verify_allowed_anywhere():
    """verify 纯读 task_store/history,任何环境允许(包括 unknown)"""
    for env in ("unknown", "prod", "staging", "sandbox"):
        ds = _ds(DatabaseType.MYSQL, environment=env)
        _expect_allowed(ds, Operation.SCENARIO_VERIFY)


# ─── 4. SCHEMA IMPORT ─────────────────────────────────────────────────────


def test_prod_schema_preview_denied_without_flag():
    ds = _ds(DatabaseType.MYSQL, environment="prod", allow_schema_import=False)
    _expect_denied(ds, Operation.SCHEMA_IMPORT_PREVIEW, expect_text="allow_schema_import")


def test_prod_schema_preview_allowed_with_flag():
    ds = _ds(DatabaseType.MYSQL, environment="prod", allow_schema_import=True)
    _expect_allowed(ds, Operation.SCHEMA_IMPORT_PREVIEW)


def test_prod_schema_save_denied_default():
    """prod 即使开 allow_schema_save=True 也拒(红线:save 只允许 sandbox)"""
    ds = _ds(
        DatabaseType.MYSQL, environment="prod",
        allow_schema_import=True, allow_schema_save=True,
    )
    _expect_denied(ds, Operation.SCHEMA_IMPORT_SAVE, expect_text="仅限 sandbox")


def test_sandbox_schema_save_allowed_with_flag():
    ds = _ds(DatabaseType.MYSQL, environment="sandbox", allow_schema_save=True)
    _expect_allowed(ds, Operation.SCHEMA_IMPORT_SAVE)


def test_sandbox_schema_save_denied_without_flag():
    ds = _ds(DatabaseType.MYSQL, environment="sandbox", allow_schema_save=False)
    _expect_denied(ds, Operation.SCHEMA_IMPORT_SAVE)


# ─── 5. sandbox 全开模板 helper ─────────────────────────────────────────────


def test_make_sandbox_kwargs_unlocks_everything():
    """make_sandbox_datasource_kwargs() 拼出的 ds 应放行所有 sandbox 范围内 op"""
    kwargs = make_sandbox_datasource_kwargs()
    env = kwargs.pop("environment")
    ds = _ds(DatabaseType.MYSQL, environment=env, **kwargs)
    for op in (
        Operation.SQL_STATIC_PREFLIGHT,
        Operation.SQL_EXPLAIN_MYSQL,
        Operation.SQL_AI_ENRICH,
        Operation.SCENARIO_MATERIALIZE,
        Operation.SCENARIO_RUN_ALL,
        Operation.SCENARIO_RECORD,
        Operation.SCENARIO_VERIFY,
        Operation.SCHEMA_IMPORT_PREVIEW,
        Operation.SCHEMA_IMPORT_SAVE,
    ):
        _expect_allowed(ds, op)


# ─── 6. OperationDenied 异常携带 metadata ───────────────────────────────────


def test_operation_denied_carries_operation_and_env():
    ds = _ds(DatabaseType.MYSQL, environment="prod")
    with pytest.raises(OperationDenied) as exc:
        assert_operation_allowed(None, ds, Operation.SQL_EXPLAIN_MYSQL)
    assert exc.value.operation == Operation.SQL_EXPLAIN_MYSQL
    assert exc.value.environment == "prod"
    assert exc.value.status_code == 403
