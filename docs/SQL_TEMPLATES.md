# SQL 模板库(SQL 工作台 v0.4)

把常用 SQL 沉淀成"模板"复用 — 跨 console、跨用户、跨数据源。一份内置 example
盘活 6 个常用查询(行数 / 重复主键 / 空值率 / 按日趋势 / 分组 Top N / EXPLAIN)。

## 数据模型

每个模板 11 个字段:

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | str | UUID hex(用户)或 `builtin:<slug>`(内置) |
| `name` | str | 必填,1-120 字符 |
| `description` | str | 一句话用途 / 注意事项 |
| `tags` | list[str] | 自由分组,UI 支持 AND 多 tag 过滤 |
| `db_types` | list[str] | `["mysql","oracle","dm","ob_mysql","ob_oracle","db2"]`,`["all"]` = 通用 |
| `project_id` | str | "" = 全局可见;具体 id = 仅该项目空间 |
| `risk_level` | `"low"\|"medium"\|"high"` | UI chip 颜色;**不**拦截执行 |
| `sql` | str | 必填,模板正文。**仅允许 SELECT/WITH**(同 sql_guard) |
| `created_by` | str | user_id;内置模板填 `"system"` |
| `created_at` / `updated_at` | ISO 8601 | 自动维护 |
| `builtin` | bool | True = 仓库内置,不可改 / 不可删 |

## 内置 example 机制

`config/sql_templates.example.json` 入仓库,跟运行时 `config/sql_templates.json`
union 显示:

- `list()` 时 example + 用户模板合并,按 `updated_at` desc 排序
- example 条目自动标 `builtin=true` + id 前缀 `builtin:`
- `update/delete` 对 builtin id 返 403 — 保证内置永远跟 git 最新,用户改不动
- 用户想基于内置改:UI 点「克隆」按钮,弹"保存为模板"modal 预填内容,另存为新模板

## API

### CRUD

```bash
# 列表(editor+,viewer 也能读)
GET /api/sql-templates?q=&tag=&db_type=&project_id=

# 详情
GET /api/sql-templates/{id}

# 新建(editor+)
POST /api/sql-templates
{
  "name": "大客户订单 Top 10",
  "description": "按订单金额排序",
  "tags": ["报表", "月度"],
  "db_types": ["mysql"],
  "risk_level": "low",
  "sql": "SELECT ... FROM orders ORDER BY amount DESC LIMIT 10"
}

# 全量更新
PUT /api/sql-templates/{id}
{...同 POST}

# 删除
DELETE /api/sql-templates/{id}
```

### 搜索/过滤

`GET /api/sql-templates` 接 4 个 query 参数:

- `q=foo`:子串 case-insensitive 匹配 name / description / sql 任一
- `tag=报表,月度`:**AND** 命中(必须同时含所有 tag)
- `db_type=mysql`:模板 db_types 含此值或 `"all"` 才通过("all" 算通用)
- `project_id=`:`null`=不过滤(默认);`""`=只看全局;`"X"`=看 X + 全局

### 导入 / 导出

```bash
# 导入
POST /api/sql-templates/import
{
  "templates": [{...}, {...}],
  "overwrite_by_name": false   # true = 同名覆盖,false = 跳过
}
# → {ok, created, skipped, errors}

# 导出
GET /api/sql-templates/export/json?include_builtin=false
# → {templates: [...], count, exported_at}
```

导入时:`id / builtin / created_at / updated_at / created_by` 字段被**忽略**,系统
重新派 — 防止用户通过 import payload 伪造 builtin=true 提权。

## 前端使用

### 从 Console 保存为模板

1. SQL 工作台编辑器写好 SQL
2. 点工具栏「📑 存为模板」按钮(`BookmarkPlus` 图标)
3. 弹出 modal 填:名称 / 描述 / 标签 / 数据库类型 / 风险等级
4. SQL 自动从当前 console 预填,直接保存即可

### 从模板插入到 Console

1. 切到底部「📑 模板」tab
2. 顶部搜索/标签/方言过滤
3. 点模板卡片右侧「插入」按钮
4. SQL 自动 **append** 到当前 console(不 replace,保护已有编辑)

### 内置模板"克隆"流程

1. 模板列表点内置模板的「克隆」按钮(铅笔图标)
2. modal 预填 name 加" (副本)"后缀
3. 改完保存,新模板归你所有,可自由再编辑

### 批量导入/导出

模板 tab 顶栏:

- **📤 导入**:选本地 `.json` 文件,若同名提示是否覆盖
  - 兼容两种格式:`{templates: [...]}` 或 直接 `[...]`
- **📥 导出**:把所有**用户模板**(不含 builtin)dump 成 JSON 下载
  - 文件名 `sql-templates-YYYY-MM-DD.json`

## risk_level 语义约定

`risk_level` 影响 UI chip 颜色,**不**自动拦截执行。真正的危险 SQL 拦截在
`utils/sql_guard.py` (DML/DDL 关键字一律拒)。这个字段是**给人看的提示**,
让团队成员看到这条查询大概的代价/风险水平:

| level | UI 颜色 | 用例 |
|---|---|---|
| `low` | 无 chip(默认) | 单表小查询、聚合统计、字段预览 |
| `medium` | 黄色"中风险" | 大表全扫、跨表 join、EXPLAIN |
| `high` | 红色"高风险" | 跨 schema 全表 join、可能引起慢 SQL |

## 权限矩阵

| 操作 | viewer | editor | admin |
|---|---|---|---|
| 列表 / 详情 | ✅ | ✅ | ✅ |
| 新建 / 编辑 / 删除 / 导入 / 导出 | ❌ 403 | ✅ | ✅ |
| 编辑/删除 builtin 模板 | ❌ 403 | ❌ 403 | ❌ 403 |

## 测试

后端:`tests/test_sql_workbench_templates.py` 25+ case 覆盖 store CRUD /
builtin 保护 / 过滤 / 导入合并语义 / 提权防御 / API 鉴权 / endpoint shape。

前端:`tests/stores/sqlTemplates.test.js` 9 case 覆盖 store 各动作 + filter
透传 + 失败保留旧 templates。
