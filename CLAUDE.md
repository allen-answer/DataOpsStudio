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

# 单元测试（vitest + jsdom + @vue/test-utils），跑得快（~1s），CI 会跑
npm test
npm run test:watch    # watch 模式
npm run test:coverage # 覆盖率（@vitest/coverage-v8）

# TypeScript 类型检查（S3.B 起 stores/*.ts 有类型）
npm run typecheck

# 从后端 /openapi.json 自动生成 TS 类型（S4.B）—— 后端 Pydantic model 改了
# 字段就跑一遍，前端类型自动跟。生成结果在 src/types/api-schema.ts；
# 友好别名（ApiUser / ApiDataSource / ...）在 src/types/api.ts，store 直接 import。
npm run schema:fetch     # 需要 docker compose up（hits localhost:8010）
npm run schema:from-file # 从已有的 .openapi.json 生成（CI / 离线用）
```

**主要验收以 Docker / WSL 构建为准。** Windows PowerShell 直接跑 `npm run build` 偶发 `Vite/Rolldown spawn EPERM`（Rolldown 子进程启动被 Windows Defender / 文件锁拦），属环境问题；切换到 WSL 跑构建：

```bash
wsl -d Ubuntu-20.04 -- bash -c "cd /mnt/g/work/DataOpsStudio/frontend/frontend && npm run build"
```

Docker 镜像构建链路在 `Dockerfile` 的 `frontend` 阶段（`node:20-alpine`），不受 Windows EPERM 影响。CI 也走 Linux runner。

### Docker（主要开发方式）

```bash
# 启动 app，代码有变动时自动重建
wsl -d Ubuntu-20.04 -- bash -c "cd /mnt/g/work/DataOpsStudio && docker compose up -d --build"

# 启动 app + 可选 MySQL 样例数据源
wsl -d Ubuntu-20.04 -- bash -c "cd /mnt/g/work/DataOpsStudio && docker compose --profile demo-db up -d --build"

# 仅重启 app（仅前端构建后；Python 代码改动必须重建镜像，restart 不够）
wsl -d Ubuntu-20.04 -- bash -c "cd /mnt/g/work/DataOpsStudio && docker compose restart app"

# 临时验证（不重建镜像）：把改动 cp 进容器内跑测试
wsl -d Ubuntu-20.04 -- bash -c "cd /mnt/g/work/DataOpsStudio && docker cp app/. dataops-studio:/app/app/ && docker cp tests/. dataops-studio:/app/tests/ && docker exec dataops-studio pytest"

# 查看日志
wsl -d Ubuntu-20.04 -- docker logs dataops-studio -f
```

应用访问地址：**http://localhost:8010**。默认 compose 只启动 app；MySQL 8 是可选 demo 数据源（profile `demo-db`），暴露在 **localhost:3307**，**不是 app 元数据库**。

`docker-compose.yml` 只 bind-mount `config/results/logs`，**app 源码（`main.py`、`app/`、`tests/`）和前端构建产物（`static/spa/`）都打入镜像**。所以：
- **前端**：构建产物 `static/spa/index.html` + `assets/` 由 Dockerfile 多阶段构建生成，**改前端代码必须 `docker compose up -d --build`**，仅 restart 不够。本地 dev 模式：`cd frontend/frontend && npm run dev`，vite proxy 到 `app:8000`。
- **后端**：改 Python 代码也必须 `docker compose up -d --build`，仅 restart 会继续跑老代码。

## 架构说明

### 后端

`main.py` 初始化 FastAPI，挂载 `/static`，并注册来自 `app/api/routes.py` 的唯一路由器（聚合 17 个领域子模块：`system / auth / projects / datasources / tasks / runs / scheduler / workflows / workflow_runs / history / lineage / lineage_graph / uploads / config_io / ai_utils / search / assets`）。新增 endpoint 加到对应子模块；不属于任何领域时新建子模块再 include 进 `routes.py`。

**横切 middleware**（main.py 按 LIFO 安装顺序：先安装的最后跑）：
1. `RequestIdMiddleware`（`app/api/_error_handler.py`）—— 纯 ASGI middleware，从 `X-Request-Id` header 接 / uuid 自动生成，写入 `request_id_ctx` ContextVar，response 必带回 header
2. `AuditLogMiddleware`（`services/audit.py`）—— mutating endpoint 流水到 `logs/audit.jsonl`
3. `MetricsMiddleware`（`app/api/_metrics_middleware.py`）—— 自动埋点 `http_requests_total{path,method,status}` + `http_request_duration_seconds`，path 归一化（`/api/tasks/<id>` → `/api/tasks/*`）防 label 基数爆炸
4. 三类 exception handler（HTTPException / RequestValidationError / Exception）统一 `{code, message, detail, request_id, retryable, ai_translation, suggestions}` envelope

**对比任务的数据流**：
1. `routes.py` → `runner.run_task(task_id)`（同步）或 `jobs.submit_task_run(task_id)`（异步后台线程）
2. `runner` 通过 `dbclients/factory.py` 查询数据 → `utils/sql_guard.py` 校验 SQL → 调用 `compare/engine.py`
3. `engine.compare_rows` 将行数据按 `key_columns` 归入 `only_source / only_target / diff / same` 四个桶
4. 结果以 JSON 和 Excel 写入 `results/`，并持久化到历史记录

**持久化** — 应用状态不依赖数据库，全部使用纯 JSON 文件：
- `config/datasources.json` / `config/tasks.json` / `config/workflows.json` / `config/workflow_templates.json` —— 业务配置
- `config/users.json` / `config/projects.json` / `config/lineage_ai.json` —— 用户 / 项目空间 / AI 配置（API key 加密落盘）
- `config/jobs.json` —— 异步任务状态（重启后保留；运行中的任务重启变 `failed`）
- `results/` —— 每次运行的 JSON + Excel 结果
- `logs/audit.jsonl` —— 审计日志，append-only

这些文件**不入库**（每个 clone 自己一份运行时状态）。仓库保留 `*.example.json`，新克隆首次启动 `JsonStore` 自动建空 file。`JsonStore`（`services/json_store.py`）是基于 mtime 缓存失效的线程安全泛型封装；`services/repositories.py` 提供模块级单例。

**异步任务执行** — `services/jobs.py` 使用 `ThreadPoolExecutor(max_workers=2)`。任务支持取消（`cancel_requested` 标志）、TTL 清理、失败重试。`/api/runs/{job_id}/cancel` 对作业流和对比任务一视同仁。

**SQL 安全** — 用户提交的所有 SQL 经过 `utils/sql_guard.py` 校验，只允许 `SELECT`/`WITH`，遇到 DML/DDL 关键字直接拒绝。

**作业流** — 参数驱动的多步骤 DAG：
- `services/workflow_engine.py` 提供 `run_workflow(workflow, variables, runners, cancel_check, resume_from, from_node_id)`，按 `depends_on` 拓扑序执行
- 节点类型：`params / compare / lineage / http / excel_export`，runner 拆在 `app/workflow/nodes/<type>.py`，集中注册在 `app/workflow/registry.NODE_RUNNERS`。新节点类型 (1) 在 `models.workflow.WorkflowNodeType` 加值 (2) 写 `(config, variables, **_) -> dict` runner (3) 在 registry 注册
- **变量与参数引用语法详见 `docs/PARAMETERS.md`**：`${name}` 引用变量、`${nodes.X.Y}` 引用上游节点输出、`${var | sql_in}` 等过滤器渲染 SQL IN 子句
- 单节点失败 → 下游 `SKIPPED`、旁路继续；`when:` 表达式条件性跳过
- **局部重跑**：`POST /api/workflow-runs/{run_id}/rerun` 指定 `from_node_id`，上游沿用上次 output（`reused=true`），祖先必须上次 SUCCESS
- **Artifact 模型**：节点产出文件统一通过 `output.artifacts: list[Artifact]` 声明，`WorkflowRun.artifacts` computed 顶层聚合，前端 `/results/<relative_path>` 下载，删 run 连带 rmtree
- WorkflowRun 落盘到 `results/workflow_runs/<run_id>.json`，由 `services/workflow_history.py` 管理

**调度器** — `services/scheduler.py` 接 APScheduler `BackgroundScheduler`：每个 `status=active` + `schedule_cron` 非空的 workflow 自动注册 `CronTrigger`，sync 任务定时重读 `workflow_store` 增删改。APScheduler 不在环境时回落 polling loop。`services/sensors.py` 支持 `file`（exists / newer_than + check_size）和 `workflow_success`（按 run_id 去重）两类 sensor，`Workflow.triggers` 配置，命中时 `submit_workflow_run(trigger="sensor:<type>")`。

**认证 / 权限 / 审计** — JWT HS256 + bcrypt（`services/auth.py`）。三档 role：`admin / editor / viewer`。资源（datasource / task / workflow / history）有 `project_id` 字段，`/api/{datasources,tasks,workflows,bootstrap,history}` 都接受 `?project_id=` 过滤。`AuditLogMiddleware` 落 `logs/audit.jsonl`，admin 通过 `/api/audit-logs` 查询。

**通知 / 集成** — `services/notifier.py` 三 channel：`webhook` / `wecom` / `email`（SMTP），workflow run finish 自动 dispatch。`services/openlineage_emitter.py` 把每次 workflow run 转成 OpenLineage `START` / `COMPLETE` event，支持 generic webhook / `marquez` / `datahub` 三种 target type（base URL 自动补端点 + Bearer token）。

**全局搜索 / 资产 / 全局 lineage 索引**（Phase 10 平台级架构）：
- `services/search.py` —— 跨 datasource / task / workflow / history / lineage_script 反向索引，AND 多 token + 字段权重评分。`/api/search?q=&kinds=&project_id=&limit=`，前端 `CommandPalette` 200ms debounce 调用
- `services/assets.py` —— 表当一等资产，`/api/assets/table/{name:path}` 返回反向引用（4 类）+ 全局索引补的 `primary_role` / `refresh_mode` / 上下游计数；前端 `/assets/table/:name` 路由
- `services/lineage_graph_query.py` + `services/lineage_index.py` —— 服务端 BFS 子图查询：v0 stateless `POST /api/lineage/graph/subgraph`（caller 提供 graph_edges），v1 stateful `GET /api/lineage/graph?asset_id=&direction=&depth=`（lazy 从最近 50 个 workflow_run lineage 节点 output 聚合，TTL 300s + run 数变化触发失效，admin `POST /api/lineage/graph/refresh` 强制失效，`GET /stats` 看构建状态）

**观测性** — `/metrics` 端点（`services/metrics.py`）走 Prometheus text format v0.0.4，自实现 Counter / Histogram / Gauge（不依赖 prometheus_client）。指标：`http_requests_total` / `http_request_duration_seconds` / `ai_usage_calls_total{kind,provider,status}` / `ai_usage_tokens_total{kind,direction}` / `lineage_index_table_count` / `lineage_index_edge_count` / `ai_jobs_inflight`。AI provider 调用通过 `app/ai/usage_log.log_call` 同步推 counter + 写 `logs/ai_usage.jsonl`。`DATAOPS_LOG_FORMAT=json` 切换结构化日志（`utils/logging_config.JsonLogFormatter`），自动注入 request_id（来自上面的 ContextVar）+ extra dict 透传。

**DB 驱动** — `dbclients/drivers.py` 声明各 `DatabaseType` 对应 Python 模块，`dbclients/factory.py` 在连接时动态导入。当前启用：`pymysql + cryptography`（MySQL）、`dmPython`（DM）。Oracle、DB2 为可选。`dbclients/pool.py` LIFO 连接池：每 datasource_id 一个池，`max_size=4`，`idle_seconds=600`，MySQL 路径 acquire 时 `SELECT 1` ping 验活。`extra.disable_pool=true` / 改 host/port 时池自动失效。

### 前端

Vue 3 SPA。状态管理走 **Pinia 渐进引入**：10 个 store —— `notice / datasource / task / workflow / lineage / batch / history / bootstrap / auth / project`。`App.vue` 顶部 `useXxxStore() + storeToRefs`，`provide('app', {...})` 仍 backward compat 把 store 字段平铺给 `inject('app')` 用。新代码直接 `useStore`。

**视图层**：`DatasourceView / WorkbenchView / WorkflowView / LineageWorkbenchView / HistoryView / AssetDetailView / LoginView` + admin 子路由 5 个（`UserManagement / AuditLog / ProjectManagement / AIConfig / SchedulerMonitor`）。Hash router（`createWebHashHistory`）+ `beforeEach` 守卫读 localStorage 跳 login + adminOnly 守卫拦非 admin。**13 个 view 全部走路由级 lazy load（`() => import('...')`）**，vite 自动按 chunk 拆分；G6（~1.4MB）/ Cytoscape（~534KB）/ admin views / WorkflowView DAG canvas 都不在首屏 chunk 里。

**作业流视图**按职责拆到 `components/workflow/`：`WorkflowDagCanvas`（SVG 画布）+ `WorkflowSettingsPanel`（元数据 sidebar）+ `WorkflowHistoryPanel`（运行历史 + mini gantt）+ `WorkflowRunNodeDetail`（节点详情）+ 4 个节点编辑器（params/compare/lineage/excel_export）。

**血缘视图**：`LineageReportView` 统一 9-tab（总览 / 输入资产 / 输出资产 / 处理过程 / 表级血缘 / 字段血缘 / 语义血缘 / 影响分析 / 风险 / AI 辅助 / AI 兜底推断）。`SemanticLineagePanel` 消费 `result.semantic_lineage`，渲染 observations / risks / 业务分组 DAG / 目标表 / procedures step 表。`LineageGraphPanel` 双引擎切换（G6 稳定 / Cytoscape 实验），数据派生抽到 `composables/useLineageGraphData.js` 共享。`LineageAIInferredPanel` 三栏分组展示 AI 兜底（红色 = parse_error / 黄色 = dynamic_sql / 蓝色 = column_attribution）。

**AI 集成**：4 个独立场景共享 provider 抽象 ——
1. **enrichment**（`/api/lineage/analyze` 默认开）：summary + suggestions + risks + column_hints
2. **inference 兜底**（admin 配置 `enable_inference` 开关）：对 `parse_errors` + `dynamic_sql_segments` + 字段歧义做白名单约束推断，结果落 `result.ai_inferred`
3. **错误翻译**（`/api/ai/translate-error`）：5xx / 长 4xx 错误的中文翻译 + 排查建议
4. **字段映射推荐**（`/api/ai/suggest-column-mapping`）：compare workbench `StepMapping` 紫色 ✨ 按钮触发

前端独有的 Figma → Vue 设计系统规则在 `frontend/frontend/CLAUDE.md`（Figma MCP 输出 React+Tailwind 参考代码 → 适配 Vue 3 SFC + 现有 `.btn`/`.card`/`.pill` 短类）。

构建产物输出到 `static/spa/`，由 FastAPI 在 `/static/spa/` 服务。`static/spa/index.html` 和 `static/spa/assets/` 已 gitignore，由 CI / Docker / release 脚本生成；手写资源 `static/spa/favicon.svg` 仍跟踪。`/spa` endpoint 加 `Cache-Control: no-cache`，避免 index.html 缓存住引用旧 hash bundle。

### 发版（release-please）

走 conventional commits 自动生成 changelog + 版本：

1. push commit 到 `main` 时格式照旧 (`feat(scope): ... / fix(scope): ... / docs: ...`)
2. `.github/workflows/release-please.yml` 自动维护一个 release PR，里面是按类型分组的 CHANGELOG diff + 自动 bump 后的版本号（feat → minor / fix → patch / `feat!: ` 或 `BREAKING CHANGE:` → major）
3. review release PR → merge → release-please 自动打 tag + 创 GitHub Release
4. tag 推送命中 `release.yml` → 跑 Windows offline 打包 + 上传到 release（draft）

配置：`release-please-config.json`（changelog 分组 / hidden types）+ `.release-please-manifest.json`（版本号 SoT）。`release-type=simple` 不写 `version.txt`，纯靠 manifest 跟踪。**首次发版前**：在 release PR 上手动调整 `manifest.json` 的版本号（比如 0.0.0 → 0.1.0）后 merge。

主要依赖：`@antv/g6`（血缘图）、`cytoscape` + `cytoscape-dagre`（实验引擎）、`@codemirror/*`（SQL 编辑器）、`@vueuse/core`（工具函数）、`lucide-vue-next`（图标）、Tailwind CSS v3（样式）。`vite.config.js` advancedChunks 拆 G6 / cytoscape / codemirror 各独立 vendor chunk。

### 血缘分析

`app/lineage/` 是按职责拆出的多模块包（基于 `sqlglot`）：

- `analyzer.py` — 单脚本入口，编排其他模块产出结果
- `batch_analyzer.py` — 多文件 ETL 血缘，支持 `.sql`/`.txt`/`.zip`
- `segments.py` — 存储过程分段抽取（BEGIN/END token 平衡，PL/SQL 控制流壳子跳过）
- `columns.py` — 字段级 lineage 抽取
- `dml.py` — DML 语句解析（INSERT/UPDATE/MERGE/DELETE/CTAS/INSERT OVERWRITE/TRUNCATE）
- `tables.py` / `roles.py` — 表引用归一化 + role 标签
- `aggregation.py` — 按目标表聚合 DML，识别 `delete_insert / truncate_insert / merge / append / mixed` 等 `refresh_mode`
- `grouping.py` + `config/lineage_group_rules.yml` — 业务分组规则（schema/basename/title 三类 matcher）
- `semantic.py` — 把 `target_summary / table_roles / procedure_segments / parse_errors / dynamic_sql_segments` 收口成 `result["semantic_lineage"]`
- `preprocess.py` — SQL 解析前归一化（全角标点 / `${name}` 模板变量 / `< =` 比较运算符空白 / `INSERT INTO tbl alias (alias.col, ...)` Oracle 形式 / `DELETE FROM tbl AS alias` 句法）
- `helpers.py` / `_common.py` / `clauses.py` / `dialects.py` / `variables.py` / `warnings.py` — 工具模块

入口和批量分析都接受可选 Schema 元数据文件（解析 `SELECT *` 和未限定列名）。

**方言路由** — `_resolve_dialect()` 把用户传入方言名映射到 sqlglot：
- `mysql` / `oracle` — 直传
- `dm` / `dameng` → `oracle`（DM 与 Oracle 高度兼容）
- `ob_mysql` / `oceanbase` → `mysql`（OceanBase MySQL 模式）
- `ob_oracle` → `oracle`（OceanBase Oracle 模式）
- 未知方言原样下传 sqlglot

**存储过程深度解析** — `extract_procedure_segments()` 识别 `CREATE [OR REPLACE] PROCEDURE / FUNCTION / PACKAGE BODY / TRIGGER` 块，token 平衡 BEGIN/END，从过程体抽取 DML。控制流壳子（IF/THEN、LOOP、CASE）跳过，DML 段保留 `procedure_name` 标签。外层 sqlglot 整体解析失败时仍能基于过程体段产出血缘。每段记录 `procedure_name / procedure_kind / segment_index / sql / confidence / line_start / line_end / preceding_comment / parse_status`。

**动态 SQL** — 三条精确路径：
1. `EXECUTE IMMEDIATE 'literal'` / `sp_executesql 'literal'` —— `confidence=high`
2. MySQL `SET @sql := '...'; PREPARE stmt FROM @sql; EXECUTE stmt;` —— 跟踪同名变量
3. PL/SQL `v_sql := 'INSERT ' || p_table || ' SELECT ...'; EXECUTE IMMEDIATE v_sql;` —— 字面量段保留、变量段替换为 `:var` 占位符后送解析（`var_concat` 低置信）

无法静态还原的（参数 / 包变量 / cursor 来的变量）输出 `confidence='unresolved'` 占位段触发"动态 SQL"warning。

**临时表** — CTAS 含 `TEMPORARY` / `TEMP` / `GLOBAL TEMPORARY` 时 mapping 加 `is_temp=true`，`dml_type=CREATE_TEMP_TABLE_AS`。批量分析在"外部源表"和"最终产物"全局警告里把临时表过滤掉。

## 关键设计决策

- **流式对比模式**（`limits.stream_compare = true`）：不将全部行加载到内存，通过有序迭代器流式归并。要求两边的 SQL 已按主键排序。
- **单 SQL 模式 vs 双 SQL 模式**：单 SQL 模式下 `source_sql` 在源端和目标端各执行一次；双 SQL 模式 `source_sql` 和 `target_sql` 分别独立执行。
- **`CompareRules.column_mappings`**：允许在对比前将源端和目标端不同名的列映射对齐。
- **`CompareRules.schema_policy`**：`warn`（默认）/ `strict`。strict 时检测到 source / target 列不一致直接 `raise ValueError`。
- **AI 输出永不替代规则结论**：6 不变量（不动 graph_edges / 输出落独立字段 / 白名单约束 / confidence 不允许 high / 前端徽章区分 / 默认关闭可降级）。
- **测试数据**：`init_db/01_init.sql` 中的 `users / users_archive` 和 `orders / orders_v2` 表有意设计了差异，用于演示对比结果。

## 路线图

整体路径：**血缘稳定 → 多来源对比 → 作业流 → 工程治理 → 血缘语义增强 → 领域模型收口 → 平台级血缘架构 + 观测性（已完成）**。

当前测试基线 **917 通过 / 0 失败 / 2 skipped**（本地 pytest 全量验证）。Phase 9 + Phase 10 全程交付：领域 schema 集中、AI 包独立、inference 异步化、错误响应统一、全局搜索、服务端 graph query、全局 lineage 索引、资产详情页 + custom aspects + 变更轨迹、字段列表 + 字段血缘热点 + datasource introspection、aspect governance dashboard、lineage 节点徽章、Prometheus `/metrics` + 结构化日志、路由 lazy loading、生产就绪闭环（ErrorBoundary + healthcheck + RUNBOOK）、`/api/v1/` 版本化前缀全部完成。下个 sprint 候选见[还可以做](#还可以做未排期) 章节。


### 已完成（按方向归类，不是时间线）

**Phase 1 血缘解析底座** — `analyzer.py` 拆 12 个 aspect 模块、方言路由（mysql/oracle/dm/ob_mysql/ob_oracle）、存储过程分段（BEGIN/END token 平衡 + 控制流壳子）、动态 SQL 三路径识别、CTAS 临时表过滤、Oracle hint / DBLink / `SELECT INTO` / 包常量 / cursor `FOR rec IN (...)` / `EXECUTE IMMEDIATE p_var unresolved` / 全角标点 / `${data_dt1}` 模板变量 / `< =` 比较运算符空白 / `INSERT INTO tbl alias (alias.col, ...)` Oracle 形式 / `DELETE FROM tbl AS alias` 句法 等 11 处 preprocess 归一化。回归 fixture 在 `tests/test_lineage_analyzer.py::test_oracle_proc_fixture_*`。

**Phase 2 多来源对比** — `SqlReader` / `ExcelReader` / `CsvReader`（UTF-8/GBK + delimiter）/ `ParquetReader`（pyarrow）抽象层；Excel↔SQL / CSV / Parquet 跨源对比；字段映射 + 类型标准化；字段筛选 UI；流式对比模式；`schema_policy=strict` 严格列匹配。

**Phase 3 作业流** — DAG 拓扑序执行 + 5 节点类型（params/compare/lineage/http/excel_export）+ 变量插值（`${name}` / `${nodes.X.Y}` / `sql_in` 过滤器）+ HTTP API CRUD/sync/async/cancel/局部重跑 from_node + `when:` 条件节点 + WorkflowRun 落盘 + Artifact 模型 + workflow 模板（创建 / 实例化 / 保存现有作业流为模板）。

**Phase 4 工程治理** — 配置安全（`config/*.json` 不入库 + 密码 API 脱敏 + 含密码导出二次确认 + JsonStore 落盘 0600）；模块拆分（`routes.py` 631→39 行 / `models.py` 540 行→5 子模块 / `workflow_nodes.py` 536 行→5 个 + registry / `analyzer.py` 956 行→12 模块）；前端 `WorkflowDetailView` 841→362 行 / `WorkflowRunView` 628→309 行；response_model 全收口。

**Phase 5 测试** — 645+ unit/integration（FastAPI TestClient）+ Playwright e2e（独立 Dockerfile + compose profile）+ 真实 DM 容器 fixture + Oracle PL/SQL 合成回归。

**Phase 6 平台化** — 多项目空间 + RBAC（admin/editor/viewer）+ JWT + bcrypt + `AuditLogMiddleware`（`logs/audit.jsonl`）+ `project_id` 过滤所有列表 endpoint + 前端 `useProjectStore` + sidebar 切换 dropdown + 5 个 admin views（用户 / 审计 / 项目 / AI 配置 / 调度器监控）+ `adminOnly` router 守卫。

**Phase 7 血缘语义增强（双轨）** — 静态规则**必须**独立可用，AI 只增强不替代。
- **轨道 A AI**：Provider 抽象（mock / openai-compatible / anthropic / ollama）+ 加密 API key（`secret_crypto`）+ enrichment 异步 job + Kimi K2.6 兼容（thinking 关闭 + temperature > 0 + max_tokens 4096 + JSON salvage）+ AI 兜底 P1（parse_errors）+ P2（dynamic_sql）+ P3（column_attribution）+ 6 不变量约束。中文 system prompt 全收口。
- **轨道 B 离线**：DML 聚合（refresh_mode 推断）+ 表角色识别（结构 + 命名）+ 业务分组规则（YAML 配 schema/basename/title matcher）+ 注释利用（statement title）+ semantic_lineage 收口字段 + 字段级 schema-aware 降级（SELECT * 缺 schema → medium + warning）+ transform 11 类细化（聚合/窗口/条件/类型转换/...）。

**Phase 8 调度 + 通知 + 集成** — APScheduler cron + sensor（file / workflow_success）+ notifier 三 channel（webhook / wecom / email）+ OpenLineage emitter（generic webhook + Marquez + DataHub，URL 自动补全 + Bearer token）+ 数据源连接池 LIFO（`max_size=4` / TTL 600s / ping 验活）+ 字段级血缘可视化 `ColumnLineageGraph.vue`+ 双图引擎共享 composable + Cytoscape compound parent。

**Phase 9 领域模型收口 + AI 异步化 + 错误响应统一**（6 day sprint，详见 commit log）：
- Day 1：`app/models/lineage.py` 收口 10 model + 9 Literal 闭集（envelope `extra="allow"` / 元素 `extra="ignore"` / AIInferredEdge confidence 拦 high → low 最保守降级）
- Day 2：`analyzer.analyze_sql_lineage` / `batch_analyzer.analyze_lineage_batch` / `lineage_service._attach_ai_inference` 出口包 model 校验，旧测试零变更通过
- Day 3：`_validate_and_filter_*` 改返回 `tuple[list[Model], int]`，外部 API 仍 list[dict]（caller 在 extend output 时 `model_dump()` 回 dict）
- Day 4：`app/ai/` 包独立 —— `providers/{base,mock,openai_compatible,anthropic,ollama}` + `prompts/{enrichment,inference,dynamic_sql,column_attr,error_translate,column_mapping}` + `schemas`（re-export）+ `filters`（实搬）+ **新增 `usage_log`**（`logs/ai_usage.jsonl`）。老路径保留 thin shim re-export
- Day 5：inference 异步化 —— `enqueue_lineage_ai_inference()` 复用 `_AI_JOBS`；`_attach_ai_inference(ai_async=True)` 立即返回 `{"status": "pending", "job_id": ..., "kind": "inference"}` placeholder；前端 `LineageAIInferredPanel` 加 banner + 自动轮询（exp backoff 500ms→3s）
- Day 6：统一错误响应 envelope `{code, message, detail, request_id, retryable, ai_translation, suggestions}` + 纯 ASGI `RequestIdMiddleware`（`finally` 不 reset token，让 unhandled exception handler 仍能拿到 rid）+ 三类 exception handler；AI 翻译改"按需"，admin AIConfigView 加 toggle `enable_auto_translation`（默认 off）；显式 `translateError()` 不受 gate 限制

**Phase 9 ADR 摘录**：schema 放 `app/models/lineage.py` 跟 `Workflow / CompareTask` 同级（避免 lineage 包内循环）；envelope 用 `extra="allow"` 让未建模字段透传；AI 翻译改按需（每个错误烧 token 不值）；Repository 抽象拆两步（先 audit/jobs 切 SQLite 再统一接口）。

**Phase 10 平台级血缘架构** — 把后端从"一次性报告"演进到"资产图谱服务"：
- ✅ #1 大图压测 fixture（`services/lineage_stress.py` + `/api/lineage/stress-fixture?size=N`）—— 合成 [10, 10000] 节点，6 schema 按 ods/dwd/dws/dim/ref/fct 30/25/20/5/5/15 分布，layer 严格递增；前端 `?stress=N` URL hook 跳过分析，紫色提示卡片提醒 DevTools Performance 录制对比
- ✅ #2 全局搜索（`services/search.py` + `/api/search?q=&kinds=&project_id=&limit=`）—— 跨 5 类资产（datasource / task / workflow / history / lineage_script），AND 多 token + 字段权重评分（name 100 > tables 50 > tags 40 > host/database 30 > description 20 > sql body 10）+ snippet 上下文；`CommandPalette.vue` 200ms debounce 调后端
- ✅ #3 v0 服务端 BFS 子图（`services/lineage_graph_query.py` + `POST /api/lineage/graph/subgraph`）—— stateless，caller 提交 graph_edges + asset_id + direction + depth + filters；BFS 切片 + role_filter 后置（锚点强保留）+ max_nodes 截断
- ✅ #3 v1 全局 lineage 索引（`services/lineage_index.py` + `GET /api/lineage/graph` + `/stats` + admin `/refresh`）—— lazy 从最近 50 个 workflow_run 的 lineage 节点 output 聚合 graph_edges / table_roles / target_summary，TTL 300s + run 数变化触发自动失效。同表多 run 的 primary_role 取频次最高，refresh_modes 合并去重，记 last_seen_run_id
- ✅ #4 资产详情页 MVP（`services/assets.py` + `/api/assets/table/{name:path}` + 前端 `/assets/table/:name`）—— 表当一等资产，反查 4 类引用（task source/target、workflow 节点 config 字符串、lineage_script 的 read/write tables、history 任务），index 补 `primary_role` / `refresh_mode` / 上下游计数 / `last_seen_run_id`
- ✅ #5 Cytoscape 决策 —— G6 默认 + Cytoscape 实验通道保留（双引擎共享 `useLineageGraphData` composable，等真实大图压测 empirical 数据再判断转正）

**观测性 + 性能**（Phase 10 收尾）：
- 路由 lazy loading：13 个 view 全部 `() => import('...')`，G6 (~1.4MB) + Cytoscape (~534KB) + admin views + WorkflowView DAG canvas 都不进首屏 chunk
- `/metrics`（`services/metrics.py`）—— Prometheus text format v0.0.4，自实现 Counter/Histogram/Gauge（不依赖 prometheus_client，**修了 Histogram observe 已是累计存储 / render 二次累加的 double-count bug**）；HTTP middleware 自动埋点 + `_normalize_path` 把 `/api/tasks/<id>` → `/api/tasks/*` 防 label 基数爆炸；`/metrics` 自身排除埋点
- 结构化 JSON 日志（`utils/logging_config.JsonLogFormatter`）—— `DATAOPS_LOG_FORMAT=json` 切换；`RequestIdInjectFilter` 从 Phase 9 Day 6 ContextVar 注入 request_id；extra dict 透传，non-serializable 值自动 stringify 兜底

### Phase 7 长期参考的设计方向

按用户调研，以下五个项目代表 SQL 血缘 / 数据治理的成熟思路。本仓库已在 Track B 走 Dataedo 方向，后续如要深做再分别参考：

- **Dataedo**（PL/SQL 拆 step + step-level lineage + 不支持的 step 标 `parse_status=unsupported`）
- **Gudu SQLFlow**（先产中间血缘模型再画图：objects / columns / relations / process_steps / target_summary）
- **sqlglot**（AST + 方言适配 + column-level lineage API）—— 已在用，继续做底座
- **DataHub**（schema-aware 字段级血缘 / 解析失败时降级）—— 字段级必须 schema-aware
- **OpenLineage**（job / run / dataset / facet 四元模型）—— 已对接 emitter

**DM 达梦**：路线是 `dialect=dm` 内部继承 oracle，再补 DM 特有语法、函数、系统表、分页写法。第一阶段：表级血缘 + DML 聚合 + DELETE+INSERT 识别；字段级精细血缘第二阶段。**别追求一步到位字段级 100% 准确** —— 先把 step 拆分、DML 计数、refresh 模式、动态 SQL 误报修准，可信度立刻上一个台阶。

### 还可以做（未排期）

**Phase 10 · 平台级血缘架构** ✅ 全部完成（详见上面"已完成"章节，5 项 + 观测性 + lazy loading）。下方留压测说明 + enhancement 候选。

**Phase 10 全 5 项（归档参考）**：

1. ✅ **真实大图压测 fixture**（commit `ccd395b`）—— `/api/lineage/stress-fixture?size=N` 合成 [10, 10000] 节点的血缘图（schema 池 / role 分布 / refresh_mode 真实分布）；前端 `/lineage?stress=N` URL hook 跳过分析直接加载，紫色提示卡片提醒 DevTools Performance 录制对比。给用户跑 G6 / Cytoscape 双引擎压测对比（main thread 耗时 / FPS / Memory 峰值）。15 个新 test。
2. ✅ **全局搜索 / 反向索引**（commit `d86c0ab`）—— `/api/search?q=...&kinds=...&project_id=...&limit=N` 跨 5 类资产搜索（datasource / task / workflow / history / lineage_script）。AND 多 token 语义 + 评分排序 + snippet 高亮 + 项目空间过滤。`CommandPalette.vue` 改后端调用，从图内 Ctrl+F 升级到 DataHub-style 平台级搜索。13 个新 test。
3. ✅ **`/api/lineage/graph` 服务端查询接口** —— v0 stateless `POST /api/lineage/graph/subgraph`（caller 提供 graph_edges）+ v1 stateful `GET /api/lineage/graph`（全局索引，TTL 300s + run 数变化失效）+ `/stats` + admin `/refresh`
4. ✅ **资产详情页 MVP** —— `/api/assets/table/{name:path}` 反查 4 类引用 + 索引补 role/refresh_mode/上下游计数；前端 `/assets/table/:name` 路由 + 4 张引用卡片。字段列表留下个 sprint
5. ✅ **Cytoscape 决策**：G6 默认 + Cytoscape 实验通道保留（双引擎共享 `useLineageGraphData` composable，等真实大图压测 empirical 数据再判断转正）
6. ✅ **元数据扩展点 / custom aspect** —— `app/services/asset_aspects.py` + SQLite 表 `asset_aspects` + schema 外置在 `config/asset_aspects.yml`（fallback `.example.yml`，6 种内置 type：owner / pii / sla / sensitive / tag / business_term）。`/api/assets/aspects` PUT/DELETE（editor+）+ `/aspects/types` 前端拉 schema + `/aspects/search` 反查（"哪些表标 PII"）。`/api/assets/table/{name}` 输出多 `aspects` 字段；前端 `AssetDetailView` 顶部分类卡片，editor+ 角色 inline 增删改，dynamic form 按 yml schema 渲染（string / list / enum）。新加 type 改 yml 不动表结构

**压测使用**（用 #1 fixture，empirical 数据决定 #5）：
1. 起 dev：`cd frontend/frontend && npm run dev` + 后端 `docker compose up -d --build`
2. 浏览器开 `#/lineage?stress=1000`（或 300 / 5000）
3. LineageGraphPanel 顶部切换 G6 / Cytoscape
4. Chrome DevTools Performance 录两段：init → 拖动 → 缩放 → focal 切换 → schema 折叠
5. 对比 main thread 耗时 / FPS 平均 / Memory 峰值

**已跑过的 baseline 数据**（2026-05-04，Playwright 自动化）：
| Engine | Size | first_canvas | mem_delta | DOM nodes | canvases |
|--------|------|--------------|-----------|-----------|----------|
| G6     |  300 | 102 ms       | +8.6 MB   | 454       | 5        |
| G6     | 1000 | 105 ms       | +8.4 MB   | 457       | 5        |
| G6     | 5000 | 167 ms       | +23.6 MB  | 457       | 5        |
| Cyto   |  300 |  94 ms       | +5.9 MB   | 435       | 3        |
| Cyto   | 1000 |  58 ms       | +5.0 MB   | 438       | 3        |
| Cyto   | 5000 | 414 ms       | +29.3 MB  | 438       | 3        |

结论：focal+BFS truncation 在两个引擎都正常（DOM 恒定 ~450，与 fixture size 无关）；G6 在 5000 节点 first paint 167ms vs Cytoscape 414ms（2.5×）；Cytoscape DOM 略少（~435 vs ~454）。两个引擎在用户操作上都流畅。compound parent 优势在合成 fixture 上没体现（schema 数固定 6 个），需真实多 schema 大图才能验证。**Phase 10 #5 决策：G6 维持默认，Cytoscape 留实验通道**，等真实 Oracle 数据再考虑转正。

**Phase 10 enhancement** ✅ 全部落地（2026-05-05）：

- ✅ **字段列表 / 字段血缘热点**（commit `513b846`）—— `services/assets.get_table_columns()` 反查最近 50 workflow_run 的 lineage `insert_mappings`，按 (write+read) 总热度倒序；`/api/assets/columns/{name:path}` 端点；前端 `AssetDetailView` 新表格卡片，含 transforms / 最近 run 跳转
- ✅ **aspect 反查可视化 / governance dashboard**（commit `c180ef4`）—— `services/asset_aspects.bulk_aspects_index()` + `/api/assets/aspects/index` 批量 endpoint；admin `/admin/governance` 视图按 type + value 子字段（pii.level=high / sla.tier=t0 等）二级过滤，schema-driven UI（加新 type 改 yml 自动出过滤器）
- ✅ **classification 用到血缘图**（commit `8686c4a`）—— `LineageGraphPanel` onMount 拉一次 aspects 索引传给两引擎，G6 + Cytoscape 共用 emoji 前缀方案（🔒 PII / ⏰ SLA / ⚠️ sensitive / 👤 owner），不动节点几何

**S1 enhancement** ✅ 全部落地（2026-05-05 同日）：

- ✅ **Aspect 变更轨迹** —— SQLite 表 `asset_aspect_history`（append-only），`upsert_aspect` / `delete_aspect` 同 transaction 落 history（insert/update/delete 三 action + old/new value）。no-op update（value 没变）跳过不污染。`/api/assets/aspects/history?asset_kind=&asset_name=&aspect_type=&changed_by=&limit=` AND 过滤组合。前端：`AssetDetailView` aspects 卡顶部"历史"按钮展开 timeline；`AspectGovernanceView` 加"变更日志"tab 全局看
- ✅ **字段血缘热点深化** —— `get_column_lineage(table, column)` 从 insert_mappings 反查 upstream / downstream 字段链（合格名直接归 source_table，单源 unqualified 归 source_tables[0]，多源 unqualified 拒绝以保持归属确定性）；`/api/assets/column-lineage/{name}?column=xxx` 端点；前端字段表格行点击展开"← 上游 / 下游 →"chip，点 chip 跳目标表 + 自动展开该字段
- ✅ **Datasource introspection** —— `services/datasource_introspect.introspect_columns(datasource_id, table_name)` 走活的 dbclients pool 拉 `information_schema.COLUMNS`（MySQL）/ `all_tab_columns + all_col_comments`（Oracle / DM）/ `SYSIBM.SYSCOLUMNS`（DB2 stub）；标识符 alphanum/_/$/. 白名单防注入；in-memory cache TTL 300s。`/api/assets/introspect/{name}?datasource_id=` 端点。前端字段卡顶部 datasource 选择器 + "拉真实"按钮 → introspect 拉到后跟 lineage 反查 merge：表里有但 lineage 没动过的标 "dormant"，lineage 有但 introspect 拉不到的标"已删除?"

**下一批 enhancement 候选**（仍未排期，长期 backlog 见下方"通用未做"）：

- **Procedure refresh mode 语义模式深化** —— 识别"先 truncate 后 insert"等 procedure-only 模式（Phase 7 轨道 A 增量）
- ✅ **字段血缘 tracing UI 多跳**（2026-05-09 落地）—— `services/assets.get_column_lineage()` 加 `depth` / `max_nodes` 参数，重构内部为「先建 edge index 再 BFS」。`depth=1`（默认）保留旧 shape；`depth>=2` 每个 item 多带 `hop` / `from`，cycle 切断 + max_nodes 截断。`/api/assets/column-lineage` 加 `depth` `max_nodes` query。前端 `AssetDetailView` 字段展开行加 1/2/3 跳 picker；多跳场景按 hop 缩进渲染 chip，每个 hop≥2 chip 显示 `← from parent` micro-label 让用户追溯路径。补 6 个测试（depth=2 上下游 / depth=1 向后兼容 / max_nodes 截断 / cycle 切断 / endpoint depth 参数）。回归 911 → 917

**Phase 11 候选 · 数据对比 × 血缘联动**（待启动，2026-05-08 立项）—— 把 Compare 和 Lineage 两套独立能力拼成「沿血缘逐层对比 → 定位数据偏离层」的诊断工具：

- **动机**：用户报「dws 报表数 ≠ ods 源」时，目前要人手逐层建 compare task + 串 workflow，痛点高。已有 `services/assets.get_column_lineage()` 字段链 + `CompareTask` 多源对比 + `workflow_engine` DAG 三件套都成熟，差一个编排器
- **MVP 路径**（最小可演示切片，先不啃聚合）：
  1. 后端加 `POST /api/lineage/trace-compare` body `{table, column, sample_keys, datasource_map}` —— 按 column lineage 反推 N 跳，对每一跳 emit compare 节点（直传 / 改名 / 类型转换走行级 1:1 diff，聚合 / 过滤跳标 `strategy=agg_check` 走行数 / sum / null 率口径检查待人工确认）
  2. 前端 `AssetDetailView` 字段表格行加「沿血缘溯源」按钮 → 跳到自动生成的 workflow draft，用户调整 PK / datasource 后一键跑
  3. 跑完结果可视化：在血缘图（`LineageGraphPanel`）每条边上色（红 = 该层数据偏离 / 绿 = 一致 / 灰 = 未对比），用户一眼看到漂移源头
- **Tradeoff 核心**：transform 类型的非 1:1 性 —— 聚合（GROUP BY / SUM）和过滤（WHERE）不能行级 diff，要么按 join key 重算 + 区间对比、要么退化口径检查。这部分**适合 AI 兜底**（让 LLM 看 transform SQL 决定每跳对比策略，比纯规则更合适，且复用已有 enrichment provider 抽象）
- **参考 hooks**：`services/assets.get_column_lineage()` / `app/lineage/columns.py` insert_mappings / `app/services/workflow_engine.py` DAG 编排 / `app/services/runner.run_task` 单跳对比 / `app/ai/providers/` LLM 抽象

**Phase 11 候选 · 数据库方言模块化**（spike-driven 重构，2026-05-08 立项 / 2026-05-09 MVP spike 落地）—— 把当前散在 ~10 处的 `if db_type == ...` 收口到 `Dialect` 类，给后续接 OceanBase / PG / TiDB + 加方言相关能力（pagination / quote / 错误码翻译）打底：

- **动机**：DB-specific switch 当前散在 `dbclients/factory.py` 6 处（连接构造 + 错误抽取）/ `dbclients/pool.py` 1 处（MySQL ping）/ `services/datasource_introspect.py` 3 处（列元数据 SQL）。还没乱但已经痒；再加一两个 DB 或一两个方言能力（生成对比 SQL 时分页 / 引用符 / 类型映射）就会失控
- **设计**：`app/dbclients/dialects/<db>.py` 每库一个 `Dialect` 类，**只放真正会分叉的能力** —— `quote_identifier` / `pagination_clause` / `introspect_columns_sql` / `extract_error_detail` / `ping_sql` / `dialect_for_sqlglot`。`register(DatabaseType, dialect)` lazy registry（用独立 `_LOADED` flag 避免「test 先 import 单个 dialect 子模块只 register 一个 → _REGISTRY 非空 → 早退漏其它注册」的坑），`get_dialect(db_type).foo()` 单一调用入口。**血缘那边不改**（`app/lineage/dialects.py` 已经按 sqlglot dialect 路由，已经够清爽）
- **✅ MVP spike（已落地）**：`app/dbclients/dialects/{__init__, base, mysql, oracle, dm, db2}.py` —— `Dialect` ABC 只声明 `introspect_columns_sql(schema, table) -> str` 一个方法。`OracleDialect` 同时被 `DM` register（DM 兼容 Oracle 数据字典视图，避免空壳子继承）。`services/datasource_introspect._columns_sql` 改成 thin shim：保留 `_validate_identifier` 防注入校验 + 委托给 `get_dialect(db_type).introspect_columns_sql(...)`，外部 API（`introspect_columns`）契约不变。新增 `tests/test_dbclients_dialects.py` 5 测：registry 单例 / DM 共享 Oracle 实例 / 4 个 db_type 都注册 / SQL 含 5 个必需输出列 alias / schema='' 跨库不抛错。回归 894 → 899 通过 0 失败
- **✅ 第二步（已落地）**：搬 `factory._connection_test_sql` 到 `Dialect.connection_test_sql()`。MySQL `select 1 as ok` / Oracle+DM `select 1 as ok from dual` / DB2 `select 1 as ok from sysibm.sysdummy1`。`test_connection` 改用 `get_dialect(...).connection_test_sql()` 直调，删了 factory 里的私有 helper。补 4 个契约测试：MySQL 不能带 FROM / Oracle+DM 必须 from dual / DB2 用 sysdummy1 / 全方言列别名 `ok`。回归 899 → 903
- **✅ 第三步（已落地）**：搬 `factory._connect` 到 `Dialect.connect(source, module_name)`。`factory._connect` 化简为一行委托。MySQL 处理 pymysql vs MySQLdb 的 kwarg 差异（`password` vs `passwd` / `database` vs `db` / pymysql 多 read_timeout/write_timeout）；Oracle 拼 dsn 兼容 extra.dsn；DB2 走 add_db2_dll_directories + conn_str 拼接 + CONNECTTIMEOUT 自动补；DM 不再共享 OracleDialect 实例 → 升级成 `DmDialect(OracleDialect)` 子类，只 override connect（dmPython 用 server/port + schema option + 老接口 positional 兜底），SQL 层方法继承 Oracle。`CONNECT_TIMEOUT_SECONDS` / `QUERY_TIMEOUT_SECONDS` 从 factory.py 移到 `dialects/base.py` 共享，避免 factory 与 dialects 互相 import。factory.py 顺势清掉不再用的 `importlib` / `add_db2_dll_directories` / `DatabaseType` import。补 8 个 connect 契约测试用 monkeypatch 拦 `importlib.import_module`：pymysql/MySQLdb kwarg 差异 / Oracle dsn fallback vs extra / DM schema 注入 / DM kwarg 失败 fallback positional / DB2 conn_str 拼装 / DB2 honor extra.conn_str。回归 903 → 911
- **下一个候选**：`pool._ping_mysql` 只 1 个分支，按「≥2 分支才抽」原则先不动 / `factory._extract_driver_error_detail` 已经 dialect-agnostic 的 best-effort 探测不需要搬 / `compare/runner.py` 的对比 SQL 生成（如果后续要支持 OFFSET/FETCH 分页或 LIMIT 跨方言，再加 `Dialect.pagination_clause`）。**当前 spike 阶段性收尾** —— 散在 dbclients 的 ~10 个 db_type switch 已收口到 4 处（factory.py 没了，全在 `dialects/<db>.py`），接新 DB 只动 `dialects/<db>.py` + `drivers.DRIVER_MODULES` 两处
- **Tradeoff 核心**：抽象层数 vs 接入新 DB 成本。原则是**只在已有 ≥2 个分支的能力上抽**（present pain），future-pain 不抽（YAGNI）；避免变成"补 1 个 driver 映射 + 1 行 sqlglot dialect → 实现 12 个空方法"的过度抽象
- **不要做的**：DB 全栈适配器（连接 + SQL 解析 + lineage + UI 都按 DB 拆 = 把 10 个 switch 换成 6 个空方法）；`utils/sql_guard.py` 现在 dialect-agnostic 工作得很好，不强行接入

**通用未做**：

- **字段级血缘解析端深化**：Oracle PL/SQL 深度场景（可视化 ✓ + transform 细化 ✓ + cursor 来源跟踪 ✓ + package 变量声明 ✓ + UDF 调用追溯 ✓ + 变量不污染字段映射 ✓ + procedure/function 不当 fake target ✓ + 显式 cursor 声明 ✓ + cursor 参数化 + INSERT ALL fan-out ✓ + BULK COLLECT/FORALL ✓ + PACKAGE BODY 多嵌套 proc ✓ + variables 渲染 ✓ + TRIGGER 源表 ✓ + 匿名 PL/SQL 块 ✓ + proc-body 局部变量过滤 ✓ + batch report 变量聚合 ✓ + MERGE 列级映射 ✓ + RETURNING INTO 子句剥离 ✓ + CTE 链穿透 ✓ 已落）。S5 PR21：`WITH cte_a AS (... FROM ods.tree), cte_b AS (... FROM cte_a a JOIN ods.names t)` 这种 CTE 链，cte_b body 引用 `cte_a a`，旧逻辑只到 `a` 别名就停。`columns._add_derived_select_columns()` 现场补 local_alias_map（CTE 名也参与别名解析）；`source_info` 在 alias_map.get 后再用 subquery_map 二次查找穿透到底层物理表。S5 PR20：Oracle PL/SQL `INSERT/UPDATE/DELETE ... RETURNING col INTO :var, :var` 让 sqlglot 整脚本解析失败。preprocess 新增 `_strip_returning_into` 用等长空白替换剥离 RETURNING ... INTO 尾巴（保行号），主体 INSERT/UPDATE/DELETE 血缘完整保留。S5 PR18-19：批量报告 `_build_batch_report` 透传 `variables` + `summary.variable_count`，`batch_analyzer` 给每个 file 加 `variable_decls` 完整字典 + 顶层聚合按 (name, kind) 去重带 `file_name` 标记，前端 SummaryPanel 多一列"来源脚本"。MERGE 子句 `merge_table_mappings` 扩展：扫 `WHEN MATCHED THEN UPDATE SET col = src.col` + `WHEN NOT MATCHED THEN INSERT (cols) VALUES (exprs)` 产列级映射（`dml_type=MERGE_UPDATE / MERGE_INSERT`），表级 mapping 仍保留向后兼容。S5 PR17：`v_row ods.orders%ROWTYPE; SELECT INTO v_row FROM ods.orders;` 这种声明，sqlglot 把 SELECT INTO 改写为 CREATE TABLE v_row AS，让 v_row 错落进 tables 列表。新增 `variables.all_plsql_local_names()` 扫所有 PROCEDURE 体 IS/AS 段 + DECLARE 块 + PACKAGE BODY 顶层声明，得到全脚本局部变量名集合：(1) 注入 PR5 的 source_info 过滤集（v_row.id 不被误归到表）(2) 后处理 flat_tables 过滤掉 sqlglot 误识别的变量名表。proc_local 不进 result.variables 列表，前端面板仍只显示 package/declare 有效变量。S5 PR16：顶层 `DECLARE ... BEGIN ... END;` / `BEGIN ... END;` 没 CREATE 前缀，sqlglot 整脚本解析失败。新增 `_RE_ANON_PLSQL_BLOCK` 在 extract_procedure_segments 头部先扫，落在 CREATE 范围外的当 `procedure_kind=ANONYMOUS` 处理 —— cursor 解析、变量提取、TRIGGER 源表等所有 PR1-15 能力都对匿名块生效。S5 PR15：`CREATE TRIGGER trg AFTER INSERT ON ods.orders ... BEGIN INSERT INTO dwd.audit_log VALUES (:NEW.id); END;` 这种 trigger 的 INSERT 过去只看到 `dwd.audit_log` 一端没源。新增 `_RE_TRIGGER_SOURCE` 抽 `[BEFORE|AFTER|INSTEAD OF] event(s) [OF cols] ON <table>` + `procedure_segments[*].trigger_source` 字段 + `_trigger_supplemental_edges()` 补 `ods.orders → dwd.audit_log` 边（`edge_type=TRIGGER` / `confidence=medium`）。S5 PR14：把 PR3 抽出来的 PL/SQL 变量列表（package_constant/variable + declare_constant/variable + 模板变量）透传到 `report.variables` + `report.summary.variable_count`，前端 LineageSummaryPanel 在 8 卡片下方渲染变量表格（变量名 + kind 友好标签 + assigned_value）—— PR3 的后端工作终于在前端可见。S5 PR13：旧逻辑只识别 PACKAGE BODY 第一个 PROCEDURE，多 proc 后续都漏。新增 `_find_nested_proc_scopes()` 在包体范围内扫所有 `PROCEDURE/FUNCTION name [(params)] [RETURN type] IS|AS BEGIN ... END;`，每个独立 scope；包级 cursor 声明合并进每个嵌套 proc 的 declaration_region 共享。procedure_name 限定为 `pkg_name.proc_name`。S5 PR9：`SELECT BULK COLLECT INTO v FROM tabA; FORALL i ... INSERT INTO tabB VALUES (v(i).col)` 这种 PL/SQL 数组中转模式：`_bulk_collect_supplemental_edges()` 建立 var → source_tables 映射，扫 INSERT/UPDATE/MERGE 段是否引用同名 var(<idx>)，补 supplemental 边（`edge_type=BULK_COLLECT` / `confidence=medium`）。S5 PR4：`INSERT INTO X VALUES (pkg.fn(...))` 这种调用 UDF 的 DML 语句，过去 INSERT 自身 source_tables 是空 → 无血缘边。新增 `_udf_supplemental_edges()`：从 procedure_segments 的 `procedure_kind=FUNCTION` 段提取 udf_reads（fn 名 → 函数体 SELECT 的源表），扫每个 statement SQL 看引用了哪些已知 UDF + DML target 是什么，补 source → target 边（`edge_type=UDF_CALL` / `confidence=medium`），CREATE FUNCTION 自身定义语句跳过避免误补。S5 PR5：`SELECT g_app_id FROM ods.orders` 不再把 `g_app_id` 误归为 ods.orders 的 source_column —— `source_info()` 接 `variable_names` 集合，无 table 限定的 Column 名落在该集合时跳过，不污染 source_columns/source_tables；mapping `source_type="variable"`（区分纯常量），ods.orders 自动落到 `graph_groups.dependency_tables` 而非 source_tables。S5 PR1：`FOR rec IN (SELECT FROM tabA) LOOP INSERT INTO tabB VALUES (rec.col)` 这种 INSERT 没 source_tables 的场景，靠 `procedure_segments[*].cursor_sources` + `_cursor_supplemental_edges()` 补 `tabA → tabB` 边（`edge_type=CURSOR_LOOP_INSERT` / `confidence=medium` 区分静态推断）。S5 PR2：`_collect_loop_scopes()` 二次扫描 body 找所有 `FOR ... LOOP ... END LOOP` 范围（含嵌套），cursor LOOP 体内多个 DML 段（INSERT / UPDATE / DELETE / MERGE）都继承同一份 cursor_sources（嵌套时取最内层）。S5 PR3：`variables.package_variables(sql)` 抽 `PACKAGE BODY` 顶层和 `DECLARE` 块的常量与变量声明（`g_app_id CONSTANT VARCHAR2(32) := 'JY';` / `v_cnt NUMBER := 100;`），合入 `result.variables` 列表，每条带 `kind` 字段（`package_constant` / `package_variable` / `declare_constant` / `declare_variable`）。PROCEDURE 体内的局部变量不进列表（避免串味）。
- **TypeScript 渐进迁移**（S3.B 全 10 store + S4.A api.ts + composable + S4.B codegen 落地）：10/10 store + api.ts（含泛型 apiGet&lt;T&gt; / apiJson&lt;T&gt;）+ useLineageGraphData composable 全部 ts；openapi-typescript 从 /openapi.json 自动生成 `src/types/api-schema.ts`，友好别名在 `src/types/api.ts`（auth / project / datasource / task / workflow 已用）。剩 view（&lt;script setup lang="ts"&gt;）大头（30+ 个 .vue 文件 script 改 lang="ts"）留下个 sprint
- **i18n 字符串抽取**（S4.C 7 个 PR 收口）：vue-i18n 11.x + zh/en 镜像 + topbar 切换 + i18n key 对齐 vitest 校验。覆盖：7 个主 view header / tabs + LineageReport 9-tab + workbench 4 step + 5 个 workflow node editor (params/compare/lineage/excel/http) + WorkflowSettingsPanel 主体 + LineageReport 6 panel 内部（Risk / Impact / Steps / Asset / AIEnrichment / AIInferred）+ 5 个 workbench 子 view + AspectGovernance + AssetDetail + FilterBar + CommandPalette + admin 5 view H2。namespace：nav / login / topbar / common / pages / lineageReport / lineagePanel / workflowEditor / workbench / filterBar / commandPalette / admin 共 12 个

## 血缘图设计（双引擎：G6 稳定 + Cytoscape 实验）

参考 DataHub / Dagster / dbt Explorer / Atlan 的可扩展模式，避免 dagre 在 50+ 节点时把图压成一列：

- **默认 focal + N 跳 BFS**：`focusMode = neighborhood`，`hopDepth = 1`；节点数 > 30 时 `autoFocalId` 自动取最高度数节点
- **Schema 折成 combo 节点（G6）** vs **compound parent（Cytoscape）**：`localStorage.lineage-graph-prefs-v1` 持久化引擎选择；前者跨 schema 多边聚合 `×N`，后者每个 schema 是 dashed 紫色容器
- **逃生通道**：`viewMode = 'graph' | 'table'`（G6）；> 100 节点时显示推荐切表横幅
- **数据派生抽到 composable**：`useLineageGraphData.js` —— `allGraphData` → `filteredBase`（角色 / 边类型 / 可信度 / 脚本过滤）→ `projectedBase`（schema combo，仅 G6）→ `graphData / cyData`（focal+hop BFS）。两引擎共享派生只换渲染层
- **引擎切换**：`LineageGraphPanel.vue` 顶部切换器，两组件 `defineAsyncComponent` 懒加载（cytoscape ~534KB；G6 ~1.4MB）
- **真实大图验证**：等用户拿真实 Oracle 多脚本 lineage（300+ 节点）跑两引擎对比，再决定是否替换 G6。当前 G6 稳定 / Cytoscape 共存

### 跟 DataHub / Atlan / Dagster / dbt Explorer 的差距

参考它们做的是"**前端血缘图可扩展交互模式**"，**不是平台级资产图谱架构**。Phase 10 已经把后端架构从"一次性报告"演进到"资产图谱服务"，5 项差距全部已落地：

**已对齐的 viz 模式**：双图引擎切换、大图收敛 / focal+N-hop、schema 聚合（combo / compound parent）、表格视图逃生通道、role/edge/confidence/script/schema 多 facet 过滤、命中搜索定位、PNG/JSON 导出、Cytoscape 路径高亮。

**5 项平台能力对比 DataHub / Atlan**：

1. ✅ **后端是资产图谱服务** —— Phase 10 #3 v1 落地：全局 lineage 索引 + `GET /api/lineage/graph` 按 `asset_id + direction + depth + filters` 切片查；前端可分 hop 增量加载
2. ✅ **资产详情页 + classification** —— Phase 10 #4 落地反向引用 + 索引补 `primary_role` / `refresh_mode` / 上下游 / `last_seen_run_id`；Phase 10 #6 落地 custom aspects（owner / pii / sla / sensitive / tag / business_term，schema 外置 yml + SQLite 持久化 + editor+ inline 编辑）
3. ✅ **全局搜索 / 反向索引** —— Phase 10 #2 落地：`/api/search` 跨 5 类资产，AND 多 token + 评分 + project_id 过滤；CommandPalette 接入
4. ✅ **元数据扩展点 / custom aspect** —— Phase 10 #6 落地：DataHub-style 多 aspect_type 模型，schema 外置 yml 让加新 type 不动表结构；`/api/assets/aspects/search` 反查"哪些表标 PII"
5. ✅ **服务端缓存 / 增量加载** —— Phase 10 #3 v1 全局索引 + #1 大图压测 fixture（empirical 数据决定 Cytoscape 转正）

**当前判断**：viz 模式 + 平台架构 + 元数据模型都已就位。下一步重心在**字段级资产 + classification 反查可视化 + lineage graph 上叠 PII/SLA 徽章**（让"敏感数据流向哪里"一眼可见的 governance dashboard）。
