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

**主要验收以 Docker / WSL 构建为准。** Windows PowerShell 直接跑 `npm run build` 偶发
`Vite/Rolldown spawn EPERM`（Rolldown 的子进程启动被 Windows Defender / 文件锁拦），
属环境问题；遇到时切换到 WSL 跑构建：

```bash
wsl -d Ubuntu-20.04 -- bash -c "cd /mnt/g/work/DataOpsStudio/frontend/frontend && npm run build"
```

Docker 镜像构建链路在 `Dockerfile` 的 `frontend` 阶段（`node:20-alpine`），不受
Windows EPERM 影响。CI 也走 Linux runner。

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

`docker-compose.yml` 只 bind-mount `config/results/logs`，**app 源码（`main.py`、`app/`、`tests/`）和前端构建产物（`static/spa/`）都打入镜像**。所以：
- **前端**：构建产物 `static/spa/index.html` + `assets/` 由 Dockerfile 多阶段构建生成，**改前端代码必须 `docker compose up -d --build`**，仅 restart 不够。本地 dev 模式：`cd frontend/frontend && npm run dev`，vite proxy 到 `app:8000`。
- **后端**：改 Python 代码也必须 `docker compose up -d --build`，仅 restart 会继续跑老代码，且容器里也不会有新增的测试文件。

## 架构说明

### 后端

`main.py` 初始化 FastAPI，挂载 `/static`，并注册来自 `app/api/routes.py` 的唯一路由器。`routes.py` 现在是聚合器（39 行），实际 endpoint 拆在 10 个领域子模块：`system` / `datasources` / `tasks` / `runs` / `workflows` / `workflow_runs` / `history` / `lineage` / `uploads` / `config_io`。新增 endpoint 加到对应子模块；不属于任何领域时新建子模块再 include 进 `routes.py`。

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

**作业流** — 参数驱动的多步骤数据对比作业：
- `services/workflow_engine.py` 提供 `run_workflow(workflow, variables, runners, cancel_check, resume_from, from_node_id)`，按 `depends_on` 拓扑序执行
- 节点类型：`params` / `compare` / `lineage` / `http` / `excel_export`，runner 拆在 `app/workflow/nodes/<type>.py`，集中注册在 `app/workflow/registry.NODE_RUNNERS`。`app/services/workflow_nodes.py` 是向后兼容 shim。新节点类型 (1) 在 `models.workflow.WorkflowNodeType` 加值 (2) `app/workflow/nodes/<type>.py` 写 `(config, variables, **_) -> dict` runner (3) 在 registry 注册
- **变量与参数引用语法详见 `docs/PARAMETERS.md`**。要点：`${name}` 引用变量、`${nodes.X.Y}` 引用上游节点输出、`${var | sql_in}` 等过滤器把 list 渲染成 SQL IN 子句体；`params` 节点的标量输出自动合并回 workflow 变量域
- 单节点失败 → 下游 `SKIPPED`、旁路继续；`when:` 表达式可让节点条件性跳过
- **局部重跑**：`POST /api/workflow-runs/{run_id}/rerun` 指定 `from_node_id`，上游沿用上次 output（`reused=true`），自身和下游重跑。所有祖先必须上次 SUCCESS 否则拒。
- **Artifact 模型**：节点产出文件统一通过 `output.artifacts: list[Artifact]` 声明，`WorkflowRun.artifacts` computed 顶层聚合。前端 `/results/<relative_path>` 下载。删 run 连带 rmtree `results/workflow_runs/<run_id>/` 整目录
- `cancel_check` 在节点之间被轮询；`services/jobs.py` 的 `submit_workflow_run` 把它接到现有的 `_is_cancel_requested` 标志，所以 `/api/runs/{job_id}/cancel` 对作业流和对比任务一视同仁
- WorkflowRun 落盘到 `results/workflow_runs/<run_id>.json`，由 `services/workflow_history.py` 管理

**DB 驱动** — `dbclients/drivers.py` 声明各 `DatabaseType` 对应的 Python 模块。`dbclients/factory.py` 在连接时动态导入第一个可用驱动。当前 `requirements.txt` 已启用的驱动：`pymysql` + `cryptography`（MySQL 8 `caching_sha2_password` 认证）。Oracle、DM、DB2 驱动为可选项。

### 前端

Vue 3 单页应用。`App.vue` 持有所有共享状态和后端调用，子视图（`views/*.vue`）通过 `provide('app', {...})` / `inject('app')` 拿到 reactive 引用——故意没引 Pinia，状态分层是后续决策，看用量再决定。当前视图：`DatasourceView` / `WorkbenchView`（对比任务）/ `WorkflowView`（作业流）/ `LineageView` / `BatchView` / `HistoryView`。

作业流相关视图按职责拆到 `components/workflow/`，DetailView / RunView 主文件只剩布局壳：

- **节点类型编辑器**（DetailView 节点配置 tab v-if 分发）：`WorkflowParamsNodeEditor` / `WorkflowCompareNodeEditor` / `WorkflowLineageNodeEditor` / `WorkflowExcelExportNodeEditor`。新增节点类型时加同名组件即可。
- **WorkflowDagCanvas**（DetailView 主区域 DAG 画布）：SVG 节点 + 自动布局 + hover tooltip + 状态叠加。props: nodes / latestRun / v-model:selectedNodeId。
- **WorkflowSettingsPanel**（DetailView 右侧元数据 sidebar）：参数预览 + 描述/项目/状态/owner/cron/tags + 输入/输出资产编辑器。
- **WorkflowHistoryPanel**（DetailView 运行历史 tab）：行展开 + mini gantt + 状态徽章 + 复用变量重跑。
- **WorkflowRunNodeDetail**（RunView 右侧节点详情面板）：节点头 + 错误块 + artifact 下载 + 5 种 type 输出（compare/excel_export/params/http/lineage）+ 折叠原始 JSON + 事件流。emits: rerun-from-node / rerun-defaults。

LineageView 在 G6 拓扑图之外另挂 **`SemanticLineagePanel`**：消费后端 `result.semantic_lineage` 字段，渲染 observations（bullet）/ risks（高/中/低着色）/ targets 表（多 role pill + refresh_mode pill + 操作计数 + titles）/ procedures（卡片）。规则分析驱动，不依赖 AI。

前端独有的 Figma → Vue 设计系统规则在 `frontend/frontend/CLAUDE.md`（Figma MCP 输出 React+Tailwind 参考代码 → 适配本仓库 Vue 3 SFC + 现有 `.btn`/`.card`/`.pill` 短类）。

构建产物输出到 `static/spa/`，由 FastAPI 在 `/static/spa/` 路径下提供服务。`static/spa/index.html` 和 `static/spa/assets/` 已 gitignore，由 CI / Docker / release 脚本生成；手写资源 `static/spa/favicon.svg` 等仍跟踪。`/spa` endpoint 加 `Cache-Control: no-cache`，避免 index.html 被浏览器缓存住引用旧 hash 的 bundle。

主要依赖：`@antv/g6`（血缘图）、`@codemirror/*`（SQL 编辑器）、`@vueuse/core`（工具函数，如 `useClipboard`）、Tailwind CSS v3（样式）。

Vite 开发服务器（`npm run dev`）将所有 API 调用代理到 `http://app:8000`，需要后端在 Docker 中运行。若仅本地启动后端，将代理目标改为 `http://localhost:8010`。

### 血缘分析

`app/lineage/` 是按职责拆出的多模块包（基于 `sqlglot`）：

- `analyzer.py`（157 行）— 单脚本入口，编排其他模块产出结果
- `batch_analyzer.py`（498 行）— 多文件 ETL 血缘，支持 `.sql`/`.txt`/`.zip`
- `segments.py`（415 行）— 存储过程分段抽取（`CREATE PROCEDURE/FUNCTION/PACKAGE BODY/TRIGGER` 内 BEGIN/END token 平衡，PL/SQL 控制流壳子跳过）
- `columns.py`（302 行）— 字段级 lineage 抽取
- `dml.py`（300 行）— DML 语句解析（INSERT/UPDATE/MERGE/DELETE/CTAS/INSERT OVERWRITE/TRUNCATE）
- `tables.py`（161 行）— 表引用归一化
- `helpers.py`（139 行）— 公共辅助
- `graph.py`（105 行）— 图结构装配
- `aggregation.py` — 按目标表聚合 INSERT/UPDATE/MERGE/DELETE/TRUNCATE，识别 `delete_insert` / `truncate_insert` 全量重刷模式（输出 `target_summary`）
- `roles.py` — 给脚本里所有出现的表打 role 标签：`target` / `intermediate` / `source_fact` / `remote_dblink`（结构角色）+ `config` / `reference` / `dimension` / `filter`（命名角色）。一张表可挂多个 role，`primary_role` 按展示优先级取一个。输出 `table_roles`
- `semantic.py` — 把 `target_summary` / `table_roles` / `procedure_segments` / `parse_errors` / `dynamic_sql_segments` 收口成一个 `semantic_lineage` 字段：`procedures` / `targets`（合并 role + refresh + titles + counts）/ `business_groups`（占位）/ `grouped_edges`（占位）/ `observations`（人类可读派生）/ `risks`（level/type/message 三元组）
- `_common.py` / `clauses.py` / `dialects.py` / `variables.py` / `warnings.py` — 方言映射、子句拆解、变量跟踪、warning 收集等小模块

入口和批量分析都接受可选 Schema 元数据文件，用于解析 `SELECT *` 和未限定列名。

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

整体路径：**血缘稳定 → 多来源对比 → 作业流 → 工程治理 → 血缘语义增强（双轨）**。

### 已完成

- **Phase 1（血缘）**：`analyzer.py` / `batch_analyzer.py` 拆分、方言路由、存储过程深度解析、动态 SQL 识别、`App.vue` 子组件拆分、API 服务化（`lineage_service.py` / `schema_service.py`）、方言测试 fixture
- **Phase 2（多来源对比）**：`SqlReader` / `ExcelReader` 抽象层、Excel vs Excel、Excel vs SQL、字段映射 + 类型标准化（日期/数字/Decimal/空值/trim/大小写）、字段筛选 UI（按列勾选 include/exclude）、对比结果导出增强
- **Phase 3（作业流）**：模型 + 变量插值（`${var}` / `${nodes.X.Y}` / 过滤器）+ DAG 拓扑序执行引擎 + 5 种节点类型（params / compare / lineage / http / excel_export）+ HTTP API（CRUD/sync/async/cancel/局部重跑 from_node）+ `when:` 条件节点 + WorkflowRun 落盘 + Artifact 模型 + 删 run 连带清目录
- **Phase 4（工程治理）**：
  - 配置安全（`config/*.json` 不入库 + datasource 密码 API 脱敏 + 导出可选含密码 + 日志脱敏 + JsonStore 落盘 0600）
  - 模块拆分（后端）：`routes.py` 631 行 → 10 个 `app/api/<domain>.py`；`models.py` 540 行 → `app/models/` 5 个子模块；`workflow_nodes.py` 536 行 → `app/workflow/nodes/` 5 个 + `registry.py`；`analyzer.py` 956 行 → 12 个 `app/lineage/<aspect>.py` 职责模块
  - 模块拆分（前端）：`WorkflowDetailView.vue` 841 → 362 行（抽 4 个组件：HistoryPanel / DagCanvas / SettingsPanel + 4 个节点编辑器）；`WorkflowRunView.vue` 628 → 309 行（抽 RunNodeDetail）
  - response_model 全收口：所有 endpoint 挂 Pydantic schema（OpenAPI /docs 给前端 / 第三方一份准确契约）
  - CI（GitHub Actions: pytest + frontend build + compileall + Docker build smoke + tag-触发的 Windows release）
  - 前端构建产物出库（`static/spa/index.html` + `assets/` 由 Dockerfile 多阶段 / Windows release 脚本生成）
- **Phase 6（测试）**：285 个 unit + HTTP 集成测试（FastAPI TestClient，`tests/test_api_integration.py`，覆盖 CRUD / 异步执行 / artifact 下载 / mimetype 回归 / 密码脱敏）+ 浏览器 e2e 框架（`tests/e2e/`，独立 `Dockerfile` + `requirements-e2e.txt`，跑法 `docker compose --profile e2e run --rm e2e`，catch render-time throw 那种 bug）

### Phase 7（血缘语义增强 · 双轨方案 · 未排期）

**核心原则**：离线规则分析必须能独立工作；AI 只提高"可读性、业务归纳、风险解释"，不能替代解析器；同一个 SQL 在无 AI 环境下也必须能输出可用的语义血缘。

#### 轨道 A：AI 可插拔增强（可选启用）

目标：用户有 AI 能力时，提升语义理解和展示质量。

- **Provider 抽象**：不绑定具体厂商，统一 `LineageAIProvider` 接口。支持 OpenAI / Azure OpenAI / 私有大模型 / Ollama / 本地模型。
- **默认关闭**：通过配置（`config/lineage_ai.json` 或 env）启用。
- **AI 只做语义增强，不作为血缘事实来源**。AI 输入必须基于系统确定性解析结果 + SQL 注释 + 表名/字段名；AI 输出落到 `ai_enrichment` 字段，不覆盖原始 lineage。
- **每条 AI 结论必须有** `confidence` / `reason` / `evidence` 三元组。
- **AI 异常不能影响普通血缘分析**——provider 调用失败时静默降级，规则结果照常输出。
- **前端明确标识"AI 辅助判断"**——徽章 + 颜色区分规则结论与 AI 结论。

#### 轨道 B：离线确定性分析增强（无 AI 也能用）

目标：没有 AI 的环境下，也能明显提升 SQL / 存储过程分析准确性。

1. **存储过程分段解析** ✅ —— `app/lineage/segments.py`：BEGIN/END token 平衡 + 控制流壳子（IF/THEN/CASE/LOOP）跳过 + `case ... end` 误算 block-end 的 bug 修过。每段 `procedure_segments` 记录含 `procedure_name` / `procedure_kind` / `segment_index` / `sql` / `confidence` / `line_start` / `line_end` / `preceding_comment` / `parse_status`：
   - `line_start` / `line_end`：原始 SQL 文件中的 1-based 行号；`line_start` 指 DML 关键字位置（前置注释不计入行号起点，但保留在 `preceding_comment`）
   - `preceding_comment`：DML 前的纯注释块（`--` 行注释 / `/* */` 块注释都支持，多段空格连接，截断 200 字符）。配合第 6 项（注释利用），让 `-- 集中交易` 这类业务标题既挂到 sqlglot statement.comments 又留在段元数据里
   - `parse_status`：`parsed`（sqlglot 单段解析成功）/ `unsupported`（解析失败，前端高亮提示）/ `unknown`（默认）
   - `semantic_lineage.procedures[i].steps`：暴露每段 step（`segment_index` / `dml_keyword` / `line_start` / `line_end` / `preceding_comment` / `parse_status`）+ 进程级 `unsupported_count` 计数；前端 `SemanticLineagePanel.vue` 折叠展开成 step 表，按 DML 关键字 + 解析状态着色

   `SELECT INTO` / `EXECUTE IMMEDIATE` 风险提示属于第 3 项 Oracle 方言增强范围，未做。
2. **DML 聚合** ✅ —— `app/lineage/aggregation.py`：按目标表聚合 INSERT/UPDATE/MERGE/DELETE/TRUNCATE 计数，识别 `truncate_insert` / `delete_insert`（DELETE 无 WHERE）/ `delete_insert_partial` / `merge` / `update` / `append` / `mixed` 等 `refresh_mode`。输出在 `analyze_sql_lineage()` 顶层 `target_summary` 字段，包含 `target_table` / `insert_count` / `update_count` / `merge_count` / `delete_count` / `truncate_count` / `delete_before_insert` / `truncate_before_insert` / `refresh_mode`。
3. **Oracle 方言增强** ✅ —— 已落地：
   - `/*+ parallel|use_hash|leading|... */` hint 不会被误识别成业务标题（`_looks_like_oracle_hint` 70+ 关键字白名单 + ASCII 头部正则）
   - `table@dblink` DB Link 表保留 `@后缀` 进 graph_edges + `role=remote_dblink`
   - `SELECT col INTO v_x FROM t` 让 sqlglot 报 unsupported syntax 时仍能在 procedure 段切分阶段抓到 SELECT 的源表
   - `PACKAGE BODY` 含 `CONSTANT := SYSDATE` 类常量赋值不影响 INSERT 抽取
   - `FOR rec IN (SELECT ...) LOOP <DML>` cursor 头部被 `_strip_cursor_for_prefix` 剥掉，避免 cursor 子查询的 SELECT 被误认作顶层 DML 而吞掉真正的 INSERT
   - `EXECUTE IMMEDIATE p_var` 但 `p_var` 来自参数 / 包变量 / cursor 静态无法推断时，输出 `confidence='unresolved'` 占位段触发"动态 SQL"warning（不再默默吞掉）
   - 测试：`tests/test_oracle_dialect.py` 8 个回归 case
4. **表角色识别** ✅ —— `app/lineage/roles.py`：结构角色（`target` / `intermediate` / `source_fact` / `remote_dblink`）+ 命名角色（`config` / `reference` / `dimension` / `filter`）。schema 段（`dim.cust` / `ref.code` / `config.t_config` / `filter.exclude_*`）和 basename（`dim_user` / `code_status` / `t_config` / `exclude_cust`）都会扫一遍，多 role 共存时 `primary_role` 按 `remote_dblink > intermediate > target > config > reference > dimension > filter > source_fact` 取一个。Oracle DB Link 表（`tab@dblink`）单独识别。输出在 `analyze_sql_lineage()` 顶层 `table_roles` 字段。
5. **业务分组规则** ✅ —— `app/lineage/grouping.py` + `config/lineage_group_rules.yml`（拷 `lineage_group_rules.example.yml` 改造，文件不存在静默降级为不分组，文件坏掉只 log 不拖崩主流程）。每个 group 挂多个 matcher，命中任一即归组，一张表可同时归多组。支持的 matcher：`schema_prefix/exact/contains` / `basename_prefix/suffix/exact/contains/regex` / `title_keyword`（匹配 target_summary[*].titles）。Oracle DB Link `tab@dblink` 在拆 schema/basename 时先剥掉 `@dblink`。规则按 mtime 缓存避免每次重新解析。输出在 `semantic_lineage.business_groups`（含 name / description / tables / table_count / target_count）和 `grouped_edges`（跨分组依赖，同组内边 / 任一端未分组的边都跳过；表归多组时一条边按组数横向展开）。pyyaml 是新增依赖；JSON 格式（`lineage_group_rules.json`）作为不装 pyyaml 时的回退。
6. **注释利用** ✅ —— `aggregation.extract_statement_title()` 从 sqlglot 节点的 `.comments` 列表里取第一段非空文本作为业务标题（截断 200 字符）。`result["statements"][i].title` 是单语句标题；`target_summary[i].titles` 是这张目标表所有写操作的标题（去重保序）。TRUNCATE 也覆盖（虽然不进 `analyses` 列表，但走 `collect_target_operations` 直扫拿 title）。后续业务分组（第 5 项）可以基于 titles 关键词匹配。
7. **语义血缘结构** `semantic_lineage` ✅ —— `app/lineage/semantic.py` 把第 2/4/5/6 项的产出 + procedure_segments + parse_errors + dynamic_sql_segments 一次性聚合到 `result["semantic_lineage"]`，前端 / 第三方一处获取语义视图。`procedures`（按 name 折叠 segment_count）/ `targets`（合并 role + refresh + titles + counts）/ `business_groups`（消费第 5 项规则）/ `grouped_edges`（跨分组依赖计数）/ `observations`（"X 张目标表，其中 Y 张全量重刷……"）/ `risks`（parse_error → high；动态 SQL var_concat → medium；动态 SQL string_literal → low）。
8. **前端两个视图**——原始血缘图（详细表/字段关系，已存在）+ 语义血缘图（业务分组 → 目标中间表 → 下游消费）。**当前进度** ✅：`SemanticLineagePanel.vue` 显示 observations / risks / 业务分组（卡片 + DAG 双视图，顶部 toggle 切换）/ 目标表（含 role + refresh + titles + counts）/ procedures（折叠展开成 step 表：行号 + DML 关键字着色 + 业务标题 + 解析状态徽章）。
   - **DAG 视图**：`components/BusinessGroupDag.vue` 用 G6 dagre 渲染，每个 group 是圆节点（size 按 table_count 对数 28~56），跨分组依赖是边（粗细按 edge_count 对数 1~6 + `×N` 标签）。点击节点 emit `focus-group` 事件 + 右侧 sidebar 显 description / table_count / target_count / 包含表前 8 张。`defineAsyncComponent` 懒加载，复用 g6-vendor chunk，不再拉新 vendor。
   - **卡片视图**：跨分组依赖 pill 列表保留（仅 cards mode 显示，避免和 DAG 重复）。
   - 把 a_cispnew_f3045.sql 配 example yaml 跑出来：7 个分组（集中交易 / 融资融券 / 期权 / 机构客户 / 持仓市值 / 配置 / 过滤排除），跨分组依赖 7 条（融资融券→集中交易 ×78、机构客户→集中交易 ×81、机构客户→持仓市值 ×26 等）。

#### Phase 7 真实 Oracle 文件回归（已修关键 bug）

测试用大型 Oracle 存储过程 `a_cispnew_f3045.sql`（GBK，4421 行，88 INSERT t_etl_jy + 39 INSERT t_etl_zqsz + 各 1 DELETE）暴露并修掉的 bug：
1. **过程体段切分把换行压扁**：`clean_dynamic_segment` 用于 procedure 段时把所有空白压成单个空格，`-- 行注释` 没换行终止，吞掉后面所有列名 → sqlglot 括号失配 → 88 INSERT 折成 1。修：新增 `clean_procedure_segment` 保留换行，dedupe key 用空白规范化版本。
2. **CASE...END 让外层 BEGIN/END 计数错乱**：`_RE_BLOCK_TOKEN` 把 `case ... end` 的 end 也算 block 结束，外层 procedure body 提前在 t_etl_zqsz 区域被截断 → 漏 32 段 INSERT。修：新增 `_is_block_end()` 仅匹配 `END;` / `END identifier;`（identifier 不属于 IF/LOOP/CASE/RECORD/FOR/WHILE/XMLELEMENT），外层和内层 token 计数都用它。
3. **324 段假动态 SQL**：`extract_dynamic_sql_segments` 的 `string_literal` 兜底匹配任何 20+ 字符字面量，中文注释片段、错误信息、报表 header 等全中招。修：删除兜底，动态 SQL 只走 EXECUTE / PREPARE / EXECUTE IMMEDIATE 变量拼接三条精确路径。
4. **Oracle hint 误为业务标题**：sqlglot 把 `/*+ parallel(...) */` 也塞进 `.comments`（去掉 `+`），看起来跟普通注释一样。修：`aggregation._looks_like_oracle_hint()` 用 Oracle hint 关键字白名单（parallel/leading/index/use_hash 等 70+ 个）跳过。
5. **过程体段把前置注释剥掉**：`_iter_procedure_body_segments` 用 `_RE_BODY_DML.search(seg)` 后取 `seg[match.start():]`，把 `-- 集中交易` 一类业务标题前缀丢了。修：仅当前缀是纯空白+注释时保留整段（让 sqlglot 把注释挂到节点 `.comments`）。
6. **全角标点 / 模板变量让 sqlglot 报 `Expected END after CASE`**：中文输入法 / ETL 工具产出的 SQL 经常混入 `（` U+FF08、`）` U+FF09、`，` U+FF0C、`；` U+FF1B 全角符号；ETL 模板变量 `${data_dt1}` 也吃不下。修：新增 `app/lineage/preprocess.py`，状态机扫 SQL 把 code 段（不含字符串字面量 / 行注释 / 块注释）的全角标点替换成半角，`${name}` 替换成 `:name`（sqlglot bind param）。`script_variables` 在 normalize 之前抽，保留原始模板变量名进 `result["variables"]`。归一化保持换行数和位置不变，所以 `procedure_segments.line_start` 不偏。

回归 fixture 在 `tests/test_lineage_analyzer.py::test_oracle_proc_fixture_*` —— 不上传真实业务文件（避免泄露内部表名），改用合成 Oracle 过程，复刻所有上述模式。

### Phase 7 长期参考的设计方向

按用户调研，以下五个项目代表了 SQL 血缘 / 数据治理的成熟思路。本仓库已在 Track B 走 Dataedo 方向（过程分 step，不支持的明确标记不误报），后续如果要深做，再分别参考：

- **Dataedo**（PL/SQL 拆 step + step-level lineage + 不支持的 step 标 `parse_status=unsupported`）—— 我们已经在 procedure_segments 里记 `procedure_name` / `segment_index`，下一步加 `line_start` / `line_end` / `parse_status` / `preceding_comment` 让 step 模型完整。
- **Gudu SQLFlow**（先产中间血缘模型再画图：`objects` / `columns` / `relations` / `process_steps` / `target_summary`）—— 我们的 `semantic_lineage` 走的是这个方向。前端不应该直接从 SQL 画图，先消费中间模型。
- **sqlglot**（AST + 方言适配 + column-level lineage API）—— 已经在用，继续做底座。Oracle hint / DBLink / SELECT INTO / 包变量 / 动态 SQL 各自加一层处理（不要塞 sqlglot 自己改）。
- **DataHub**（schema-aware 字段级血缘 / 解析失败时降级）—— 字段级血缘必须 schema-aware，无 schema 时表级 high / 字段级 medium-low；未限定字段和 SELECT * 必须有风险标记，不能装作 100% 准确。
- **OpenLineage**（job / run / dataset / facet 四元模型）—— 长期把 workflow run 输出成 OpenLineage event，和现有作业流模块对接。

**DM 达梦**：没有直接可参考的开源血缘项目，路线是 `dialect=dm` 内部继承 oracle，再补 DM 特有语法、函数、系统表、分页写法。第一阶段目标：DM 的表级血缘 + DML 聚合 + DELETE+INSERT 识别准确，字段级精细血缘留到第二阶段。**别追求一步到位字段级 100% 准确**——先把 step 拆分、DML 计数、refresh 模式、动态 SQL 误报修准，可信度立刻上一个台阶。

### 还可以做（未排期）

- ~~前端状态管理（视情况引入 Pinia）~~ 渐进引入中：8 个 store 已上 —— `notice / datasource / task / workflow / lineage / batch / history / bootstrap`。`App.vue` 顶部 `useXxxStore() + storeToRefs`，`provide('app')` 仍 backward compat 把 store 字段平铺给 inject('app')。`useBootstrapStore.state` 是全局列表（datasources/tasks/workflows/drivers/dbTypes/history/historySheets）单一数据源。handlers（saveTask / runTask / saveWorkflow / loadBootstrap 业务联动 等）仍在 App.vue —— 它们跨 store 协调（默认 datasource / task selection / fillDraft 联动），后续可继续迁。
- 任务系统增强（job TTL、失败重试）
- ~~调度器 cron + 通知（企业微信 / 邮件 / Webhook）~~ ✅（codex 分支 `codex/lineage-scheduler-openlineage`，待 merge）—— `app/services/scheduler.py` 接 APScheduler `BackgroundScheduler`：每个 `status=active` 且 `schedule_cron` 非空的 workflow 自动注册 `CronTrigger`，定时 sync 任务重读 `workflow_store` 增 / 删 / 改 cron，APScheduler 不在环境时回落到原 polling loop（老 image / 离线测试可继续跑）。`app/services/notifier.py` 三类 channel 已上：`webhook` / `wecom` / `email`（SMTP），workflow run finish 自动 dispatch。**仍未做**：sensor / 事件型触发器（数据到达、上游 success 等）。
- ~~OpenLineage webhook emitter~~ ✅（codex 分支同上，待 merge）—— `app/services/openlineage_emitter.py:emit_workflow_run_openlineage` 把每次 workflow run 转成 OpenLineage `START` / `COMPLETE` event 推到外部 collector（webhook target list + 自定义 facet `dataops-v1`）。emitter best-effort：外部失败只 log 不抛，不阻塞 workflow 主路径。`app/services/jobs.py` + `app/api/workflows.py` 已挂入。**剩余**：把现有 OpenLineage event 也转成 dataset facet 并推到 DataHub / Marquez（长期方向，line 266）。
- 多项目空间 + 用户权限 + 审计日志
- ~~数据源连接池~~ ✅ —— `app/dbclients/pool.py`：每 datasource_id 一个 LIFO 池，`max_size` 默认 4，`idle_seconds` 默认 600（连接空闲超时丢弃）。`fetch_rows` / `fetch_columns` / `iter_rows` 改走 `pool.borrow(source, factory)` context manager；MySQL 路径 acquire 时 `SELECT 1` ping 验活，失败弃池重建。`extra.disable_pool=true` / 改 host/port 等关键字段时池自动失效（`_datasource_fingerprint`）。datasource API `update` / `delete` 同步调 `pool.invalidate(id)`。新增 7 个 test case 覆盖复用 / TTL 过期 / ping 失败 / 异常 broken / fingerprint change / disable_pool。
- ~~CSV / Parquet 数据对比~~ ✅ —— `app/readers/csv_reader.py`（stdlib csv，UTF-8/GBK 编码 + 自定义 delimiter + header_row 跳元数据）+ `app/readers/parquet_reader.py`（pyarrow 后端，支持 columns subset）。`SourceKind` 加 `csv / parquet`，`CompareTask` 加 `source_file_path / source_file_encoding / source_csv_delimiter`（CSV 用）通用文件路径字段，Parquet 复用 file_path。`/api/uploads/csv` + `/api/uploads/parquet` 两个 endpoint，`/api/preview/columns` 同步加 csv / parquet kind。`stream_compare` 互斥规则扩展到所有文件源（Excel / CSV / Parquet）。`pyarrow>=14.0` 加进 requirements。23 个 reader 测试（含 11 新增）全过。
- ~~字段级血缘 schema-aware 降级~~ ✅ —— `app/lineage/columns.py:select_columns` 加规则：SELECT * / SELECT t.* 在缺 schema 元数据时不再走 `source_info`（会输出空源表、伪 high），改为生成单条 `confidence='medium' + transform='星号展开（未解析）' + warning '通配符未展开'` 的占位条目，`source_tables` 锁定到 SELECT 所属表（按 alias map 解 t.*）。带 schema 时仍 high 展开，行为不变。多表 unqualified column 缺 schema 已经走 source_info 降 low+warning（既有）。新增 4 个 test case 覆盖。
- 字段级血缘（column-level lineage 独立深化，Phase 7 双轨之外）—— **前端可视化部分完成 ✅**（codex 分支 `codex/lineage-scheduler-openlineage`，待 merge）：`components/lineage/ColumnLineageGraph.vue` 消费 `result.column_edges` 渲染表→字段→字段链路 + 搜索 / 跳深度过滤 / 低可信度 / 歧义高亮 / `impact.downstream` 列表。**剩余**：解析端的字段精细化（多方言函数白名单、CASE/WINDOW 表达式归并、UDF / 包变量来源跟踪）按 Phase 7 双轨内推进。
- workflow 模板能力 + 端到端测试 + 乱码清理

### 产品打磨 Phase 1-4 后续清理（非阻塞，待排期）

`feat/product-ui-phase1` 分支已验收通过作为本轮产品打磨基线。下面是验收时记录的 5 项非阻塞收尾项，下一轮可挑做：

1. ~~**LineageGraph chunk 拆包**~~ ✅ —— `vite.config.js` 加 `rolldownOptions.output.advancedChunks` 把 `@antv/g6` / `cytoscape*` / `@codemirror/*` 各拆独立 vendor chunk。应用层 `LineageGraph` / `LineageGraphCytoscape` / `SqlEditor` 各自 <17KB 薄壳，重构应用代码不污染 vendor chunk hash → 浏览器长期缓存命中 vendor。引擎切换时也只下增量 vendor。
2. ~~**TopBar 搜索 / 通知占位按钮**~~ ✅ —— 选 (c) 补真实功能：搜索→`components/CommandPalette.vue` 命令面板，Ctrl/Cmd+K 全局触发，搜索导航 / 数据源 / 任务，键盘 ↑↓+Enter 跳转；通知→`components/NotificationPopover.vue`，挂当前 `asyncJob/asyncStatus`，运行中显示绿点 + cancel 按钮，无任务显示空态。
3. ~~**多脚本 report.process_steps 当前为空**~~ ✅ —— `batch_analyzer` 在每个文件 file 子结果里保留 `procedure_segments`（注入 `file_name`）+ `statements`（精简版：type/title/statement_index）。`report._build_batch_report` 优先消费 procedure_segments（有行号），否则从 statements 兜底，让顶层 INSERT/UPDATE 也出 step。`summary.process_step_count` 与 list 长度同步。
4. ~~**DataCompare 预览样例 mojibake**~~ ✅ —— mysql 8 docker image 默认 client/results=latin1，UTF-8 init script 被当 latin1 读 → utf8mb4 列里写 mojibake。修：`init_db/01_init.sql` 顶部加 `SET NAMES utf8mb4` + 各 `CREATE TABLE` 加 `DEFAULT CHARSET=utf8mb4`；`docker-compose.yml` 给 mysql8 加 `--character-set-server=utf8mb4 --skip-character-set-client-handshake` 防止下次起容器又踩同坑。**升级时需要 `docker compose down -v` 清掉旧 volume 重新初始化**（bind mount 不影响）。pymysql 端早就默认 `charset=utf8mb4`。
5. ~~**Excel/SQL 混合输入逻辑**~~ ✅ ——
   - **主键推荐 `recommendKey`** 跨模式：两侧字段都提取后，在交集里挑 id-like 命名 (`id/pk/key/no/code/sn/uuid`) 推荐；无 id-like 时降级填交集首列；无交集报"先做映射"；退回到 sqlglot 路径仅 source 是 SQL 时。
   - **`StepRules.vue` 流式分块开关**：任一边是 Excel 时 `disabled + opacity-60 + AlertTriangle 图标`，watch hasExcelSide 自动复位 stream_compare=false 防止 stale state。
   - **`StepMapping.vue` 跨模式预警面板**：跨 Excel/SQL 提示；CamelCase ↔ snake_case 命名风格不一致提示；无字段交集时 error 高亮；映射条目里列名不在已提取字段实时报"`xxx`不在源/目标"。
6. ~~**e2e 浏览器 smoke 缺 fixture**~~ ✅ —— 单独的 `tests/e2e/Dockerfile`（chromium + 字体 + playwright）+ `requirements-e2e.txt`，主 image 不变。`docker-compose.yml` 加 e2e profile（默认 `up` 不启动）：`docker compose --profile e2e run --rm e2e` 跑通 5 个 smoke。
   - chromium 对自定义 hostname `app` 会触发 HTTPS Upgrade → SSL 错；用 `network_mode: host` + `localhost:8010` 跳过这套
   - 用户机器 docker daemon 注入的 HTTP_PROXY 会污染容器，env 强清 + `NO_PROXY=*`
   - tests/ bind-mount 进容器，改测试不用 rebuild image
   - `page_with_error_capture` 改成只收 `pageerror`（uncaught throw），不收 `console.error`（mount 时短暂 fetch race 会误报）
   - e2e 测试同步到 vue-router 路径（不再依赖 button click 切 view）

### Phase 8（血缘图引擎双轨：G6 稳定 + Cytoscape 实验，待真实大图验证）

抽 `composables/useLineageGraphData.js` 之后，G6 / Cytoscape 共享同一份数据派生（filter / focal / BFS / 适用于 G6 的 combo 投影 / 适用于 Cytoscape 的 cyData）。`LineageGraphPanel.vue` 顶部切换器让用户在两个引擎之间切，prefs 持久化在 `localStorage.lineage-graph-prefs-v1.engine`。

- **G6 稳定**：现有所有交互（搜索 / 跳数 / 角色 + 边类型 + 可信度 + 脚本 + Schema 筛选 / 表视图 / combo 折叠 / 大图 300 截断）保持不变。
- **Cytoscape 实验**：差异化卖点是 compound parent 节点 —— 每个 schema 是一个 dashed 紫色容器，table 节点 `data.parent` 落入容器，比 combo 折叠更直观。可关闭 "Schema 容器" 开关回退平铺。
- **Cytoscape 已补完**：表视图（>100 节点逃生通道，复用 composable 的 `tableRows`，行可点击回跳到 graph 视图聚焦该节点） + 路径高亮（toggle 进路径模式 → 依次点选 from/to → BFS 算最短路径，路径上节点 / 边紫色高亮，其余 opacity=0.18 半透明，无路径时 from 红框提示）。
- **Cytoscape 暂未实现**：minimap、combo aggregated edge 的 `×N` 标签（compound 不需要）。
- **真实大图验证**：等用户拿真实 Oracle 多脚本 lineage 结果（300+ 节点）跑两个引擎对比筛选 / 聚焦 / compound 容器表现，再决定是否把 Cytoscape 升为默认 / 替换 G6。
- **不替换 G6**：路径稳定，先共存。Cytoscape 失败 / 大图卡顿，用户可一键切回 G6。

## 血缘图设计（双引擎：G6 稳定 + Cytoscape 实验）

参考 DataHub / Dagster / dbt Explorer / Atlan 的可扩展模式，避免 dagre 在 50+ 节点时把图压成一列：

- **默认 focal + N 跳 BFS**：`focusMode = neighborhood`，`hopDepth = 1`；节点数 > 30 时 `autoFocalId` 自动取最高度数的非 combo 节点为聚焦点；用户点击任一节点会更新 `clickedFocalId`，搜索命中也作为聚焦候选。
- **Schema 折成 combo 节点**（G6 模式）：`projectedBase` computed 把 `collapsedSchemas` 中的所有表投射成一个 `__combo:<schema>` 虚拟节点，跨 schema 的多条边聚合为单条粗边并加 `×N` 标签。点击 combo 节点切换展开。搜索命中折叠 schema 内的表会自动展开该 schema。
- **Schema 用 compound parent 表达**（Cytoscape 模式）：每个 schema 出一个 dashed 紫色容器节点，table 节点 `data.parent = 'schema:<name>'` 自动落入容器。比 combo 折叠更直观，是 Cytoscape 的差异化卖点。可在工具栏关闭"Schema 容器"开关回退到平铺。
- **布局/间距**：`layoutDir`（LR/TB）+ `spacingPreset`（compact/normal/relaxed）+ `engine`（g6/cytoscape）+ `compoundBySchema` 全部写入 `localStorage` key `lineage-graph-prefs-v1`。
- **逃生通道**：`viewMode = 'graph' | 'table'`（仅 G6 实现）；表视图按上游/下游 BFS 分组，每行可点击重新聚焦。节点 > 100 时显示推荐切表横幅。
- **数据层抽到 composable**：`composables/useLineageGraphData.js` —— `allGraphData` → `filteredBase`（角色/边类型/可信度/脚本过滤）→ `projectedBase`（schema combo 投射，仅 G6）→ `graphData / cyData`（focal+hop BFS）。两个引擎共享同一份数据派生，只是渲染层不同。
- **引擎切换**：`LineageGraphPanel.vue` 顶部 G6 / Cytoscape 切换器，两个组件通过 `defineAsyncComponent` 懒加载（用户只在切换时才付 cytoscape 的 ~534KB；G6 主体 ~1.4MB）。G6 是当前稳定实现，Cytoscape 处于实验阶段，待真实大图验证后再决定是否替换。
