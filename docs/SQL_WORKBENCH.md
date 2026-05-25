# SQL Workbench v0.1

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
- **Phase 2**(计划): 前端 `/sql-workbench` 视图,多 tab + CodeMirror 编辑器 + 结果 grid + history 面板
- **Phase 3**(计划): metadata tree(schemas/tables/columns)接 `datasource_introspect`
- **Phase 4**(计划): 编辑器顶部 / 历史行加快捷:发送当前 SQL 到 → 血缘分析 / 数据对比 / SQL 诊断
