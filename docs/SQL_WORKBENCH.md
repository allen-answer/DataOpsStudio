# SQL Workbench v0.2

> v0.1 后端 + 多 tab + 元数据 + 跨视图打通已交付。v0.2 增强 IDE 体验:
> SQL 格式化、Explain 执行计划、查询中断(异步执行模型)。仍保持只读安全策略。

## v0.2 新增端点

### 格式化 `POST /api/sql-workbench/format`

```json
// request
{ "datasource_id": "ds-xxx", "sql": "select * from t where id=1" }
// response
{ "success": true, "formatted_sql": "SELECT *\nFROM t\nWHERE id = 1", "dialect": "mysql" }
// 失败时(SQL 语法错 / 空):
{ "success": false, "formatted_sql": "", "dialect": "mysql", "error": "格式化失败: ..." }
```
- 基于 `sqlglot.transpile(pretty=True)`,dialect 映射跟 `app.lineage.dialects.resolve_dialect` 一致:
  - `MySQL` → `mysql`(含 OceanBase MySQL mode:`ob_mysql` / `oceanbase` → `mysql`)
  - `Oracle` / `DM` / `dameng` / `ob_oracle` → `oracle`
- 失败时 `formatted_sql` 为空,**不覆盖原 SQL**,前端能安全展示错误
- `datasource_id` 可选,空 → 默认 `mysql` 方言

### Explain `POST /api/sql-workbench/explain`

```json
// request
{ "datasource_id": "ds-xxx", "sql": "SELECT * FROM users WHERE id=1" }
// response (MySQL)
{
  "success": true, "dialect": "mysql",
  "columns": ["id", "select_type", "table", "type", "rows", "Extra"],
  "rows": [[1, "SIMPLE", "users", "ALL", 1000, "Using where"]],
  "explain_sql": "EXPLAIN SELECT * FROM users WHERE id=1",
  "elapsed_ms": 12, "unsupported": false, "error": null
}
// Oracle / DM (unsupported):
{
  "success": false, "dialect": "oracle", "columns": [], "rows": [],
  "explain_sql": "", "elapsed_ms": 0, "unsupported": true,
  "error": "Oracle / DM EXPLAIN PLAN 在 SQL Workbench v0.2 未启用 —— ..."
}
```
- 仍只允许 SELECT/WITH(`sql_guard` 拦截)
- MySQL / OceanBase MySQL:`EXPLAIN <sql>` + fetch_rows
- Oracle / DM / OceanBase Oracle:返 `unsupported=true` + 引导用户去 SQL 诊断模块
  (`EXPLAIN PLAN FOR ...` 需写 PLAN_TABLE,跟 slow_sql.analyze_sql 链路重复)
- DB2:同 unsupported

### 异步执行 + 中断

#### `POST /api/sql-workbench/execute` (envelope 升级)

```json
// request 不变
{ "datasource_id": "ds-xxx", "sql": "SELECT ...", "max_rows": 1000, "console_id": "..." }
// response (envelope):
{
  "execution_id": "exe-abc",
  "status": "done" | "running" | "failed" | "cancelled",
  "cancel_requested": false,
  // done/failed 时平铺(v0.1 兼容):
  "success": true, "columns": [...], "rows": [...], "row_count": N,
  "elapsed_ms": 12, "truncated": false, "error": null
}
```
- 服务端 short-poll 默认 300ms;快查直接返 `done`,慢查返 `running` 让前端 poll
- v0.1 客户端不动可用 —— envelope 在 done 时平铺仍含 `success/columns/rows`

#### `GET /api/sql-workbench/executions/{execution_id}` (新)
返回当前 execution 状态(同 envelope shape)。

#### `POST /api/sql-workbench/executions/{execution_id}/cancel` (新)
```json
{ "ok": true, "execution_id": "exe-abc", "cancel_requested": true }
```
- 404:execution 不存在或已 TTL 清理
- 403:无权 cancel 他人的 execution
- 409:execution 已 `done/failed/cancelled`,不能再 cancel

**实现说明**:大多数 DB-API 驱动不支持中途真 cancel。本模块的兜底是:
1. 设 `cancel_requested=True`
2. worker 完成时再 check;如果 cancel 了,丢弃结果不展示,状态标为 `cancelled`
3. history 仍记一条(success=false, error="cancelled")

cancel 后 SQL 仍会在 DB 端跑完(浪费一次资源),但用户不再看到结果。

## 前端 UX

- 顶部增加 ✨「格式化」按钮(快捷键 **Alt + Shift + F**)
- 「运行」按钮在执行中切换为红色「停止」按钮(底层不支持真中断时仍标记)
- 顶部「Explain」按钮 → 切到底部 Explain tab 展示 plan 表格
- 底部新 tab:**Explain**(plan 表格 + explain_sql 显示 + unsupported 友好提示)


数据工程师 / DBA 日常跑 SELECT 的工作台。跟现有「数据对比 / 慢 SQL 诊断 / 场景测试沙盒」**平行**,不替代任何模块 —— 它只是更轻的"打开就能查"通道。

## 当前能力(v0.1 / Phase 1 后端)

- 仅允许 **SELECT / WITH** 查询。任何 DML / DDL(INSERT / UPDATE / DELETE / CREATE / DROP / TRUNCATE / GRANT 等)
  在请求送到 DB 前就被 `app.utils.sql_guard.validate_readonly_sql` 拦下,返回 `success=false`。
- 多 console tab(SQL 标签页),内容 + 数据源选择 + SQL 草稿持久化到 `config/sql_workbench.json`,
  浏览器刷新或重启 app 后恢复。
- 历史记录:每次执行都落 history(成功/失败都记),按用户隔离;ring buffer cap 5000 条防膨胀。
- 复用现有数据源 + 项目 + 权限体系。每次执行都走 `require_datasource_access` 校验,且只有
  `role >= editor` 的用户能用本模块(viewer 看不到入口,API 拒 403)。
- 服务端硬上限 `max_rows ≤ 10000`(请求模型 + executor 双层 clamp 防绕过)。

## 安全限制

| 限制 | 实现位置 | 触发后行为 |
|---|---|---|
| SELECT / WITH 白名单 | `app/utils/sql_guard.py` | 200 + `success=false`,`error` 字段说明被拦 |
| 单语句 | `sql_guard._has_multiple_statements` | 同上 |
| `SELECT FOR UPDATE` 拒绝 | `sql_guard` | 同上 |
| FORBIDDEN keyword(call / lock / delete / insert / merge / replace / truncate / update / drop / create / alter / grant / revoke / execute) | `sql_guard.FORBIDDEN_SQL_KEYWORDS` | 同上 |
| 数据源 `allow_select=false` | `app/api/sql_workbench.py::execute` | 403 `数据源 X 已禁用 SELECT` |
| 跨项目数据源 | `app/api/_authz.require_datasource_access` | 404 / 403 |
| `role < editor` | router 级 `Depends(require_role("editor"))` | 401 / 403 |
| `max_rows > 10000` | `ExecuteRequest` Pydantic + `executor._MAX_ROWS_HARD_CAP` | 422 / 静默 clamp 到 10000 |

**不支持(刻意为之)**:
- DML/DDL 路径:目前 v0.1 不开。需要的人请用 Compare Task / Scenario Materialize / 慢 SQL 诊断 等已存在的特定通道。
- 写 prod:即使 SELECT 通过 sql_guard,prod 环境的 ds 仍要 `allow_select=true`(默认 fail-safe = unknown 全锁,admin 显式打开)。
- 跨用户共享 console:v0.1 console 按 owner_user_id 隔离,只列自己的。

## REST API

所有端点要求 `Authorization: Bearer <token>` + `role >= editor`。

### Console (SQL Tab)
```
GET    /api/sql-workbench/consoles                        # 列自己的 tabs
POST   /api/sql-workbench/consoles    {name, datasource_id?, sql?, project_id?}
PUT    /api/sql-workbench/consoles/{id}  {name?, sql?, datasource_id?, project_id?}  (partial)
DELETE /api/sql-workbench/consoles/{id}
```

### 执行
```
POST   /api/sql-workbench/execute
       {datasource_id, sql, max_rows?: 1000, console_id?}
       →  {success, columns: [...], rows: [[...], ...], row_count, elapsed_ms, truncated, error?}
```

`rows` 是 column-ordered list of lists(紧凑、保列序);单元格按类型归一化:
- `datetime / date / time` → ISO 8601 string
- `Decimal` → float(精度损失;后续可考虑加 cell_meta)
- `bytes / bytearray` → hex string
- `None / bool / int / float / str` → 原样

### 历史
```
GET    /api/sql-workbench/history?datasource_id={ds}&limit=100
       →  {items: [{id, datasource_id, datasource_name, sql, executed_by,
                    executed_at, success, elapsed_ms, row_count, truncated, error?}]}
```

### 元数据(Phase 3 实)
```
GET    /api/sql-workbench/metadata/schemas?datasource_id={ds}      # 当前 v0.1 stub
GET    /api/sql-workbench/metadata/tables?datasource_id={ds}&schema={s}
GET    /api/sql-workbench/metadata/columns?datasource_id={ds}&table={t}
```
Phase 1 返回 `{items: [], phase: 1, note: "metadata tree shipped in Phase 3"}`,等
Phase 3 接 `datasource_introspect` 真正拉 information_schema。

## 数据持久化

唯一文件:`config/sql_workbench.json`,root object:
```json
{
  "consoles": [
    {"id": "...", "name": "tab-1", "datasource_id": "...", "sql": "SELECT 1",
     "owner_user_id": "...", "project_id": "...",
     "created_at": "2026-05-26T...", "updated_at": "..."}
  ],
  "history": [
    {"id": "...", "datasource_id": "...", "sql": "...", "executed_by": "alice",
     "executed_at": "...", "success": true, "elapsed_ms": 12, "row_count": 5,
     "truncated": false, "error": null}
  ]
}
```

文件权限 0600(POSIX),跟 `datasources.json` 同口径。

## 后端模块

```
app/sqlide/
├── __init__.py        # 公开 API re-export
├── models.py          # Pydantic schemas (Console / ExecuteRequest / HistoryEntry / ...)
├── storage.py         # SqlWorkbenchStore — 单文件 root-dict + thread-safe RLock
└── executor.py        # execute_sql(source, sql, max_rows) → ExecuteResponse

app/api/sql_workbench.py # FastAPI router,挂在 routes.py 第 19 个子模块
```

## 测试

```
tests/test_sql_workbench_storage.py    # 10 测 SqlWorkbenchStore 直测
tests/test_sql_workbench_executor.py   # 8 测 execute_sql + sql_guard + 类型序列化
tests/test_sql_workbench_api.py        # 14 测 endpoint 鉴权 + CRUD + execute + history
```

跑:
```bash
pytest tests/test_sql_workbench_*.py
```

## 路线图

- **Phase 1**(已交付): 后端 API + JSON 存储 + SELECT 执行 + 测试 + 本文档
- **Phase 2**(已交付): 前端 `/sql-workbench` 视图,多 tab + CodeMirror 编辑器 + 结果 grid + history 面板
- **Phase 3**(已交付): metadata 树 tab 接 `app/sqlide/metadata.py` —— 支持 MySQL / Oracle / DM / DB2 的
  schemas / tables / columns 列表;前端 lazy-load + 点表名插入 `SELECT * FROM ... LIMIT 100;`
- **Phase 4**(已交付): "发送到" 菜单 + history 行 hover 快捷,SQL 通过 `utils/sqlTransfer` sessionStorage
  桥跳转到 → 血缘分析(`/lineage`)/ 数据对比(`/data-compare`,backlog 二端 SQL 接收)/ SQL 诊断(`/sql-diagnosis`)。
  目标视图 `onMounted` 调 `takeSqlTransfer()` 预填。Lineage / Diagnosis 已接;Compare 双 SQL 形态待 v0.2 完整接入

## 跨视图传 SQL 桥

`frontend/frontend/src/utils/sqlTransfer.ts` 提供:
```ts
setSqlTransfer({ sql, datasourceId?, source? })   // 发起方写
takeSqlTransfer(): { sql, datasourceId?, source? } | null  // 接收方读 + 一次性清
```
走 sessionStorage 而非 URL query —— SQL 经常含特殊字符 / 可能很长 / 用户分享链接时不应带走 payload。
