# 作业流参数使用指南

DataOpsStudio 的作业流是**参数驱动**的：在 `params` 节点定义一组带类型的参数，
下游节点（`compare` / `lineage` / `http` / `excel_export`）的 SQL、URL、文件名、
Sheet 名等任意字符串字段都可以用 `${...}` 语法引用这些参数。运行时引擎在节点
执行前完成插值。

---

## 1. 参数类型

| 类型 | 说明 | 解析后的值 | 典型用途 |
| --- | --- | --- | --- |
| `fixed` | 固定字面值 | `default` 字段（字符串） | 系统代码、批次号、表名 |
| `date` | 指定日期 | `default` 字符串（如 `2026-05-01`） | 业务日期 |
| `relative_date` | 相对日期 | 按 `source` 即时计算 | 每日跑批的「昨天」 |
| `multi_value` | 多值列表 | `default`（数组） | 分区列表、ID 列表 |
| `sql_result` | SQL 查询结果 | 在指定 datasource 执行 SQL，取**第一列**作为列表 | 动态生成的 ID 列表 |
| `json` | JSON 字符串 | `default`（字符串原样） | 复杂结构化配置 |

`relative_date` 的 `source` 取值：
- `today` → `2026-05-01`（今天，ISO 日期）
- `yesterday` → `2026-04-30`
- `last_month` → `2026-04`（上月，ISO 年月）
- `now` → `2026-05-01T14:30:00`（当前时间戳）

`sql_result` 例子：
```yaml
- name: tier1_users
  type: sql_result
  datasource: mysql8
  sql: "SELECT id FROM dim_users WHERE tier = 1 AND active = 1"
# → tier1_users = [1, 5, 9, 12, ...]   （取 SELECT 的第一列）
```

调用 `run_workflow` 时通过 `variables=` 传入的覆盖值**永远**优先于参数定义里的
`default` —— 这是手动触发时调整参数的入口。

---

## 2. 引用语法

### 2.1 工作流变量：`${name}`

引用 workflow 级变量域中的名字。来源（按优先级）：
1. `run_workflow(workflow, variables=...)` 调用时显式传入
2. `workflow.default_variables` 字典
3. `params` 节点的标量输出（`int` / `float` / `str` / `bool`，自动合并）
4. 引擎内置变量：`today` / `now` / `year` / `month` / `day`

```sql
WHERE id = ${user_id}                    -- → WHERE id = 42
WHERE dt = '${biz_date}'                 -- → WHERE dt = '2026-05-01'
WHERE created_at >= '${now}'             -- → WHERE created_at >= '2026-05-01T14:30:00'
```

> ⚠️ 字符串类型的参数嵌入 SQL 需要**自己加单引号**。`'${biz_date}'` 才合法，
> `${biz_date}` 直接写到 SQL 里会变成裸字面量。

### 2.2 上游节点输出：`${nodes.<id>.<path>}`

引用某个**已完成**的上游节点的输出字段。`path` 是用 `.` 串起来的 dict key
路径，遇到 list 时下标用整数：

```text
${nodes.compare1.summary.diff}      # compare 节点的 summary.diff 字段
${nodes.compare1.summary.same}      # 同节点的另一个字段
${nodes.params.user_ids}            # 整个 list（注意：会被 str() 成 [1, 2, 3]）
${nodes.params.user_ids.0}          # list 第 0 项
${nodes.compare1.samples.diff.0.changes.amount.source}   # 任意深度
```

> **依赖关系**：要引用 `nodes.X.Y`，当前节点必须把 X 写到 `depends_on` 里，否则
> 引擎按拓扑序还没跑到 X，输出尚未就绪。

### 2.3 列表过滤器：`${... | filter}`

把解析后的值再过一道格式化。当前支持：

| 过滤器 | 输入 | 输出 | 用途 |
| --- | --- | --- | --- |
| `sql_in` | list / 标量 | SQL `IN()` 子句体 | 多值参数→IN 子句 |
| `csv` | list | 逗号分隔字符串（不加引号） | 内部数字 ID 列表 |

```sql
WHERE id IN (${user_ids | sql_in})            -- → WHERE id IN (1, 5, 9)
WHERE name IN (${names | sql_in})             -- → WHERE name IN ('alice', 'bob')
WHERE id IN (${nodes.params.tier1 | sql_in})  -- 上游输出 + 过滤器组合
```

`sql_in` 的细节：
- 数字（int / float）保留原样：`[1, 5, 9]` → `1, 5, 9`
- 字符串自动单引号包裹，内部 `'` 转义为 `''`：`["O'Brien"]` → `'O''Brien'`
- `True` / `False` → `1` / `0`
- `None` → `NULL`
- 空 list → `NULL`（`IN()` 是非法 SQL；`IN(NULL)` 永真为 false，安全且合法）
- 标量自动包成单元素列表：`42` → `42`、`"a"` → `'a'`

---

## 3. 解析顺序

引擎在每个节点执行前会对 `node.config` 做**深度递归插值**（dict / list / str 都
会走一遍），按下面的顺序确定每个 `${...}` 的值：

```
${name}                       ${nodes.X.Y.Z}
   │                              │
   ├─ runtime variables          ├─ outputs[X][Y][Z]   （X 必须已完成）
   ├─ workflow.default_variables ├─ list 用 .<int> 取下标
   ├─ params 节点 emit 的标量    └─ 缺失 → 节点 FAILED, error="unresolved variable"
   ├─ 内置 today / now / ...
   └─ 缺失 → 节点 FAILED
```

**插值结果再过过滤器**（如果有）：`${var | sql_in}` 先解析 `var`，再把值喂给
`sql_in`。链式过滤器今后可扩展：`${var | filter1 | filter2}`。

---

## 4. 常见模式

### 4.1 同一个对比任务，每天跑不同分区

```yaml
nodes:
  - id: params
    type: params
    config:
      parameters:
        - name: biz_date
          type: relative_date
          source: yesterday
  - id: compare
    type: compare
    depends_on: [params]
    config:
      task_id: my-orders-compare        # 引用已存的对比任务
      source_sql_override: |
        SELECT id, user_id, amount
        FROM orders
        WHERE dt = '${biz_date}'
        ORDER BY id
      target_sql_override: |
        SELECT id, user_id, amount
        FROM orders_v2
        WHERE dt = '${biz_date}'
        ORDER BY id
```

每天 02:00 跑这个工作流，`biz_date` 自动是「昨天」，SQL 自动绑定到对应分区。
保存的 task 不动，仅本次 run 生效。

### 4.2 限定 ID 集合（多值 IN 子句）

```yaml
parameters:
  - name: vip_users
    type: sql_result
    datasource: mysql8
    sql: "SELECT id FROM dim_users WHERE vip_level = 1"
# 假设解析得 [1, 5, 9, 12]

# 下游 SQL：
SELECT * FROM orders WHERE user_id IN (${vip_users | sql_in})
# → SELECT * FROM orders WHERE user_id IN (1, 5, 9, 12)
```

### 4.3 条件节点：差异 > 0 才发告警

```yaml
nodes:
  - id: compare
    type: compare
    config: { task_id: my-task }
  - id: notify
    type: http
    depends_on: [compare]
    when: "${nodes.compare.summary.diff} > 0"        # 0 时跳过
    config:
      url: "https://hooks.example.com/alert"
      method: POST
      body: '{"text": "${nodes.compare.summary.diff} 行差异"}'
```

### 4.4 Sheet 名也支持参数

文件名由 `excel_export` 在写盘时自动命名（`<workflow_name>_<run_id_short>.xlsx`），
避免冲突 + 可追溯。Sheet 名则可以引用变量。

```yaml
- id: export
  type: excel_export
  config:
    sheets:
      - id: summary
        sheet_name: "汇总_${biz_date}"
        source: summary
```

---

## 5. 注意事项

1. **SQL 安全**：所有用户 SQL 都过 `app/utils/sql_guard.py` 的只读校验，DML / DDL
   关键字直接拒绝。`sql_in` 过滤器对字符串自动转义单引号，常见 SQL 注入路径
   被堵死。但 SQL 拼接本身不是端到端防御，**生产部署时 datasource 账号应只
   有 SELECT 权限**。
2. **变量缺失**：引用了未定义的 `${var}` 或未完成的 `${nodes.X.Y}`，节点
   立刻标记 FAILED，错误信息形如 `unresolved variable: foo`。下游会按 DAG
   连级 SKIPPED。
3. **list 直接拼字符串**：`${user_ids}` （没加 `| sql_in`）会拼成 Python list
   repr `[1, 2, 3]`，不是合法 SQL。要么加过滤器，要么用 `${nodes.x.user_ids.0}`
   取单值。
4. **`when` 表达式不参与变量域合并**：`when` 在配置插值之前评估，能引用
   workflow 变量和已完成节点的输出，但不能引用同一节点 config 里的临时变量。
5. **类型提示**：参数定义里写的 `description` 字段在前端 UI 显示给协作者，
   保持简短一句话，回答「这个参数是什么、谁该改它」。

---

## 6. 例子：一份完整的工作流

```yaml
name: 订单日清洗
default_variables:
  env: prod
nodes:
  # ──── 准备参数 ─────────────────────────────────
  - id: params
    type: params
    config:
      parameters:
        - { name: biz_date, type: relative_date, source: yesterday, required: true,
            description: 业务日期，默认昨天 }
        - { name: batch_id, type: fixed, default: "1001", required: true,
            description: 批次号 }
        - { name: vip_users, type: sql_result, datasource: mysql8,
            sql: "SELECT id FROM dim_users WHERE tier = 1",
            description: VIP 用户 ID 列表 }

  # ──── 数据对比（注入参数到 SQL）────────────────
  - id: compare
    type: compare
    depends_on: [params]
    config:
      task_id: orders-compare
      source_sql_override: |
        SELECT id, user_id, amount FROM orders
        WHERE dt = '${biz_date}'
          AND user_id IN (${vip_users | sql_in})
        ORDER BY id
      target_sql_override: |
        SELECT id, user_id, amount FROM orders_v2
        WHERE dt = '${biz_date}'
          AND user_id IN (${vip_users | sql_in})
        ORDER BY id

  # ──── 仅在 prod 环境且发现差异时才发告警 ────────
  - id: notify
    type: http
    depends_on: [compare]
    when: "${env} == 'prod' && ${nodes.compare.summary.diff} > 0"
    config:
      url: "https://hooks.example.com/alert"
      method: POST
      body: |
        {"text": "[${biz_date}] orders vs orders_v2 发现 ${nodes.compare.summary.diff} 行差异"}

  # ──── 导出 Excel 报告（文件名由引擎自动生成）─────
  - id: export
    type: excel_export
    depends_on: [compare]
    config:
      sheets:
        - { id: summary, sheet_name: "汇总", source: summary, max_rows: 10000 }
        - { id: diff,    sheet_name: "差异_${biz_date}", source: diff, max_rows: 100000 }
```

执行时引擎会按拓扑序：
1. `params` 解析 yesterday + SQL → 输出 `{biz_date, batch_id, vip_users}`，标量
   合并到 workflow 变量域
2. `compare` 用 override SQL（已替换 `${biz_date}` 和 `${vip_users | sql_in}`）跑对比
3. `notify` 检查 `when` —— 仅当 env=prod 且 diff > 0 才执行
4. `export` 用 `compare` 的输出生成 Excel
