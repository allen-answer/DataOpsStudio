"""SQL Workbench v0.1 —— 数据工程师 / DBA 日常跑 SELECT 的工作台。

设计原则(跟 Phase 14 「SQL 优化沙盒」/ 慢 SQL 分析平行,但更轻):
- 仅 SELECT / WITH:复用 `app/utils/sql_guard.validate_readonly_sql`,DML / DDL
  直接拒绝。第一版**不开放**给生产以外的写库通道。
- 复用既有 dbclients (`fetch_rows_with_schema`) + datasource_store + project /
  role 鉴权 (`require_datasource_access` + `require_role('editor')`)。
- 持久化 console + history 到 `config/sql_workbench.json` 单文件(用户偏好)。
  store 内部是 `{"consoles": [...], "history": [...]}` root object。
- 多 console tab = 单文件的 list 元素。前端刷新可拿回原 tab。

后续 phase 扩展:
- Phase 3:metadata tree(schemas / tables / columns)走 `datasource_introspect`
- Phase 4:打通 → 血缘分析 / 数据对比 / SQL 诊断 三个入口
"""
from app.sqlide.models import (
    Console,
    ConsoleCreate,
    ConsoleUpdate,
    ExecuteRequest,
    ExecuteResponse,
    HistoryEntry,
)
from app.sqlide.storage import sql_workbench_store
from app.sqlide.executor import execute_sql, SqlWorkbenchError

__all__ = [
    "Console",
    "ConsoleCreate",
    "ConsoleUpdate",
    "ExecuteRequest",
    "ExecuteResponse",
    "HistoryEntry",
    "sql_workbench_store",
    "execute_sql",
    "SqlWorkbenchError",
]
