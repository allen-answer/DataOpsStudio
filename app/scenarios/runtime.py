"""Scenario runtime —— 端到端在真实 datasource 上跑 materialize。

切片 3 只产 plan + apply via fake executor。runtime 这一层把 plan + 真
datasource 连接缝起来：
    datasource_id → datasource_store 查 → pool.borrow + cursor → apply_plan
    → commit

放在独立模块（而不是 materializer.py 里）的原因：materializer 是纯 SQL
plan 模型，不该 import dbclients；runtime 承担 IO 编排。

API endpoint 调 `materialize_to_datasource(scenario, data, datasource_id)`，
单测可 monkeypatch 这一层。
"""
from __future__ import annotations

from typing import Any

from app.scenarios.generator import TableData
from app.scenarios.materializer import (
    CursorExecutor,
    apply_plan,
    build_materialize_plan,
    materialize_streaming,
)
from app.scenarios.models import Scenario


class ScenarioRuntimeError(RuntimeError):
    pass


def materialize_to_datasource(
    scenario: Scenario,
    data: dict[str, TableData] | None,
    datasource_id: str,
    *,
    drop_first: bool = True,
    batch_size: int = 500,
    streaming: bool = True,
) -> dict[str, Any]:
    """跑 materialize 到指定 datasource。

    Phase 14 P0-2 起默认走 **streaming** 路径(`materialize_streaming`)——
    内存恒定 O(batch_size × col_width),千万行不爆。data 参数可以传 None。

    `streaming=False` 走老 `build_materialize_plan + apply_plan` 路径(data
    必须传)——向后兼容老 caller / 单测路径。

    raises:
        ScenarioRuntimeError —— datasource 不存在 / 驱动未装 / 执行失败
    returns:
        summary dict(dialect / schemas_created / tables / warnings + streaming 标志)
    """
    if not datasource_id.strip():
        raise ScenarioRuntimeError("datasource_id is required")

    # lazy import 避免顶层依赖 dbclients（运行时按需）
    from app.dbclients import pool as _pool
    from app.dbclients.drivers import first_available_module
    from app.dbclients.factory import _connect
    from app.services.repositories import datasource_store

    source = datasource_store.get(datasource_id)
    if source is None:
        raise ScenarioRuntimeError(f"datasource not found: {datasource_id}")
    module_name = first_available_module(source.db_type)
    if not module_name:
        raise ScenarioRuntimeError(
            f"{source.db_type.value} driver is not installed"
        )

    if not streaming and data is None:
        raise ScenarioRuntimeError(
            "streaming=False 时必须传 data(老路径需要预先 generate_scenario 结果)"
        )

    try:
        with _pool.borrow(source, lambda: _connect(source, module_name)) as conn:
            cur = conn.cursor()
            try:
                if streaming:
                    summary = materialize_streaming(
                        scenario, CursorExecutor(cur),
                        drop_first=drop_first, batch_size=batch_size,
                    )
                else:
                    plan = build_materialize_plan(scenario, data or {}, drop_first=drop_first)
                    summary = apply_plan(plan, CursorExecutor(cur), batch_size=batch_size)
                conn.commit()
                return summary
            finally:
                try:
                    cur.close()
                except Exception:
                    pass
    except ScenarioRuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ScenarioRuntimeError(
            f"materialize failed on datasource={source.name}: {exc}"
        ) from exc
