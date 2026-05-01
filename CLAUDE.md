# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 常用命令

### 后端（Python / FastAPI）

```bash
# 本地运行（不用 Docker）
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8010 --reload

# 运行全部测试
pytest

# 运行单个测试文件
pytest tests/test_compare_engine.py

# 运行单个测试用例
pytest tests/test_compare_engine.py::test_identical_rows_go_to_same
```

### 前端（Vue 3 / Vite）

```bash
cd frontend/frontend

# 开发服务器（/api/* 代理到 http://app:8000，需后端运行中）
npm run dev

# 生产构建 — 输出到 ../../static/spa/
npm run build
```

### Docker（主要开发方式）

```bash
# 启动全部服务（MySQL 8 + app），代码有变动时自动重建
wsl -d Ubuntu-20.04 -- bash -c "cd /mnt/g/work/DataOpsStudio && docker compose up -d --build"

# 仅重启 app（仅前端构建后；Python 代码改动必须重建镜像，restart 不够）
wsl -d Ubuntu-20.04 -- bash -c "cd /mnt/g/work/DataOpsStudio && docker compose restart app"

# 修改 Python 代码后必须重建镜像才能生效（app 源码打入镜像，未 bind-mount）
wsl -d Ubuntu-20.04 -- bash -c "cd /mnt/g/work/DataOpsStudio && docker compose up -d --build"

# 临时验证（不重建镜像）：把改动 cp 进容器内跑测试
wsl -d Ubuntu-20.04 -- bash -c "cd /mnt/g/work/DataOpsStudio && docker cp app/. dataops-studio:/app/app/ && docker cp tests/. dataops-studio:/app/tests/ && docker exec dataops-studio pytest"

# 查看日志
wsl -d Ubuntu-20.04 -- docker logs dataops-studio -f
```

应用访问地址：**http://localhost:8010**。MySQL 8 暴露在 **localhost:3307**（容器名 `mysql8`，内部端口 3306）。

`docker-compose.yml` 只 bind-mount `config/results/logs/static`，**app 源码（`main.py`、`app/`、`tests/`）打入镜像**。所以：
- **前端**：构建产物落到 `./static/spa/`，restart app 即可生效（静态文件走 volume）。
- **后端**：改 Python 代码必须 `docker compose up -d --build`，仅 restart 会继续跑老代码，且容器里也不会有新增的测试文件。

## 架构说明

### 后端

`main.py` 初始化 FastAPI，挂载 `/static`，并注册来自 `app/api/routes.py` 的唯一路由器。所有 HTTP 端点都在这一个文件中。

**对比任务的数据流：**
1. `routes.py` → `runner.run_task(task_id)`（同步）或 `jobs.submit_task_run(task_id)`（异步后台线程）
2. `runner` 通过 `dbclients/factory.py` 查询数据 → `utils/sql_guard.py` 校验 SQL → 调用 `compare/engine.py`
3. `engine.compare_rows` 将行数据按 `key_columns` 归入 `only_source / only_target / diff / same` 四个桶
4. 结果以 JSON 和 Excel 写入 `results/`，并持久化到历史记录

**持久化** — 应用状态不依赖数据库，全部使用纯 JSON 文件：
- `config/datasources.json` — 数据源配置
- `config/tasks.json` — 对比任务配置
- `config/jobs.json` — 异步任务状态（重启后保留；运行中的任务重启后变为 `failed`）
- `results/` — 每次运行的 JSON + Excel 结果

`JsonStore`（`services/json_store.py`）是基于 mtime 缓存失效的线程安全泛型封装。`datasource_store` 和 `task_store` 均为 `services/repositories.py` 中的模块级单例。

**异步任务执行** — `services/jobs.py` 使用 `ThreadPoolExecutor(max_workers=2)`。任务支持取消，通过 `cancel_requested` 标志在 `runner.run_task` 各阶段检查。

**SQL 安全** — 用户提交的所有 SQL 在执行前都经过 `utils/sql_guard.py` 校验。只允许 `SELECT`/`WITH`，遇到 DML/DDL 关键字直接拒绝。

**DB 驱动** — `dbclients/drivers.py` 声明各 `DatabaseType` 对应的 Python 模块。`dbclients/factory.py` 在连接时动态导入第一个可用驱动。当前 `requirements.txt` 已启用的驱动：`pymysql` + `cryptography`（MySQL 8 `caching_sha2_password` 认证）。Oracle、DM、DB2 驱动为可选项。

### 前端

Vue 3 单页应用，主体为 `frontend/frontend/src/App.vue`（单一大组件，所有状态和逻辑均在此）。构建产物输出到 `static/spa/`，由 FastAPI 在 `/static/spa/` 路径下提供服务。

主要依赖：`@antv/g6`（血缘图）、`@codemirror/*`（SQL 编辑器）、`@vueuse/core`（工具函数，如 `useClipboard`）、Tailwind CSS v3（样式）。

Vite 开发服务器（`npm run dev`）将所有 API 调用代理到 `http://app:8000`，需要后端在 Docker 中运行。若仅本地启动后端，将代理目标改为 `http://localhost:8010`。

### 血缘分析

`app/lineage/analyzer.py` — 单脚本 SQL 血缘分析（基于 `sqlglot`）。
`app/lineage/batch_analyzer.py` — 多文件 ETL 血缘分析，支持 `.sql`/`.txt`/`.zip`。

两者均可接受可选的 Schema 元数据文件，用于解析 `SELECT *` 和未限定列名。

**方言路由** — `_resolve_dialect()` 把用户传入的方言名映射到 sqlglot 实际方言。当前支持：
- `mysql`、`oracle` — 直传
- `dm` / `dameng` → `oracle`（DM 与 Oracle 语法高度兼容）
- `ob_mysql` / `oceanbase_mysql` / `oceanbase` → `mysql`（OceanBase MySQL 模式）
- `ob_oracle` / `oceanbase_oracle` → `oracle`（OceanBase Oracle 模式）
- 未知方言原样下传给 sqlglot

**存储过程深度解析** — `_extract_procedure_segments()` 识别 `CREATE [OR REPLACE] PROCEDURE / FUNCTION / PACKAGE BODY / TRIGGER` 块，token 平衡 BEGIN/END，从过程体抽取 INSERT/UPDATE/MERGE/DELETE/CTAS/INSERT OVERWRITE/TRUNCATE。控制流壳子（IF/THEN、LOOP）会被跳过，DML 段保留 `procedure_name` 标签。当外层 sqlglot 整体解析失败（PL/SQL 控制流），分析器仍会基于过程体段产出血缘。

**动态 SQL** — 三种识别路径，置信度递减：
1. `EXECUTE IMMEDIATE 'literal'` / `sp_executesql 'literal'` — `confidence=high`
2. MySQL `SET @sql := '...'; PREPARE stmt FROM @sql; EXECUTE stmt;` — 跟踪同名变量，`prepare_var` 高置信
3. PL/SQL `v_sql := 'INSERT ' || p_table || ' SELECT...'; EXECUTE IMMEDIATE v_sql;` — 字面量段保留、变量段替换为 `:var` 占位符后送解析，`var_concat` 低置信
4. 静态长度 ≥20 的字符串字面量兜底 — `string_literal` 中置信
分支判断、循环、参数化拼接无法静态还原，按 `low_confidence` 标记 + warning 返回。

**临时表** — CTAS 含 `TEMPORARY` / `TEMP` / `GLOBAL TEMPORARY` 时 mapping 加 `is_temp=true`，`dml_type=CREATE_TEMP_TABLE_AS`。批量分析在"外部源表"和"最终产物"全局警告里把临时表过滤掉，避免把跨段中间产物误报成外部输入。

## 关键设计决策

- **流式对比模式**（`limits.stream_compare = true`）：不将全部行加载到内存，而是通过有序迭代器流式归并两侧数据。要求两边的 SQL 已按主键排序。
- **单 SQL 模式 vs 双 SQL 模式**：单 SQL 模式下，`source_sql` 在源端和目标端各执行一次；双 SQL 模式下，`source_sql` 和 `target_sql` 分别独立执行。
- **`CompareRules` 中的 `column_mappings`**：允许在对比前将源端和目标端不同名的列进行映射对齐。
- **测试数据**：`init_db/01_init.sql` 中的 `users`/`users_archive` 和 `orders`/`orders_v2` 表有意设计了差异，用于演示对比结果。

## 路线图

整体路径：**先稳住血缘 → 扩展数据输入类型 → 串成作业流 → 工程治理**。

### 阶段 1：收口现有血缘能力

目标：把已有血缘能力变稳定、可维护。

| 任务 | 内容 | 优先级 |
| --- | --- | --- |
| 收口 Git 状态 | 提交 `analyzer.py`、`batch_analyzer.py` 变更，确认分支干净 | P0 |
| Docker 内跑测试 | 跑后端 pytest、前端 build，确认无回归 | P0 |
| 拆分 `analyzer.py` | 拆出方言路由、存储过程解析、字段解析、图构建、风险提示模块 | P0 |
| 拆分 `App.vue` | 拆出单脚本分析、批量分析、schema 面板、风险面板、映射面板等子组件 | P0 |
| API 服务化 | 把 `routes.py` 的血缘编排抽到 `lineage_service.py` / `schema_service.py` | P1 |
| 补方言测试 | Oracle / DM / OceanBase / MySQL fixture 测试 | P1 |

### 阶段 2：完善数据比对能力

目标：从"SQL vs SQL"升级成"多来源数据比对"。

| 任务 | 内容 | 优先级 |
| --- | --- | --- |
| 抽象数据读取层 | 新增 `SqlReader`、`ExcelReader`，预留 `CsvReader` 扩展位 | P0 |
| 统一对比输入模型 | 左右两边统一为 `rows + columns + schema metadata` | P0 |
| Excel vs Excel | 支持 sheet、表头行、主键字段、字段选择 | P0 |
| Excel vs SQL | 上传 Excel 与数据库 SQL 结果对比 | P0 |
| 字段映射 | Excel 字段名与 SQL 字段名不一致时的映射 | P1 |
| 类型标准化 | 日期、数字、空值、字符串 trim、大小写规则 | P1 |
| 对比结果导出增强 | Excel 导出包含差异摘要、仅左、仅右、字段差异明细 | P1 |

### 阶段 3：轻量作业流

目标：把数据比对、血缘分析、Excel 导出串成可执行流程。**第一版只做步骤式编排，不做拖拽画布。**

| 任务 | 内容 | 优先级 |
| --- | --- | --- |
| 作业流模型 | 定义 `workflow / node / edge / variable / artifact` | P0 |
| 变量系统 | 支持 `${biz_date}`、`${schema}`、`${table}`、`${file}` 等变量 | P0 |
| DAG 执行器 | 节点依赖、拓扑执行、失败中断、状态记录 | P0 |
| 节点执行器 | 接入数据对比、血缘分析、Excel 导出、批量导出 | P0 |
| 执行历史 | 保存每次运行的输入变量、节点状态、日志、输出产物 | P1 |
| 条件节点 | 例如差异数 > 0 才导出 | P2 |
| 作业流前端 | 第一版步骤式编排页面 | P1 |

第一版支持的最小流程：

```
输入变量 → 数据对比 → 血缘分析 → Excel 导出 / 批量导出
```

### 阶段 4：产品化与工程治理

目标：让项目从"能用"变成"可长期维护、可交付"。

| 任务 | 内容 | 优先级 |
| --- | --- | --- |
| 配置安全清理 | 真实数据源配置不入库，仓库内保留 example 配置 | P0（**不要拖到最后**） |
| API 响应模型 | 为 lineage、compare、workflow 定义稳定 schema | P1 |
| 前端状态管理 | 视情况引入 Pinia 或 composable 分层 | P1 |
| 任务系统增强 | job TTL、取消、失败重试、结果落盘 | P1 |
| 大文件保护 | Excel 行数、SQL 结果行数、导出大小限制 | P1 |
| CI 检查 | 后端测试、前端 build、基础 lint | P1 |
| 用户文档 | 数据比对、血缘分析、作业流、schema 接入说明 | P2 |

### 推荐排期（8 周）

| 周期 | 重点 |
| --- | --- |
| 第 1 周 | 收口血缘代码、拆分 `analyzer.py`、补方言测试 |
| 第 2 周 | 拆分 `App.vue`、API 服务化、前后端回归 |
| 第 3 周 | 抽象数据读取层，完成 Excel vs Excel |
| 第 4 周 | 完成 Excel vs SQL、增强导出 |
| 第 5 周 | 作业流模型、变量系统、DAG 执行器骨架 |
| 第 6 周 | 接入对比/血缘/导出节点，完成第一版作业流页面 |
| 第 7 周 | 执行历史、产物管理、异常处理 |
| 第 8 周 | 配置安全清理、CI、文档、整体回归 |

### 当前最优先的 6 件事

1. 提交并验证当前血缘改动
2. 拆分 `analyzer.py`
3. 拆分 `App.vue`
4. 抽象统一数据读取层
5. 实现 Excel vs Excel / Excel vs SQL
6. 实现轻量作业流执行器

## 血缘图设计（LineageGraph.vue）

参考 DataHub / Dagster / dbt Explorer / Atlan 的可扩展模式，避免 dagre 在 50+ 节点时把图压成一列：

- **默认 focal + N 跳 BFS**：`focusMode = neighborhood`，`hopDepth = 1`；节点数 > 30 时 `autoFocalId` 自动取最高度数的非 combo 节点为聚焦点；用户点击任一节点会更新 `clickedFocalId`，搜索命中也作为聚焦候选。
- **Schema 折成 combo 节点**：`projectedBase` computed 把 `collapsedSchemas` 中的所有表投射成一个 `__combo:<schema>` 虚拟节点，跨 schema 的多条边聚合为单条粗边并加 `×N` 标签。点击 combo 节点切换展开。搜索命中折叠 schema 内的表会自动展开该 schema。
- **布局/间距**：`layoutDir`（LR/TB）+ `spacingPreset`（compact/normal/relaxed）写入 `localStorage` key `lineage-graph-prefs-v1`。
- **逃生通道**：`viewMode = 'graph' | 'table'`；表视图按上游/下游 BFS 分组，每行可点击重新聚焦。节点 > 100 时显示推荐切表横幅。
- **状态层级**：`allGraphData` → `filteredBase`（角色/边类型/可信度/脚本过滤）→ `projectedBase`（schema combo 投射）→ `graphData`（focal+hop BFS）。所有下游 computed 链在 `projectedBase` 之上。
