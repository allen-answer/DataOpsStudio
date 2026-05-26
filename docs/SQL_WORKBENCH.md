# SQL Workbench

数据工程师 / DBA 日常跑 SELECT 的工作台。跟「数据对比 / SQL 诊断 / 场景测试沙盒」**平行**
模块,不替代任何一个 —— 它只是更轻的"打开就能查"通道。

> **范围**:仅只读查询(SELECT / WITH)。任何 DML / DDL 在送 DB 之前就被
> `app.utils.sql_guard.validate_readonly_sql` 拦掉。

## 版本演进

| 版本 | 主题 | 关键能力 |
|---|---|---|
| v0.1 | 后端 + 多 tab | API CRUD + JSON 存储 + SELECT 执行 + history ring buffer |
| v0.2 | IDE 体验 | SQL 格式化 + Explain + 异步执行 + cancel |
| v0.3 | 元数据 | metadata cache + 对象搜索 + 表详情面板 |
| v0.4 | 模板库 | 跨用户 / 跨数据源的 SQL 模板沉淀(详见 [SQL_TEMPLATES.md](SQL_TEMPLATES.md)) |
| v0.5 | 慢 SQL 闭环 | Execution 状态机 + KILL QUERY + Explain hints + 慢阈值标记 + 跨视图传透 |
| v0.5+ | 结果导出 | CSV / Excel / JSON / SQL 导出(详见 [SQL_EXPORT.md](SQL_EXPORT.md)) |

详细 hints + Explain 行为见 [SQL_EXPLAIN_HINTS.md](SQL_EXPLAIN_HINTS.md)。

## 路由

`/sql-workbench` —— sidebar 顶级一级菜单(editor+,Database 图标)。

## 安全限制

| 限制 | 实现位置 | 触发后行为 |
|---|---|---|
| SELECT / WITH 白名单 | `app/utils/sql_guard.py` | 200 + `success=false`,`error` 字段说明被拦 |
| 单语句 | `sql_guard._has_multiple_statements` | 同上 |
| `SELECT FOR UPDATE` 拒绝 | `sql_guard` | 同上 |
| FORBIDDEN keyword(call / lock / delete / insert / merge / replace / truncate / update / drop / create / alter / grant / revoke / execute) | `sql_guard.FORBIDDEN_SQL_KEYWORDS` | 同上 |
| 数据源 `allow_select=false` | `app/api/sql_workbench.py::execute` | 403 |
| 跨项目数据源 | `_authz.require_datasource_access` | 404 / 403 |
| `role < editor` | router 级 `Depends(require_role("editor"))` | 401 / 403 |
| `max_rows > 10000` | `ExecuteRequest` Pydantic + `executor._MAX_ROWS_HARD_CAP` | 422 / 静默 clamp 到 10000 |
| **单 cell > 64KB** | `executor._MAX_CELL_BYTES` | 截断 + 加 `...[CELL_TRUNCATED]` 后缀 |
| **整结果 > 64MB** | `executor._MAX_TOTAL_BYTES` | 提前终止 + `truncated=true` |

**不支持(刻意为之)**:
- DML / DDL 路径 —— 走 Compare Task / Scenario Materialize / SQL 诊断 等特定通道
- 写 prod —— SELECT 通过 sql_guard 后,prod ds 仍要 `allow_select=true`(默认 fail-safe)
- 跨用户共享 console / execution —— 全部按 owner_user_id 隔离

## REST API

所有端点要求 `Authorization: Bearer <token>` + `role >= editor`。

### Console(SQL Tab)CRUD
```
GET    /api/sql-workbench/consoles                        # 列自己的 tabs
POST   /api/sql-workbench/consoles    {name, datasource_id?, sql?, project_id?}
PUT    /api/sql-workbench/consoles/{id}  {name?, sql?, datasource_id?, project_id?}  (partial)
DELETE /api/sql-workbench/consoles/{id}
```

### 执行(异步 + 可中断)

```
POST   /api/sql-workbench/execute
```

```json
// request
{
  "datasource_id": "ds-xxx",
  "sql": "SELECT ...",
  "max_rows": 1000,
  "console_id": "...",
  "timeout_seconds": 300
}
// response (envelope)
{
  "execution_id": "exe-abc",
  "status": "pending" | "running" | "success" | "failed" | "cancelled",
  "cancel_requested": false,
  "timeout_seconds": 300,
  "cancel_reason": "user" | "timeout",          // 仅 cancelled 时
  // 终态时平铺(向后兼容 v0.1 客户端):
  "success": true,
  "columns": [...],
  "rows": [...],
  "row_count": N,
  "elapsed_ms": 12,
  "truncated": false,
  "error": null
}
```

服务端 short-poll 默认 300ms。快查在窗口内完成直接返 `success`;慢查返 `pending` /
`running` 让前端 poll。

```
GET    /api/sql-workbench/executions/{execution_id}      # 查当前状态(同 envelope)
POST   /api/sql-workbench/executions/{execution_id}/cancel
```

`cancel` 响应:`{ok, execution_id, cancel_requested: true}`。404 = 不存在或 TTL 清理;
403 = 跨用户;409 = 已 `success/failed/cancelled`。

#### 中断语义(v0.5 关键变化)

不再是"只能标记,worker 完成后丢结果"。v0.5 起对 MySQL 主动发 `KILL QUERY <connection_id>`
真驱动级中断;Oracle / DM 走 `callTimeout`(dialect 层已有)。兜底:

1. 设 `cancel_requested = True`(+ `cancel_reason = "user"` / `"timeout"`)
2. **MySQL**:旁路新连接发 `KILL QUERY` → in-flight `cursor.execute` 抛中断异常
3. **Oracle / DM**:`callTimeout` 已生效,语句到点自动 abort
4. **其它驱动 / 中断失败**:worker 完成后 check `cancel_requested`,丢弃结果不展示
5. history 仍记一条(`success=false`, `error="cancelled (user/timeout)"`)

#### 超时

`timeout_seconds` 范围 [1, 3600],默认 300。到点 → 自动 `cancel_requested=true` +
`cancel_reason="timeout"` + 触发同样的 KILL 链路。

### 格式化

```
POST   /api/sql-workbench/format
       {datasource_id?, sql}
```

```json
// 成功
{ "success": true, "formatted_sql": "SELECT *\nFROM t\nWHERE id = 1", "dialect": "mysql" }
// 失败 / 空(SQL 语法错或 strip 后空)
{ "success": false, "formatted_sql": "", "dialect": "mysql", "error": "..." }
```

- `sqlglot.transpile(pretty=True)`;dialect 走 `app.lineage.dialects.resolve_dialect`
  (`dm / dameng / ob_oracle` → `oracle`;`ob_mysql / oceanbase` → `mysql`)
- 失败时 `formatted_sql=""`,**不覆盖原 SQL**
- `datasource_id` 可选,空 → 默认 `mysql`

### Explain

```
POST   /api/sql-workbench/explain
       {datasource_id, sql}
```

返 `{success, dialect, columns, rows, explain_sql, elapsed_ms, unsupported, error, hints[]}`。

**方言矩阵**:MySQL / OB MySQL 真跑 `EXPLAIN <sql>`;Oracle / DM / OB Oracle / DB2 返
`unsupported=true` 引导用户去 [SQL 诊断](SQL_DIAGNOSIS.md)(`EXPLAIN PLAN FOR ...` 需写
PLAN_TABLE,跟 `slow_sql.analyze_sql` 链路重复)。

**hints**:即使 plan 跑不通,只要请求里有 SQL 文本,response 都附 hints 数组。当前 4
条静态规则(`select_star / no_where / leading_wildcard / order_no_limit`)。详见
[SQL_EXPLAIN_HINTS.md](SQL_EXPLAIN_HINTS.md)。

### 历史

```
GET    /api/sql-workbench/history?datasource_id={ds}&limit=100
```

返 `{items: [{id, datasource_id, datasource_name, sql, executed_by, project_id,
executed_at, success, elapsed_ms, row_count, truncated, error?}]}`。

ring buffer cap 5000 条防膨胀。按用户隔离,只看自己的执行。

### 元数据(v0.3 真接 introspect)

```
GET    /api/sql-workbench/metadata/schemas?datasource_id={ds}
GET    /api/sql-workbench/metadata/tables?datasource_id={ds}&schema={s}
GET    /api/sql-workbench/metadata/columns?datasource_id={ds}&schema={s}&table={t}
```

`app/sqlide/metadata.py` 接 `datasource_introspect` 走 information_schema /
all_tab_columns,支持 MySQL / Oracle / DM / DB2 全方言。

**缓存层** `app/sqlide/metadata_cache.py`:按 datasource_id 缓存 schemas+tables+columns,
TTL 300s,可被 admin endpoint 主动失效。预热路径在用户切 datasource 时自动触发,让
`SELECT * FROM <schema>.` 后的补全立即就绪。

### 对象搜索(v0.3)

```
GET    /api/sql-workbench/search?datasource_id={ds}&q=user&limit=50
```

跨 schema / table / column 三类对象全文匹配,前端搜索条挪到顶部跨 tab 都可见。

### 表详情(v0.3)

```
GET    /api/sql-workbench/metadata/table-detail?datasource_id={ds}&schema={s}&table={t}
```

返回列定义 + 索引 + 估算行数 + 引用关系。

### 模板库(v0.4)

详见 [SQL_TEMPLATES.md](SQL_TEMPLATES.md)。

### 结果导出(v0.5+)

详见 [SQL_EXPORT.md](SQL_EXPORT.md)。

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

模板单独落 `config/sql_templates.json`(+ `config/sql_templates.example.json` 入仓做内
置)。导出文件落 `results/sql_exports/`,24h TTL 清理。

execution / metadata_cache 全部 in-memory,不持久化 —— execution 走 TTL 1h 内存清理。

文件权限 0600(POSIX),跟 `datasources.json` 同口径。

## 后端模块

```
app/sqlide/
├── __init__.py        # 公开 API re-export
├── models.py          # Pydantic schemas
├── storage.py         # SqlWorkbenchStore(consoles + history)
├── executor.py        # execute_sql + cell/total OOM 防护
├── runtime.py         # Execution + 状态机 + ThreadPoolExecutor + KILL QUERY
├── format.py          # sqlglot.transpile 包装
├── explain.py         # MySQL EXPLAIN + 方言 unsupported 路由
├── sql_hints.py       # 4 条静态规则(select_star / no_where / leading_wildcard / order_no_limit)
├── metadata.py        # introspect schemas / tables / columns
├── metadata_cache.py  # TTL 300s + 主动失效
├── search.py          # 跨 schema/table/column 搜索
└── template_store.py  # SQL 模板 CRUD + 内置 example union

app/services/
└── sql_export.py      # 4 格式导出 + 异步状态机 + 公式注入防御

app/api/
├── sql_workbench.py   # 主 router(19 个 endpoint,routes.py 子模块)
└── sql_templates.py   # 模板 CRUD + 导入导出
```

## 前端 UX

`views/SqlWorkbenchView.vue` + `stores/sqlWorkbench.ts` + `stores/sqlTemplates.ts`。

主要交互:
- **工具栏**:运行 / ⏹ 停止(running 时切红)/ ✨ 格式化(`Alt+Shift+F`)/ 🔬 Explain /
  📑 存为模板 / 📤 发送到
- **底部 tab**:结果 / 历史 / 元数据(schema 树 + 搜索)/ Explain / 模板
- **慢 SQL 阈值** `SLOW_THRESHOLD_MS = 3000`:result 顶部黄色 banner + 「✨ 发送到优化
  工作台 →」按钮;history 表行加 `⚡SLOW` chip
- **别名补全**:`FROM users t` 后键入 `t.` 列 users 字段(`SqlEditor.alias.test.js` 覆盖)
- **6 个 snippets**:CodeMirror `startCompletion` 提示常用片段

## 跨视图传 SQL 桥

`frontend/frontend/src/utils/sqlTransfer.ts` 用 sessionStorage:

```ts
setSqlTransfer({ sql, datasourceId?, source?, consoleId?, consoleName?,
                  datasourceName?, datasourceDbType?, elapsedMs?, executedAt? })
takeSqlTransfer(): { ... } | null   // 接收方一次性读 + 清
```

走 sessionStorage 而非 URL —— SQL 经常含特殊字符 / 很长 / 不应跟分享链接走。

接收方:
- **SQL 诊断** (`/sql-diagnosis`) onMounted 调 `takeSqlTransfer()` 预填 + 顶部紫色"来源
  信息卡"
- **血缘分析** (`/lineage`) 同
- **数据对比** (`/data-compare`) 双 SQL 形态接入

## 测试

```bash
# 后端
pytest tests/test_sql_workbench_*.py
# - storage / executor / api(基础 CRUD + execute + history)
# - v02(format / explain / executions + cancel + poll)
# - metadata_cache / templates / export
# - hints(24 case)

# 前端
cd frontend/frontend && npm test
# - stores/sqlWorkbench.test.js(48 case)
# - stores/sqlTemplates.test.js(9 case)
# - components/ExplainPanel.test.js(8 case)
# - components/SqlEditor.alias.test.js(别名补全)
```

## 路线图

短期完成,长期看真实使用反馈再扩:

- 双 SQL 发送到数据对比的完整接入(目前 backlog)
- 元数据搜索的 fuzzy 匹配 + 高亮
- 模板市场 / 评分 / 收藏 —— 等多客户使用后再判
- DML / DDL 通道 —— **当前明确不做**,新需求走 Compare Task / Scenario / SQL 诊断

## 相关文档

- [SQL_TEMPLATES.md](SQL_TEMPLATES.md) —— v0.4 模板库 11 字段 + 内置 example + 导入导出
- [SQL_EXPORT.md](SQL_EXPORT.md) —— v0.5+ 4 格式导出 + Excel 公式注入防御
- [SQL_EXPLAIN_HINTS.md](SQL_EXPLAIN_HINTS.md) —— v0.5 Explain 增强 + 4 条静态规则 + 慢
  SQL 跨视图传透
- [SQL_DIAGNOSIS.md](SQL_DIAGNOSIS.md) —— `/sql-diagnosis` 独立 view,慢 SQL 深度诊断 +
  AI 复核(workbench 把慢 SQL "发送过去"的目标)
