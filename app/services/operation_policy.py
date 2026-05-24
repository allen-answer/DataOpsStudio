"""Operation Policy — 后端强制策略层(Phase 14 #3 合规防御)。

设计目标:
- 把"哪些操作允许"从 API 各端点散在的 if-else 改成集中决策
- 按 (environment, db_type, allow_*) 三元组矩阵化
- fail-safe:未知环境 / 未开 allow_* 一律拒绝
- 抛 OperationDenied(HTTPException 403) — caller 可直接 raise 给 FastAPI
- 同时落 audit_log,allow / deny 都记(管理员追溯能看到"被拒绝的尝试")

策略矩阵(简明版):

| environment | SQL_EXPLAIN_*           | SCENARIO_*              | SCHEMA_IMPORT_* |
|-------------|-------------------------|-------------------------|-----------------|
| unknown     | 全拒(要求 admin 确认环境)                                    |
| prod        | 按 allow_* flag 翻开    | MATERIALIZE/RUN_ALL/RECORD 默认拒 | PREVIEW 按 allow_schema_import; SAVE 默认拒 |
| staging     | 同 prod (默认收紧)     | 写入默认拒,管理员可改       | 同 prod         |
| sandbox     | 全允许                  | 全允许                  | 全允许          |

红线无条件拒(任何环境):
- SCENARIO_MATERIALIZE / RUN_ALL / RECORD 在 prod / staging:即使 allow_scenario_write=True
  也拒,这是产品红线;sandbox 才允许(防 admin 误把 prod ds 翻 allow_scenario_write 后造数)

调用模式:
    from app.services.operation_policy import Operation, assert_operation_allowed
    assert_operation_allowed(current_user, datasource, Operation.SQL_EXPLAIN_MYSQL)
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Any, TYPE_CHECKING

from fastapi import HTTPException

if TYPE_CHECKING:
    from app.models.datasource import DataSource
    from app.models import User


logger = logging.getLogger(__name__)


class Operation(str, Enum):
    """所有受 operation_policy 管的高风险操作枚举。

    分 3 类:
    - SQL 诊断:静态 / EXPLAIN(按方言)/ AI 复核
    - Scenario 测试沙盒:materialize / run-all / record / verify
    - Schema 元数据:导入预览 / 保存 yml
    """
    # SQL 诊断
    SQL_STATIC_PREFLIGHT = "sql.static_preflight"
    SQL_EXPLAIN_MYSQL = "sql.explain_mysql"
    SQL_EXPLAIN_DM = "sql.explain_dm"
    SQL_EXPLAIN_ORACLE_PLAN_TABLE = "sql.explain_oracle_plan_table"
    SQL_AI_ENRICH = "sql.ai_enrich"

    # Scenario 测试沙盒
    SCENARIO_MATERIALIZE = "scenario.materialize"
    SCENARIO_RUN_ALL = "scenario.run_all"
    SCENARIO_RECORD = "scenario.record"
    SCENARIO_VERIFY = "scenario.verify"

    # Schema 元数据
    SCHEMA_IMPORT_PREVIEW = "schema_import.preview"
    SCHEMA_IMPORT_SAVE = "schema_import.save"


class OperationDenied(HTTPException):
    """policy 拒绝。HTTPException 子类,FastAPI 自动转 403。"""

    def __init__(self, detail: str, *, operation: Operation, environment: str = ""):
        super().__init__(status_code=403, detail=detail)
        self.operation = operation
        self.environment = environment


# scenario 写入红线 - sandbox 之外的环境无条件拒,即使 allow_scenario_write=True
_SCENARIO_WRITE_OPERATIONS = {
    Operation.SCENARIO_MATERIALIZE,
    Operation.SCENARIO_RUN_ALL,
    Operation.SCENARIO_RECORD,
}


def _get_env(datasource: "DataSource") -> str:
    return (getattr(datasource, "environment", "") or "unknown").lower()


def _get_db_type(datasource: "DataSource") -> str:
    """归一化 db_type → mysql / oracle / dm / db2 / ..."""
    raw = getattr(datasource, "db_type", None)
    if raw is None:
        return ""
    val = getattr(raw, "value", raw)
    return str(val).lower()


def _decide(  # noqa: C901,PLR0911,PLR0912 - 矩阵决策天然有多 branch
    datasource: "DataSource",
    operation: Operation,
) -> tuple[bool, str]:
    """核心策略决策。返 (allowed, reason)。

    设计原则:
    - environment=unknown:除 STATIC_PREFLIGHT + AI_ENRICH(纯静态)外全拒
    - SCENARIO_VERIFY 是纯读 task_store + history,不算写入红线,任何环境都允许
    - SCHEMA_IMPORT_PREVIEW 读 information_schema,纯只读,prod 也允许(开 flag)
    - SCHEMA_IMPORT_SAVE 落本地 yml 文件(不写数据库),但生产侧默认拒以防 admin
      误把生产 schema 复制成 sandbox fixture
    """
    env = _get_env(datasource)
    db_type = _get_db_type(datasource)
    ds_name = getattr(datasource, "name", "?")

    # 1) 纯静态 / 纯只读操作 — 不连数据库或只读 task_store/history,任何环境允许
    if operation in (
        Operation.SQL_STATIC_PREFLIGHT,
        Operation.SQL_AI_ENRICH,
        Operation.SCENARIO_VERIFY,  # 只读 task_store + history,跟 ds 无 IO
    ):
        return True, "纯静态/AI 复核/只读校验,任何环境允许"

    # 2) unknown 环境 — 全部高风险操作拒,逼 admin 先确认环境
    if env == "unknown":
        return False, (
            f"datasource '{ds_name}' 环境未确认 (environment=unknown)。"
            "请到 admin 数据源管理页确认环境标签 (sandbox/staging/prod) 后再操作。"
            "fail-safe 设计:未知环境的高风险操作一律拒绝。"
        )

    # 3) EXPLAIN 三套(按方言)— allow_* flag 控制
    if operation == Operation.SQL_EXPLAIN_MYSQL:
        if db_type != "mysql":
            return False, f"db_type={db_type} 不应走 MySQL EXPLAIN 路径"
        if env == "sandbox":
            return True, "sandbox 默认允许"
        if not getattr(datasource, "allow_explain", False):
            return False, (
                f"该 MySQL {env} 数据源未开启执行计划查看权限。"
                "MySQL 使用 EXPLAIN SELECT 查看执行计划,不修改业务数据;"
                "如需开启请由管理员翻开 allow_explain。"
            )
        return True, f"allow_explain=True 在 {env} 允许"

    if operation == Operation.SQL_EXPLAIN_DM:
        if db_type != "dm":
            return False, f"db_type={db_type} 不应走 DM EXPLAIN 路径"
        if env == "sandbox":
            return True, "sandbox 默认允许"
        # DM 接受 allow_dm_explain 或 allow_explain 任一
        if not (
            getattr(datasource, "allow_dm_explain", False)
            or getattr(datasource, "allow_explain", False)
        ):
            return False, (
                f"该 DM {env} 数据源未开启执行计划查看权限。"
                "DM 使用 EXPLAIN SELECT 查看执行计划,不修改业务数据;"
                "如需开启请由管理员确认 allow_dm_explain。"
            )
        return True, f"DM EXPLAIN 在 {env} 允许"

    if operation == Operation.SQL_EXPLAIN_ORACLE_PLAN_TABLE:
        if db_type != "oracle":
            return False, f"db_type={db_type} 不应走 Oracle PLAN_TABLE 路径"
        if env == "sandbox":
            return True, "sandbox 默认允许"
        if not getattr(datasource, "allow_oracle_plan_table", False):
            return False, (
                f"该 Oracle {env} 数据源未开启 PLAN_TABLE 诊断写入权限。"
                "Oracle EXPLAIN PLAN FOR 会向诊断表 PLAN_TABLE 写一行临时记录"
                "(non-业务表);如需开启请由 DBA 确认 allow_oracle_plan_table。"
            )
        return True, f"Oracle PLAN_TABLE 在 {env} 允许(诊断写入)"

    # 4) Scenario 写入红线
    if operation in _SCENARIO_WRITE_OPERATIONS:
        # sandbox 才允许 — 即使 prod 上把 allow_scenario_write/allow_record_task
        # 翻开也无条件拒(产品红线,防止误操作灌生产)
        if env != "sandbox":
            return False, (
                f"⚠ 合规拒绝:datasource '{ds_name}' (environment={env}) "
                f"不允许执行 {operation.value}。造数据 / record / run-all 仅限 sandbox 环境;"
                "如需在此 ds 跑只读分析,走 /sql-diagnosis 即可。"
            )
        # sandbox 内部:materialize/run-all 看 allow_scenario_write;
        # record 看 allow_record_task
        if operation in (Operation.SCENARIO_MATERIALIZE, Operation.SCENARIO_RUN_ALL):
            if not getattr(datasource, "allow_scenario_write", False):
                return False, (
                    f"sandbox 但 allow_scenario_write=False 拒绝 {operation.value}。"
                    "demo / 测试 ds 应在 admin 数据源管理打开。"
                )
            return True, "sandbox + allow_scenario_write=True 允许"
        # SCENARIO_RECORD
        if not getattr(datasource, "allow_record_task", False):
            return False, (
                "sandbox 但 allow_record_task=False 拒绝 record。"
                "demo / 测试 ds 应在 admin 数据源管理打开。"
            )
        return True, "sandbox + allow_record_task=True 允许"

    # 5) SCENARIO_VERIFY — 纯读 task_store/history,任何环境允许
    if operation == Operation.SCENARIO_VERIFY:
        return True, "verify 纯只读,任何环境允许"

    # 6) Schema 元数据
    if operation == Operation.SCHEMA_IMPORT_PREVIEW:
        if env == "sandbox":
            return True, "sandbox 默认允许"
        if not getattr(datasource, "allow_schema_import", False):
            return False, (
                f"该 {env} 数据源未开启 schema 反查权限。"
                "schema 导入预览读 information_schema / all_tab_columns 等元数据,"
                "纯只读,但默认收紧;如需开启请由管理员翻开 allow_schema_import。"
            )
        return True, f"schema preview 在 {env} 允许"

    if operation == Operation.SCHEMA_IMPORT_SAVE:
        # save=True 把生产 schema 落成 sandbox fixture yml 是高风险动作 —
        # 不在生产 ds 上下文做(防 admin 误把生产 schema 名拷出来后又走 materialize)
        if env != "sandbox":
            return False, (
                f"⚠ schema yml 保存(save=True)仅限 sandbox 环境;"
                f"当前 datasource '{ds_name}' (environment={env}) 只允许 preview。"
                "若需把生产 schema 当 sandbox fixture 模板,请人工 copy yml 内容"
                "到 sandbox 域(或先把 ds 标记为 sandbox)。"
            )
        if not getattr(datasource, "allow_schema_save", False):
            return False, (
                "sandbox 但 allow_schema_save=False 拒绝。"
                "demo / 测试 ds 应在 admin 数据源管理打开。"
            )
        return True, "sandbox + allow_schema_save=True 允许"

    return False, f"unknown operation {operation}"


def assert_operation_allowed(
    user: "User | None",
    datasource: "DataSource",
    operation: Operation,
    context: dict[str, Any] | None = None,
) -> None:
    """主入口:决策 + 审计 + 拒绝时抛 OperationDenied。

    user 可为 None(unit test 不需要 audit 时)。其它 caller 都应传 current user
    以便审计追溯。

    audit 落:operation / allow|deny / reason / environment / db_type /
    datasource_id / datasource_name / request_id / extra context。
    """
    allowed, reason = _decide(datasource, operation)
    # 审计:allow / deny 都记(管理员能看到"被拒绝的尝试")
    try:
        from app.services.audit import record_auth_event
        event = f"{operation.value}.{'allowed' if allowed else 'denied'}"
        extra = {
            "operation": operation.value,
            "datasource_id": getattr(datasource, "id", ""),
            "datasource_name": getattr(datasource, "name", ""),
            "environment": _get_env(datasource),
            "db_type": _get_db_type(datasource),
            "allowed": allowed,
            "reason": reason,
        }
        if context:
            # caller-supplied context (sql_hash / scenario_id / 等),全推 extra
            extra.update({k: v for k, v in context.items() if k not in extra})
        username = getattr(user, "username", "") if user else ""
        user_id = getattr(user, "id", "") if user else ""
        record_auth_event(
            event,
            username=username,
            user_id=user_id,
            method="POLICY",
            path=f"/policy/{operation.value}",
            status_code=200 if allowed else 403,
            extra=extra,
        )
    except Exception:  # noqa: BLE001 — audit 失败不阻塞主决策
        logger.exception("operation_policy audit 写失败")

    if not allowed:
        raise OperationDenied(
            detail=reason,
            operation=operation,
            environment=_get_env(datasource),
        )


__all__ = [
    "Operation",
    "OperationDenied",
    "assert_operation_allowed",
]
