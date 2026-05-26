# SQL 工作台 Explain & 静态规则提示(v0.5)

把 SQL 工作台 v0.2 已有的 explain endpoint 增强:加 **4 条文本规则** 给写 SQL
的人即时提醒,加 **执行耗时阈值标记** 让慢 SQL 一眼可见,加 **跨工作台元数据
透传** 让 SQL 优化工作台知道这条 SQL 从哪来。

## 后端 Explain

`POST /api/sql-workbench/explain`

```json
// request
{"datasource_id": "ds-xx", "sql": "SELECT * FROM users WHERE id=1"}

// response (success)
{
  "success": true,
  "dialect": "mysql",
  "columns": ["id", "select_type", "table", ...],
  "rows": [...],
  "explain_sql": "EXPLAIN SELECT * FROM users WHERE id=1",
  "elapsed_ms": 12,
  "unsupported": false,
  "error": null,
  "hints": [
    {"code": "select_star", "severity": "warning", "message": "..."}
  ]
}
```

### 方言矩阵

| 方言 | 处理 | 备注 |
|---|---|---|
| MySQL | `EXPLAIN <sql>` 直接前缀 | 支持 |
| OceanBase MySQL 模式 | 同 MySQL(datasource 配 `db_type=MySQL`) | 支持 |
| Oracle | 返 `unsupported=true` | EXPLAIN PLAN 需写 PLAN_TABLE,跟「SQL 诊断」模块重复 |
| DM 达梦 | 返 `unsupported=true` | 同 Oracle 路径 |
| OceanBase Oracle 模式 | 返 `unsupported=true`(datasource 配 `db_type=Oracle`) | 同 |
| DB2 | 返 `unsupported=true` | 同 |

**unsupported 不假装成功** — `success=false` + `unsupported=true` + `error` 字段含
明确原因,前端 UI 渲染黄色 banner 而非"空 plan 表格"。

### 静态规则(`app/sqlide/sql_hints.py`)

无论 plan 跑没跑通,只要请求里有 SQL 文本,response 都附 `hints` 数组。
v0.5 落地 4 条:

| code | 触发 | severity | 提示 |
|---|---|---|---|
| `select_star` | `SELECT *` / `SELECT DISTINCT *` | warning | 列爆炸 / I/O 浪费 |
| `no_where` | 有 `FROM` 但无 `WHERE` | warning | 可能全表扫描 |
| `leading_wildcard` | `LIKE '%xxx'` / `LIKE "%xxx%"` | warning | 索引失效 |
| `order_no_limit` | `ORDER BY` 但无 `LIMIT` / `FETCH FIRST` / `ROWNUM` / `TOP N` | warning | 排序大表代价高 |

`COUNT(*)` / `SUM(*)` 不算 `select_star`(正则 `\bSELECT\s+\*` 不匹配括号包的 *)。
注释里(`--` / `/* */`)的内容会被剥离,不计。

`POST /explain` 必经 **sql_guard.validate_readonly_sql** — DML/DDL/多语句一律拒,
跟 execute 一个口径。

## 前端 ExplainPanel

抽到独立组件 `components/sql/ExplainPanel.vue`,SqlWorkbenchView 底部 explain
tab 直接挂。四态:

1. **null**:"点击「Explain」查看执行计划"
2. **unsupported**:黄色 banner + `error` 文案
3. **failed**:红色 banner + `error` 文案
4. **success**:Markdown-friendly plan 表格

### 复制按钮(#9 落地)

成功状态下表格上方有两个按钮:

- **复制**(默认):拷贝成 Markdown 表格 + 顶部 `-- EXPLAIN SELECT ...` 注释,
  粘贴到 IM / 文档直接可读
- **JSON**:拷贝成 `{dialect, explain_sql, columns, rows, hints}` 结构,给
  AI 喂 / 长期归档用

底层调 `navigator.clipboard.writeText`,旧浏览器(无 clipboard API)降级到
`document.execCommand('copy')`。

### Hints 渲染

数组以 chip 形式渲染在 plan 表格上方;`severity` 决定底色(warning 黄 /
error 红 / info 蓝)。即使 plan 跑不通(Oracle/DM unsupported)hints 仍显示。

## 慢 SQL 阈值标记

`SLOW_THRESHOLD_MS = 3000`(SQL 工作台 view 内常量,简化版,不可配置)。

两个触点:

1. **执行成功后 result tab 顶部**:`elapsed_ms ≥ 3000` 时显示黄色 banner
   `⚡ 本次执行耗时 X.X 秒,SQL 可能需要优化` + 「✨ 发送到优化工作台 →」按钮
2. **历史表行**:`elapsed_ms ≥ 3000` 行的耗时列旁显示 `⚡SLOW` chip + 字色
   变橙色

## 跨工作台元数据透传(`utils/sqlTransfer.ts`)

`SqlTransfer` 扩展字段(全可选,向后兼容):

```ts
interface SqlTransfer {
  sql: string
  datasourceId?: string
  source?: string  // 'sql-workbench' / 'sql-workbench-history' / ...
  // v0.5 新增
  consoleId?: string
  consoleName?: string
  datasourceName?: string
  datasourceDbType?: string
  elapsedMs?: number
  executedAt?: string
}
```

SqlWorkbenchView 的 `sendTo()` / `sendHistoryEntry()` 自动塞:
- 当前 console name
- 当前 ds 的 name + db_type(从 bootstrap.datasources 反查)
- 上次执行的 elapsed_ms

SqlDiagnosisView onMounted 时读 transfer,有 `source` 字段就在顶部显示紫色
**来源信息卡**:

```
← 来源: SQL 工作台 · console: 月度报表 · 📂 demo-mysql (MySQL) · 🕐 上次执行 3.50 s ⚡SLOW
                                                                                       [✕]
```

`[✕]` 清掉 origin 卡(SQL 不清,继续在编辑器里)。

## 测试

- **后端**:`tests/test_sql_hints.py` — 24 case 覆盖 4 规则 + 综合 + 空 SQL +
  注释剥离 + 多 quote / case-insensitive / FETCH FIRST / ROWNUM / TOP N
- **前端**:`tests/components/ExplainPanel.test.js` — 8 case 覆盖 null /
  unsupported / failed / success / hints chip / 复制按钮(Markdown + JSON) /
  unsupported 时 hints 仍渲染

## 工作流示例

1. 在 SQL 工作台 console 里写 `SELECT * FROM orders ORDER BY created_at`
2. 点「Explain」→ 底部 explain tab 显示 plan 表格 + 3 条 hints:
   - `select_star`:列爆炸提醒
   - `no_where`:可能全表扫描
   - `order_no_limit`:排序大表代价高
3. 点「运行」执行 → 耗时 4.2s,result 顶部黄色 banner
4. 点「✨ 发送到优化工作台 →」→ 跳到 SQL 诊断 view
5. 诊断 view 顶部紫色卡片显示「来源: SQL 工作台 · console: 月度报表 · MySQL ·
   上次执行 4.2 s ⚡SLOW」,SQL 已经预填好,直接点诊断按钮深入
