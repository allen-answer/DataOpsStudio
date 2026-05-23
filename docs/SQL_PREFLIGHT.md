# sql_preflight —— 对比 SQL 运行前静态体检

安全加固方案 P0 #2。跟 [resource_guard](./RESOURCE_GUARD.md) 互补：
resource_guard 看任务形状（max_rows / 格式 / 并发），sql_preflight 看 SQL
文本本身。

实现：`app/services/sql_preflight.py`。端点：`POST /api/sql/preflight`
（挂在 `app/api/tasks.py` 的 router 下，紧贴 run）。

## 三级静态检查

`assess_sql()` 纯函数，不连库：

1. **read-only guard 复检** —— 复用 `utils.sql_guard.validate_readonly_sql`，
   非单条 SELECT/WITH、含 DML/DDL/多语句 → `block`。
2. **sqlglot AST 解析** —— 解析失败保守按 `warn` 处理，不静默放行。
3. **AST 规则引擎** —— 见下表。

## 规则表

| 规则代码 | 触发条件 | 级别 |
|---|---|---|
| `not_readonly` | 未过只读校验（DML/DDL/多语句/FOR UPDATE） | block |
| `parse_failed` | sqlglot 解析失败 | warn |
| `select_star` | `SELECT *` | warn（大任务 → block） |
| `no_where` | 没有 WHERE 子句 | warn（大任务 → block） |
| `stream_no_order` | `stream_compare=true` 且无 `ORDER BY` | block |
| `order_missing_keys` | `stream_compare=true`，`ORDER BY` 未以主键为前缀 | block |
| `wide_select` | 选择列数 > 50 | warn |
| `expensive_ops` | 含 DISTINCT / GROUP BY / WINDOW / UNION | warn |
| `order_func_wrapped` | `ORDER BY` 列被函数包裹 | warn |

「大任务」= `max_rows > 100 万`。`stream_*` 规则仅在 `mode=compare` 且
`stream_compare=true` 时生效（流式归并要求两端按主键有序，否则结果错配）。

`risk_level`：有 `stream_no_order`/`order_missing_keys` → critical；其它
block → high；仅 warn → medium；无 → low。`blocking = 有任一 block`。

## 端点

`POST /api/sql/preflight`（`require_role(editor)`）：

请求体：

```json
{
  "sql": "SELECT ...",
  "dialect": "mysql",
  "key_columns": ["id"],
  "mode": "compare",
  "max_rows": 100000,
  "stream_compare": false
}
```

`key_columns` 支持数组或逗号分隔字符串。响应是 `SQLPreflightDecision`
（`dialect / risk_level / blocking / rules[] / normalized_sql`）。

端点是 **advisory** —— 只返回体检结果，不拦运行。Workbench 应在点「运行」
前调用并展示风险；高危 SQL 由用户决定是否继续。

## 在 run / run-async 强制 block 规则

除 advisory 端点外，`/api/tasks/{id}/run` 与 `/run-async` 内置 enforce 通道：

- `DATAOPS_SQL_PREFLIGHT_ENFORCE=false`（默认）：dry-run，不查不拦。
- `DATAOPS_SQL_PREFLIGHT_ENFORCE=true`：每次运行前对 source / target SQL 都
  跑一遍 `assess_sql`，**任一侧 `blocking=True` → 429**（详情含具体 block
  规则消息）。Excel / CSV / Parquet 源跳过。

跟 [resource_guard](./RESOURCE_GUARD.md) 同样的 dry-run → enforce 推进节奏。
配套观察期默认走 advisory，确认无误伤再切 enforce。

## 未覆盖（后续切片）

- 前端 Workbench 运行前调用 + 风险弹窗。
- EXPLAIN Broker（连只读账号 / 影子库做计划评估）—— `explain_used` 字段已预留。
- LOB/BLOB 大字段、谓词函数包裹等需要 schema 知识的规则。
