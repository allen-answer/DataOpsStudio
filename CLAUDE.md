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
- `config/workflows.json` — 作业流配置（Phase 3）
- `config/jobs.json` — 异步任务状态（重启后保留；运行中的任务重启后变为 `failed`）
- `results/` — 每次运行的 JSON + Excel 结果

这四个 JSON 文件**不入库**（每个 clone 自己一份运行时状态）。仓库里保留 `config/datasources.example.json` 和 `config/tasks.example.json`。新克隆的环境首次启动前可选复制：

```bash
cp config/datasources.example.json config/datasources.json
cp config/tasks.example.json config/tasks.json
```

如果不复制，`JsonStore` 在首次写入时会自动创建空数组文件，应用照常启动，只是没有预置数据源/任务。`workflows.json` 没有 example 文件——空仓库下就是空作业流列表。

`JsonStore`（`services/json_store.py`）是基于 mtime 缓存失效的线程安全泛型封装。`datasource_store` 和 `task_store` 均为 `services/repositories.py` 中的模块级单例。

**异步任务执行** — `services/jobs.py` 使用 `ThreadPoolExecutor(max_workers=2)`。任务支持取消，通过 `cancel_requested` 标志在 `runner.run_task` 各阶段检查。

**SQL 安全** — 用户提交的所有 SQL 在执行前都经过 `utils/sql_guard.py` 校验。只允许 `SELECT`/`WITH`，遇到 DML/DDL 关键字直接拒绝。

**作业流（Phase 3）** — 参数驱动的多步骤数据对比作业：
- `services/workflow_engine.py` 提供 `run_workflow(workflow, variables, runners, cancel_check)`，按 `depends_on` 拓扑序执行
- 节点类型：`params` / `compare` / `lineage` / `http` / `excel_export`，注册在 `services/workflow_nodes.py` 的 `NODE_RUNNERS`。新节点类型 (1) 在 `models.WorkflowNodeType` 加值 (2) 写 `(config, variables) -> dict` runner (3) 注册到字典
- **变量与参数引用语法详见 `docs/PARAMETERS.md`**。要点：`${name}` 引用变量、`${nodes.X.Y}` 引用上游节点输出、`${var | sql_in}` 等过滤器把 list 渲染成 SQL IN 子句体；`params` 节点的标量输出自动合并回 workflow 变量域
- 单节点失败 → 下游 `SKIPPED`、旁路继续；`when:` 表达式可让节点条件性跳过
- `cancel_check` 在节点之间被轮询；`services/jobs.py` 的 `submit_workflow_run` 把它接到现有的 `_is_cancel_requested` 标志，所以 `/api/runs/{job_id}/cancel` 对作业流和对比任务一视同仁
- WorkflowRun 落盘到 `results/workflow_runs/<run_id>.json`，由 `services/workflow_history.py` 管理

**DB 驱动** — `dbclients/drivers.py` 声明各 `DatabaseType` 对应的 Python 模块。`dbclients/factory.py` 在连接时动态导入第一个可用驱动。当前 `requirements.txt` 已启用的驱动：`pymysql` + `cryptography`（MySQL 8 `caching_sha2_password` 认证）。Oracle、DM、DB2 驱动为可选项。

### 前端

Vue 3 单页应用。`App.vue` 持有所有共享状态和后端调用，子视图（`views/*.vue`）通过 `provide('app', {...})` / `inject('app')` 拿到 reactive 引用——故意没引 Pinia，状态分层是 Phase 4 决策，看用量再决定。当前视图：`DatasourceView` / `WorkbenchView`（对比任务）/ `WorkflowView`（作业流）/ `LineageView` / `BatchView` / `HistoryView`。构建产物输出到 `static/spa/`，由 FastAPI 在 `/static/spa/` 路径下提供服务。

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

整体路径：**血缘稳定 → 多来源对比 → 作业流 → 工程治理**。

### 已完成

- **Phase 1（血缘）**：`analyzer.py` / `batch_analyzer.py` 拆分、方言路由、存储过程深度解析、动态 SQL 识别、`App.vue` 子组件拆分、API 服务化（`lineage_service.py` / `schema_service.py`）、方言测试 fixture
- **Phase 2（多来源对比）**：`SqlReader` / `ExcelReader` 抽象层、Excel vs Excel、Excel vs SQL、字段映射 + 类型标准化（日期/数字/Decimal/空值/trim/大小写）、字段筛选 UI（按列勾选 include/exclude）、对比结果导出增强
- **Phase 3 已交付的部分**：作业流模型 + 变量插值（`${var}`）+ 顺序执行引擎 + `compare` 节点 + HTTP API（CRUD/sync/async）+ 取消支持 + 前端编排页面
- **Phase 4 已交付的部分**：配置安全清理（`config/*.json` 不入库）+ CI（GitHub Actions: pytest + frontend build）

### Phase 3 还没做

- **新节点类型**：`lineage` / `excel_export` / `batch_lineage` 等（注册表已就位）
- **真 DAG**：`depends_on` 字段 + 拓扑排序 + 并行执行
- **执行历史持久化**：`WorkflowRun` 当前不落盘，复用 `jobs.json` 但记录残缺
- **条件节点**：`when: ${diff_count} > 0` 之类的跳过判断

### Phase 4 还没做

- API 响应模型（lineage / compare / workflow Pydantic schemas）
- 前端状态管理（视情况引入 Pinia）
- 任务系统增强（job TTL、失败重试）
- 大文件保护（Excel 行数 / SQL 结果 / 导出大小上限）
- 用户文档

## 血缘图设计（LineageGraph.vue）

参考 DataHub / Dagster / dbt Explorer / Atlan 的可扩展模式，避免 dagre 在 50+ 节点时把图压成一列：

- **默认 focal + N 跳 BFS**：`focusMode = neighborhood`，`hopDepth = 1`；节点数 > 30 时 `autoFocalId` 自动取最高度数的非 combo 节点为聚焦点；用户点击任一节点会更新 `clickedFocalId`，搜索命中也作为聚焦候选。
- **Schema 折成 combo 节点**：`projectedBase` computed 把 `collapsedSchemas` 中的所有表投射成一个 `__combo:<schema>` 虚拟节点，跨 schema 的多条边聚合为单条粗边并加 `×N` 标签。点击 combo 节点切换展开。搜索命中折叠 schema 内的表会自动展开该 schema。
- **布局/间距**：`layoutDir`（LR/TB）+ `spacingPreset`（compact/normal/relaxed）写入 `localStorage` key `lineage-graph-prefs-v1`。
- **逃生通道**：`viewMode = 'graph' | 'table'`；表视图按上游/下游 BFS 分组，每行可点击重新聚焦。节点 > 100 时显示推荐切表横幅。
- **状态层级**：`allGraphData` → `filteredBase`（角色/边类型/可信度/脚本过滤）→ `projectedBase`（schema combo 投射）→ `graphData`（focal+hop BFS）。所有下游 computed 链在 `projectedBase` 之上。
