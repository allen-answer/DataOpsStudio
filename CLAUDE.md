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

`main.py` 初始化 FastAPI，挂载 `/static`，并注册来自 `app/api/routes.py` 的唯一路由器（聚合 19 个领域子模块：`system / auth / projects / datasources / tasks / runs / scheduler / workflows / workflow_runs / history / lineage / lineage_graph / uploads / config_io / ai_utils / search / assets / scenarios / slow_sql`）。新增 endpoint 加到对应子模块；不属于任何领域时新建子模块再 include 进 `routes.py`。

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

**大结果落盘**（切片 B-G 完成，详见 `docs/COMPARE_RESULT_STORAGE.md` + `docs/STREAMING_COMPARE_WRITER.md`）：
- `RunLimits.result_format`：`"json"`（默认，向后兼容，单文件 `<run_id>.json`）/ `"parquet"`（目录 `<run_id>/{meta.json, *.parquet}`）
- `RunLimits.persist_same_bucket: bool`：parquet 模式下 same 桶是否全量落 parquet（默认 False，只在 meta.json 记 count + sample）
- `app/compare/result_writer.py`：`ResultWriter` 协议 + `JsonResultWriter`（向后兼容老格式）+ `ParquetResultWriter`（目录 + batch flush row group，writer 内存 = O(batch_size)）
- `app/compare/engine.py`：`compare_rows_streaming(...)` / `compare_sorted_row_events(...)` 两个 generator —— runner 在 parquet 模式下不再持完整 buckets dict（4 象限路径矩阵详见 STREAMING_COMPARE_WRITER §7）
- 读侧 API：`GET /api/runs/<id>/meta` envelope + `GET /api/runs/<id>/buckets/<bucket>?offset=&limit=` 分页（`services/run_result.py`：`load_run_meta` / `read_bucket` / `iter_bucket_rows` 自动 dispatch parquet/legacy）
- 异步导出：`POST /api/runs/<id>/export-excel` 走 `submit_excel_export`（`services/jobs.py` ThreadPoolExecutor），JobInfo `kind=excel_export`，前端 poll 后下载
- 项目级授权：`compare_result_project_id` / `result_download_project_id` / `job_project_id` 三个 helper 统一在 `app/api/_authz.py`，覆盖 `/api/runs/<id>/*`、`/results/<id>/*` 下载、job 状态查询（详见 `docs/PROJECT_AUTHORIZATION.md` §4.3）
- 剩余未做：Excel `write_only` 流式（切片 F.4）、`/api/history` offset 标准分页、writer.samples 收口到 manifest

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

### SQL 优化沙盒（Phase 12 起的「AI 测试沙盒」 → Phase 14 P0-1 重定位为 SQL 优化工作台,`app/scenarios/`）

> **历史**:Phase 12 时定位为「admin 测试沙盒」(adminOnly + `/admin/sandbox` + Beaker 图标),用途是回归 / demo。Phase 14 P0-1 重定位:实际用途是数据工程师 / DBA 日常处理慢 SQL 工单 —— 升级为顶级一级菜单 `/sql-optimize`(editor+,Microscope 图标),`/admin/sandbox` 保留 301 重定向兼容老书签。后端 API + scenario DSL + 沙盒能力完全不变,只是 UI 位置 + 命名 + 权限调整。



把 yml 描述的虚拟业务场景一键生成真数据 + 真对比任务 + 真血缘脚本，把对比 / 血缘 / 慢 SQL 三大模块的回归测试 / 演示数据 / 真实案例都收口到一个 admin 视图。8 个职责模块：

- `models.py` —— Pydantic DSL 收口（Scenario / TableDef / ColumnDef / IndexDef / ColumnOverride / AnomalyDef / WorkloadDef + AISettings / DomainDef）；`extra='forbid'` 拦 yml 笔误，anomaly / workload 子项 `extra='allow'` 因 kind-specific 字段太多列 Literal 反失控
- `loader.py` —— `load_scenario(path) → Scenario` + `list_scenarios()` 扫 `config/scenarios/*.yml`（坏文件单独列错因 + 没用户文件时回退 example 让新部署能看 demo）
- `generator.py` —— 7 个 column generator（uuid_short / random_int+zipf / realistic / timestamp / enum+weights / constant / sequence+prefix）+ 6 个 anomaly kind（missing_rows / extra_rows / value_drift±X% / null_drift / duplicate_pk / type_mismatch），seed 决定性；realistic 优先用 ai.fill 写进 col.values 的业务样本池，否则按类型嗅探 fallback
- `ai_filler.py` —— LLM 给 realistic 列业务样本池（20~30 值 dedup + cap）+ 表中文描述，走 lineage_ai provider；max_calls cap 50 防 token 烧爆，单字段失败不影响其它，provider=off → ok=False + 占位字段
- `dialects/` —— materialize 方言抽象：base.MaterializeDialect ABC + mysql / oracle（DM 复用 Oracle 实例）。MySQL 反引号 + `%s`；Oracle 双引号 + `:1, :2, ...` + DROP 包 PL/SQL 异常块吞 ORA-00942 + schema_create_sql=None（schema=user 不擅自建）
- `materializer.py` —— `build_materialize_plan` 纯函数产 DDL + INSERT 计划；`apply_plan` 接收最小 `SqlExecutor` 协议（pymysql cursor / mock 都能包，caller 自管事务边界）；通过 dialects 派发让方言变换不动主流程
- `runtime.py` —— datasource_id → pool.borrow + cursor → apply_plan + commit，IO 边界单独一层，方便单测 monkeypatch
- `recorder.py` —— compare_task workload → CompareTaskCreate 走 task_store.create（SELECT 显式列名 + ORDER BY pk + sql_mode=DOUBLE，dialect-aware quote）；lineage_script workload → `analyze_sql_lineage` + 写 results/{run_id}.json 让 HistoryView / LineageReportView 直接展示
- `verifier.py` —— actual vs yml expected 回归校验，5 状态（pass / fail / no_expected / no_task / no_run）；按命名约定 `<scenario_id> · <workload_name>` 从 task_store 反查 task，走 `list_result_history` 拿最近 run summary
- `orchestrator.py` —— `run_all(scenario, datasource_id, *, ai_fill)` 6 步串成一次调用（fill → generate → materialize → record → run tasks → verify），CI 友好（POST 一次 endpoint 看 `.ok` 字段判定）

慢 SQL 分析另立 `app/services/slow_sql.py`：`analyze_sql` 跑 EXPLAIN + 4 条 MySQL plan 规则推断（full_table_scan / filesort / using_temporary / high_row_scan）；`enrich_via_ai` 把规则结果 + plan 喂给 LLM 复核 + 补漏 + 给 DDL + 对比 yml `expected_optimizations` 算 coverage_pct（≥80 绿 / 40-80 黄 / <40 红）。`POST /api/slow-sql/analyze` + `/enrich` 两端点。

API endpoint 全集（`app/api/scenarios.py` + `slow_sql.py`）：`GET /api/scenarios` / `GET /api/scenarios/{id}` / `POST /api/scenarios/{id}/materialize`（含 `ai_fill: bool`）/ `POST /api/scenarios/{id}/record` / `GET /api/scenarios/{id}/verify` / `POST /api/scenarios/{id}/ai-fill` / `POST /api/scenarios/{id}/run-all` / `POST /api/slow-sql/analyze` / `POST /api/slow-sql/enrich`。前端单视图 `views/admin/ScenarioSandboxView.vue` 覆盖全套交互（~31 kB chunk / 9 kB gzip），sidebar Beaker 图标 + `/admin/sandbox` adminOnly + lazy load 路由。

详见下方[Phase 12 章节](#phase-12--ai-测试沙盒2026-05-12--05-1313-commits--202-tests)。

## 关键设计决策

- **流式对比模式**（`limits.stream_compare = true`）：不将全部行加载到内存，通过有序迭代器流式归并。要求两边的 SQL 已按主键排序。
- **单 SQL 模式 vs 双 SQL 模式**：单 SQL 模式下 `source_sql` 在源端和目标端各执行一次；双 SQL 模式 `source_sql` 和 `target_sql` 分别独立执行。
- **`CompareRules.column_mappings`**：允许在对比前将源端和目标端不同名的列映射对齐。
- **`CompareRules.schema_policy`**：`warn`（默认）/ `strict`。strict 时检测到 source / target 列不一致直接 `raise ValueError`。
- **AI 输出永不替代规则结论**：6 不变量（不动 graph_edges / 输出落独立字段 / 白名单约束 / confidence 不允许 high / 前端徽章区分 / 默认关闭可降级）。
- **测试数据**：`init_db/01_init.sql` 中的 `users / users_archive` 和 `orders / orders_v2` 表有意设计了差异，用于演示对比结果。

## 路线图

整体路径：**血缘稳定 → 多来源对比 → 作业流 → 工程治理 → 血缘语义增强 → 领域模型收口 → 平台级血缘架构 + 观测性 → AI 测试沙盒 → 安全加固(v0.2.0) → 可用性收尾(已完成)**。

当前测试基线 **1589 通过 / 0 失败 / 1 skipped**（本地 pytest 全量,排除 2 个慢 lineage 集 + 34 个 frontend vitest 全过 + npm typecheck/build 全绿）。Phase 9 + Phase 10 全程交付：领域 schema 集中、AI 包独立、inference 异步化、错误响应统一、全局搜索、服务端 graph query、全局 lineage 索引、资产详情页 + custom aspects + 变更轨迹、字段列表 + 字段血缘热点 + datasource introspection、aspect governance dashboard、lineage 节点徽章、Prometheus `/metrics` + 结构化日志、路由 lazy loading、生产就绪闭环（ErrorBoundary + healthcheck + RUNBOOK）、`/api/v1/` 版本化前缀全部完成。Phase 11 落地：方言模块化 spike（3 commit / 11 tests）、字段血缘多跳追溯 + procedure refresh mode 深化、4 处 unbounded cache 收口（前后端各 2）、trace-compare 后端 MVP（13 tests）。**Phase 12 「AI 测试沙盒」18 个 commit 全部交付**：scenario DSL + generator + materializer + recorder + admin UI + slow-sql 规则分析 + AI 复核 + AI filler + regression verifier + 一键链 orchestrator + lineage_script workload + Oracle/DM 方言扩展 + verifier tolerance + SQL 模板变量 + slow-sql Oracle EXPLAIN PLAN + AI filler v2 分布参数 + CI scenario lint + 夜间回归 workflow 模板，详见下方 Phase 12 章节。**v0.2.0 安全加固 27 commits 收工**:MFA + recovery codes + refresh rotation + reuse detection + rate limit + HttpOnly cookie + audit enrich + 自签 HTTPS 部署。**Phase 13 可用性收尾 6 项 3 commits**:Oracle/DM/DB2 callTimeout + JobInfo 三字段 + RunLimits.query_timeout + mid-run 水位 + per-run 配额,详见 Phase 13 章节。


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

- ✅ **Procedure refresh mode 语义模式深化**（2026-05-09 落地）—— 修了「procedure 体内 TRUNCATE → INSERT 没识别成 truncate_insert」的 bug：顶层 `parse_lineage_statements` 在 procedure 解析失败回退时用 `extract_analyzable_segments` 拆 `;`，但拆出来的 statements 顺序跟源 SQL 不一致，导致 `_has_followed_by` 判断 TRUNCATE 在 INSERT 之后。修法：`aggregation.py` 加 `collect_procedure_operations()` 直接走 `procedure_segments`（已按 line_start 排序）独立产 ops，每 op 带 `procedure_name`；analyzer 把顶层 ops（去掉 procedure-内重复）+ proc ops 合并喂入 `aggregate_target_summary`。dedup 用「sqlglot parse → 再序列化」做 canonical 比较，避免 `ods.orders o` vs `ods.orders AS o` 字符串差异错配。`_has_followed_by_within_scope` 替代 `_has_followed_by`，让先后顺序在同一 procedure 内成立才算（避免 proc1 truncate / proc2 insert 跨过程巧合）。`TargetSummary` 加 `procedure_origins: list[str]` 字段让 UI 能展示「此表被 procX / pkg.refresh_daily 重刷」。`semantic._build_targets` 透传 `procedure_origins` 到 `semantic_lineage.targets[*]`，前端 `SemanticLineagePanel` 表格新增「由谁写入」列：有 origins 时按 chip 显示过程名（`<anonymous>` 渲染为「匿名块」），空时显示「顶层」。补 7 个测试覆盖 procedure-only TRUNCATE+INSERT / DELETE+INSERT / 匿名块 / dedup 不双重计数 / 纯顶层无 origins / semantic_lineage.targets 透传。回归 917 → 924
- ✅ **字段血缘 tracing UI 多跳**（2026-05-09 落地）—— `services/assets.get_column_lineage()` 加 `depth` / `max_nodes` 参数，重构内部为「先建 edge index 再 BFS」。`depth=1`（默认）保留旧 shape；`depth>=2` 每个 item 多带 `hop` / `from`，cycle 切断 + max_nodes 截断。`/api/assets/column-lineage` 加 `depth` `max_nodes` query。前端 `AssetDetailView` 字段展开行加 1/2/3 跳 picker；多跳场景按 hop 缩进渲染 chip，每个 hop≥2 chip 显示 `← from parent` micro-label 让用户追溯路径。补 6 个测试（depth=2 上下游 / depth=1 向后兼容 / max_nodes 截断 / cycle 切断 / endpoint depth 参数）。回归 911 → 917。**后续小补**：response 多 `upstream_truncated` / `downstream_truncated` / `max_nodes` 字段；前端在该方向用 amber banner「⚠ 已达上限 N 节点，可能漏链路」提示用户结果不全。**性能优化**：column edge index 加 TTL+run_count 缓存（跟 `lineage_index.py` 一个套路）—— 反复点字段血缘（同一 AssetDetailView 上多字段、多跳切换）只首次扫所有 run，后续走 cache。`invalidate_column_edge_index_cache()` 在 `isolated_storage` fixture 调一下避免跨测试 tmp_path 切换污染。补 3 个缓存测试

**Phase 11 候选 · 数据对比 × 血缘联动**（2026-05-08 立项 / 2026-05-10 后端落地 / 2026-05-11 MVP 三步全部完成）—— 把 Compare 和 Lineage 两套独立能力拼成「沿血缘逐层对比 → 定位数据偏离层」的诊断工具：

- **动机**：用户报「dws 报表数 ≠ ods 源」时，目前要人手逐层建 compare task + 串 workflow，痛点高。已有 `services/assets.get_column_lineage()` 字段链 + `CompareTask` 多源对比 + `workflow_engine` DAG 三件套都成熟，差一个编排器
- **MVP 路径**（最小可演示切片，先不啃聚合）：
  1. ✅ **后端 `POST /api/lineage/trace-compare`**（commit `c800f0b`）—— body `{table, column, key_column, base_task_id, sample_keys, datasource_map, per_table_keys, depth, project_id, run_limit}`。`services/trace_compare.py` 走 `get_column_lineage(depth=N)` 拉链，把每条 (upstream → downstream) 边变成一个 compare 节点：填 `task_id` (caller 提供 shell-task) / `source_sql_override` / `target_sql_override` / `key_columns_override`，复用 compare 节点已有的 override 通道。`sample_keys` 走 `IN (...)` + `ORDER BY key`（流式 compare 要求两端同序），`datasource_map` 缺一表→该 hop 标 `unmapped_tables` + 顶层 `warnings`，`per_table_keys` 按表覆盖 PK，标识符走白名单正则拦 SQL 注入，depth clamp [1,10]。返回 `{focal, chain, workflow_draft, warnings, stats}`，caller 拿 draft 可直接 POST `/api/workflows`。**只做 `direct` 策略**（assume 上下游列值一致），聚合 / 过滤 / 类型转换的口径检查留给 AI 兜底或下一切片。13 个新测试覆盖单跳 / 多跳 / sample_keys 字面量 / unmapped 警告 / per_table_keys / 标识符校验 / depth 边界 / 401/400/200 endpoint
  2. ✅ **前端「✨溯源」按钮**（commit `4455695`）—— `components/lineage/TraceCompareModal.vue` 4 步流程：填 PK 字段 / 采样 PK 值（逗号分隔，纯数字识别为 number 其它走 string）/ 追溯深度 / base task 选择 → 点「预览 chain」拿 trace-compare 返回（先空 `datasource_map`），后端响应里 `_trace_compare.datasource_source/target` 反向预填 datasourceMap → 用户给链上各表选 ds（缺的标黄）→ 点「保存为作业流」用最终 map 再调一次 trace-compare → POST `/api/workflows` 保存 → 跳 `/workflows/:id`。AssetDetailView 字段表格行加紫色「✨溯源」chip（`isEditor` + `lineage_known` 才显示）。附「复制 draft JSON」按钮 + accordion 展开看每节点生成的 source/target SQL。独立组件 412 行不嵌进 941 行的 AssetDetailView，build 通过 chunk 体积几乎不变（37.50→37.45 kB）
  3. ✅ **跑完结果链式着色**（commit `4225c39`）—— `components/lineage/TraceCompareSummary.vue` 检测 `workflow_run.nodes[*].config._trace_compare` meta，每跳渲染一个 mini card，按节点 `output.summary` 判定 verdict：绿 = 全 same，红 = `only_source/only_target/diff` 任一 > 0，灰 = 未运行/失败/跳过。card 含 hop 编号 + 上下游字段 + 4 个计数（same/diff/L/R）+ click → 跳节点详情。**诊断信号**：右上角红 chip「首次偏离 hop N」（倒序扫边，最远 upstream 起找第一个 diff 边——污染源精确定位）。挂在 `WorkflowRunView` 顶部状态卡片下、gantt 之上；自检 `isTraceRun = edges.length > 0`，非 trace-compare run 完全不渲染，不影响普通 run 体验
- **Tradeoff 核心**：transform 类型的非 1:1 性 —— 聚合（GROUP BY / SUM）和过滤（WHERE）不能行级 diff，要么按 join key 重算 + 区间对比、要么退化口径检查。这部分**适合 AI 兜底**（让 LLM 看 transform SQL 决定每跳对比策略，比纯规则更合适，且复用已有 enrichment provider 抽象）
- **MVP ADR 摘录**：
  - 编排器**不持久化** workflow —— 返回 draft 字典，让 caller 决定保存或临时跑。这样后端只管「lineage 链 → compare 节点」纯转换，UI 集成时再决定 UX。
  - compare 节点用 **shell-task 模式**（caller 提供一个 `base_task_id`，所有节点都覆盖它的 SQL/keys）—— 跟现有 `compare.py` line 27-44 的 override 路径已支持，不动 compare 节点 schema。
  - 各 hop **默认无 `depends_on`** —— 它们是逻辑上独立的两端 compare，让 engine 并发跑更快出结果；UI 后续可选择性加链让用户分步看。
  - 标识符校验放服务层而非端点层 —— 即使前端走 lineage 反查给的表名也防一手；`base_task_id` 不校验（让后续 jobs 层的 task 存在性检查接管）。
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

### Phase 12 · AI 测试沙盒（2026-05-12 ~ 05-14，18 commits）

把 DataOpsStudio 从「真实库面板工具」扩展成「自带测试沙盒的 AI 数据治理平台」。yml 描述一个 scenario（表 schema + 偏差注入 + 工作负载消费），系统能自动 ① LLM 给业务化数据 ② 落到 demo MySQL/Oracle ③ 建对比任务 ④ 跑 EXPLAIN 分析慢 SQL ⑤ AI 复核覆盖率 ⑥ 验证实际 diff 跟预期一致。让对比 + 血缘 + slow-sql 三大模块的回归测试 / 演示数据 / 真实案例一站式生成。

整体架构：`app/scenarios/` 包独立编排，前端顶级视图 `/sql-optimize`(Phase 14 P0-1 前是 admin 视图 `/admin/sandbox`,老路径 301 重定向兼容) 端到端可视化。`app/services/slow_sql.py` 单独建慢 SQL 分析层（依赖 dbclients 跑 EXPLAIN）。前端 `SqlOptimizeView.vue`(原 `views/admin/ScenarioSandboxView.vue`)单视图覆盖 ai_fill / materialize / record / verify / run-all / slow-sql analyze + AI enrich 全套交互。

**切片 1-4：scenario DSL + 数据落地 + 对比任务生成**（commits `f6e865e` / `185752b` / `77e5b0a` / `140fd74`）—— `app/scenarios/models.py` Pydantic 定义：Scenario / TableDef / ColumnDef / IndexDef / ColumnOverride / AnomalyDef / WorkloadDef + AISettings / DomainDef，`extra='forbid'` 拦 yml 笔误；anomaly / workload 子项 `extra='allow'` 因 kind-specific 字段太多列 Literal 反失控。7 个 column generator（uuid_short / random_int+zipf / realistic / timestamp / enum+weights / constant / sequence+prefix）+ 6 个 anomaly kind（missing_rows / extra_rows / value_drift±X% / null_drift / duplicate_pk / type_mismatch），seed 决定性复跑同结果。`materializer.py` 纯函数 `build_materialize_plan` 产 DDL + INSERT 计划，`apply_plan` 接收最小 `SqlExecutor` 协议（pymysql cursor / mock 都能包），caller 自管事务边界。`recorder.py` 把 compare_task workload 翻译成 `CompareTaskCreate` 走 task_store.create，SELECT 显式列名 + ORDER BY pk + sql_mode=DOUBLE（source/target 同一 datasource）。`runtime.py` datasource_id → pool.borrow + cursor → apply_plan + commit，IO 边界单独一层。API 4 端点：`GET /api/scenarios` / `GET /api/scenarios/{id}` / `POST /api/scenarios/{id}/materialize` / `POST /api/scenarios/{id}/record`。第一个 example.yml `orders-recon-mvp` 含 2 表 / 4 anomaly / 3 workload（compare_task + lineage_script + slow_query）。`config/scenarios/*.yml` gitignore，`*.example.yml` 跟踪。

**切片 5：admin 沙盒视图**（commit `79d6429`）—— `frontend/src/views/admin/ScenarioSandboxView.vue` + `/admin/sandbox` 路由（adminOnly + lazy load）+ sidebar Beaker 图标 + i18n adminNav.sandbox（zh/en 双语）。两栏布局：左 scenario 列表卡片（坏文件单独 warning 区，不让一份坏 yml 把列表打没），右选中后渲染 tables / anomalies / workloads 三栏概览 + datasource picker（仅过滤 MySQL）+ 「生成数据并落库」+「建对比任务」两个 action button。结果以绿色 / 紫色卡片粘在面板下方：materialize 显示每表 rows_generated vs rows_inserted + indexes_created；record 显示 task 列表，每行「打开任务 →」跳 `/data-compare?task_id=`。

**切片 6-8：slow-sql 规则分析 + UI + AI 复核**（commits `1c8d981` / `43e8e26` / `1563529`）—— `app/services/slow_sql.py` 走 `validate_readonly_sql` 拦 DML/DDL，自己 prepend `EXPLAIN`，按 4 条 MySQL plan 规则触发 issue + suggestion：`type=ALL` → full_table_scan / `Extra` 含 filesort → filesort / `Extra` 含 `Using temporary` → using_temporary / `rows>10000 && type in (all,index)` → high_row_scan。`build_suggestions` 按 issue code 派生建议，同表同类 dedup 避免噪音。`POST /api/slow-sql/analyze` 返回 `{dialect, explain_sql, plan, issues, suggestions}`。前端 sandbox 视图 slow_query workload 行加紫色 🔬「分析」按钮，结果以独立 card 左右两栏展开：左 yml `intentional_issues` + `expected_optimizations`（设计意图），右后端规则推断 + 原始 plan 表格。AI enrichment：`enrich_via_ai(sql, plan, issues, suggestions, expected_optimizations)` 走 lineage_ai provider 抽象（off / mock / openai / anthropic / ollama），system prompt 4 件套（复核规则 issues / 补漏 / 给 DDL/SQL 改写 / 对比 expected 算 coverage_pct），防御性处理：LLM 返非 dict / null / 单 dict 都过滤；coverage_pct 缺失按 matched/expected 反算；非法 pct（>100, <0, "abc"）clamp [0,100]；plan > 4000 chars 切半防 token 爆；provider=off 时返 ok=False + 占位字段（200 降级，避免误以为接口坏了）。`POST /api/slow-sql/enrich` 端点；前端「✨ AI 复核」紫色按钮跑完后顶部 pill「覆盖 67%」≥80 绿 / 40-80 黄 / <40 红，verdict 三态彩色 pill（confirmed / false_positive / insufficient_info），AI 补充建议 confidence pill + 折叠 SQL 代码块。

**切片 9：AI filler**（commit `3a02ec2`）—— `scenario.ai.fill = [column_values, table_descriptions]` 真正发力。`fill_scenario(scenario) → (filled, FillReport)` 纯函数：含 `column_values` 时对 `gen=realistic` 且 `values` 空的列调 LLM 拿 20~30 个真实业务样本（payload: table_name + col_name + col_type + domain.vertical + domain.hint），写进 col.values；含 `table_descriptions` 时对缺 description 的表补一句中文。`max_calls` 默认 50 防大 scenario 烧 token；单字段调用失败不影响其它（errors per-field 累积）；dedup values + 截 30；description 截 60 chars 防 LLM 长漂；provider=off / ai.fill 空 → 返回原 scenario + skipped_reason。generator `_realistic_value` 加 fast path：`col.values` 非空时直接 `rng.choice(values)`，否则走原类型嗅探 fallback —— **没 AI 时一切照旧**的不变量。`POST /api/scenarios/{id}/ai-fill` 独立预览；`materialize` 端点加 `ai_fill: bool` 参数后整条 fill→generate→insert 走通，summary 多 `ai_fill` 子字段报告 LLM 用量。前端 materialize 卡新增 ✨「AI 填血肉」复选框 + 成功卡片紫框子卡显示「N 个 LLM 调用 · 填了 X 列样本池 + Y 表描述」+ list filled_columns。

**切片 10：regression verifier**（commit `2ad2711`）—— scenario yml 的 `expected: {only_source, only_target, diff, same}` 块从摆设升级成 ground truth，admin 跑完对比任务后调 `GET /api/scenarios/{id}/verify` 自动对比 actual summary vs expected，5 个状态：`pass / fail / no_expected / no_task / no_run`。命名约定按 recorder 规则（`<scenario_id> · <workload_name>`）从 task_store 反查 task，再走 `list_result_history` 拿最近一次 run summary，精确匹配（所有 expected 字段值 == actual → pass）。`actual` 漏字段当 0 算不抛，delta = actual - expected 反映完整差距；`project_id` 非空时按项目过滤。前端 「🛡 回归校验」按钮（独立于 datasource，校验是纯读），结果以三色 pill（pass 绿 / fail 红 / skipped 黄）+ 4 列字段对比卡，delta=0 绿、≠0 红 + "(±N)" 后缀。no_run 显示「task 未跑过」+ 跳工作台链接；no_task 提示「点建对比任务」；no_expected 提示「补 yml expected 块」。

**切片 11：one-shot orchestrator**（commit `4754efc`）—— sandbox 5 个独立按钮合成一个「🚀 一键全套」单按钮 / 单 endpoint。`app/scenarios/orchestrator.py` `run_all(scenario, datasource_id, *, project_id, drop_first, batch_size, ai_fill)` 6 步串：ai_fill (optional) → generate + materialize → record → runner.run_task per task → verify_scenario。短路语义：materialize 失败 → 短路；ai_fill 失败 → 记错但 pipeline 继续（用原 scenario 走下游）；每 task run 独立 ok 字段。整体 ok 规则：任一 run.ok=False 或 verify.summary.fail>0 → False。`POST /api/scenarios/{id}/run-all` 一次拿组合 report，CI 友好（`curl ... | jq '.ok'` 一行判定）。前端紫色 primary 按钮 + 跑完顶部 banner（绿 / 红 border-2）一句话「全套通过 / 有失败步骤」+ 5 项精简计数（AI 填 N 调用 / 落库 X 表 / 建任务 Y / 运行 M/N ok / 校验 P pass · F fail · S skipped），同步 materialize / record / verify 三块到各自分步面板，失败 run 列表展开 + error message。

**切片 12：lineage_script workload → analyzer + history JSON**（commit `3a42ca7`）—— example.yml 第二条 workload 接通。`_record_lineage_scripts` 迭代 `kind=lineage_script` workload，每条调 `analyze_sql_lineage(sql, dialect)`（lazy import 避免 sqlglot 启动开销），写一份 history JSON 到 `results/{run_id}.json`，run_id 格式 `lineage_script_<YYYYMMDDHHMMSS>_<8hex>`，含 type / sql / dialect / 完整 analyzer 输出（table_edges 必含，让 `_classify_result` 落 type=lineage）。`record_scenario` 返回多 `lineage_runs` 字段（向后兼容）；单条 lineage_script 失败（缺 sql / analyzer 抛错）不影响其它 + 不阻塞 compare_task 创建。orchestrator report.record 同步带 lineage_runs。前端 record 卡展开多一节「血缘脚本入库（N）」，每行 ✓/✗ pill + workload_name + run_id 后 8 位 + 「查看历史 →」按钮跳 `/history?type=lineage`。example.yml 3 类 workload 全部接通：compare_task → CompareTask（切片 4）/ slow_query → admin 即时分析（切片 6+8）/ lineage_script → analyzer + history（切片 12）。

**切片 13：materialize dialect abstraction（Oracle / DM 扩展）**（commit `4876301`）—— materializer 从「mysql-only NotImplementedError」升级成方言可插拔。`app/scenarios/dialects/` 包：`base.MaterializeDialect` ABC（4 个必覆盖抽象：quote_identifier / schema_create_sql / drop_table_sql / placeholder + 3 个默认实现：quote_qualified / create_table_sql / create_index_sql / insert_sql）；`mysql.MysqlMaterializeDialect`（` 标识符 / CREATE DATABASE IF NOT EXISTS / DROP IF EXISTS / %s 占位符）；`oracle.OracleMaterializeDialect`（" 标识符 / `schema_create_sql→None` 因 Oracle schema=user 不擅自建 / DROP 包 PL/SQL 异常块吞 ORA-00942 / `:1, :2, ...` 编号占位符接 cx_Oracle / oracledb / dmPython）。`__init__.get_dialect(name)` 大小写不敏感 + DM→Oracle 实例复用（DM 跟 Oracle 在 DDL / PL/SQL / 数据字典都兼容）。materializer.py 移除 mysql 硬编码 helpers，build_materialize_plan 用 `get_dialect(scenario.dialect)` 派发；`_build_indexes` 改吃 dialect 参数走 `create_index_sql`。recorder build_compare_tasks 也接 dialect —— Oracle scenario 生成的 SELECT 自动用 " 引用（之前会用 mysql 反引号让 Oracle 报错）。**唯一未支持的是 slow-sql Oracle EXPLAIN PLAN 解析**（Oracle 走 `DBMS_XPLAN.DISPLAY`，输出格式跟 MySQL 列式 plan 完全不同），留下个切片。

**Phase 12 ADR 摘录**：
- DSL 三层独立扩展（tables / anomalies / workloads）—— 加新 anomaly kind 只动 Literal 闭集 + 注册一个 generator 函数，不动 Scenario 模型
- 结构 deterministic / 内容 AI fill —— template 控 schema shape，LLM 只在 ai.fill 白名单字段里填业务血肉
- materialize 用「最小 SqlExecutor 协议」而非具体 cursor 类型 —— mock / pymysql / oracle 都能包，build_plan 完全无 IO
- run-all 短路语义：materialize 失败必短路（后续都没数据），ai_fill 失败不短路（用原 scenario 继续），run_task 失败不短路（其它 run 仍跑）
- AI 三处都遵守「provider 关 → 200 降级 + ok=False」不变量，从不抛 4xx 让普通用户误判接口坏了
- scenario.expected 用精确匹配（不上来加 tolerance）—— actual ≠ expected 必 fail，避免「±5%」让真 bug 漏网；将来加 tolerance 走显式字段
- dialect 抽象只放真正分叉的能力（标识符 quote / schema 是否可建 / 占位符 / DROP 安全语义）；DDL 形态在基类给默认实现，多数方言不用 override

**Phase 12 端到端用法**（admin 视角，30 秒）：
1. `/sql-optimize` 选 scenario + datasource（MySQL / Oracle / DM）+ 勾 ✨ AI 填血肉
2. 点 🚀 一键全套 → 绿色 banner「全套通过」+ 5 项计数（AI 填 N 调用 · 落库 2 表 · 建任务 1 · 运行 1/1 ok · 校验 1 pass）
3. slow_query workload 行单独 🔬 分析 + ✨ AI 复核 → 顶部「覆盖 67%」徽章 + 三栏对比（设计期望 / 规则实测 / AI 复核）
4. record 卡里点「打开任务 →」/「查看历史 →」一键跳工作台 / HistoryView

**CI 友好的 API 调用**：
```bash
curl -X POST /api/scenarios/orders-recon-mvp/run-all \
  -d '{"datasource_id":"demo-mysql","ai_fill":true}' \
  | jq '.ok'
# true / false 一行判定，配合 GitHub Actions / Jenkins 当 nightly 回归 fixture
```

**切片 14-17（2026-05-13 ~ 05-14，4 commits）—— Phase 12 收尾 enhancement**：

- **切片 14：verifier tolerance**（commit `6481921`）—— scenario `expected` 块从精确匹配升级成可容差。`WorkloadVerifyResult.tolerance: dict[str, int]`；匹配逻辑从 `act != exp` 改成 `abs(delta) > tol`。workload 子项透传 `tolerance`（标量 → 所有字段 / 字典 → 按字段）+ `tolerance_pct`（按 expected 值百分比），`_resolve_tolerance` 把绝对值 + 百分比按字段取 max 合并，负值 clamp 0。修的痛点：anomaly `fraction × 行数` 取整误差（`0.005 × 985 = 4.925→4`）让 verifier 报假阳性。+7 tests
- **切片 15：SQL 模板变量**（commit `435ea60` + `e1dcad9`）—— `app/scenarios/templating.py` 新模块：`render_template(sql, variables)` 把 workload.sql 里 `{{name}}` 占位符替换成 `Scenario.variables[name]`，缺失变量原样保留 + 收 `missing` 列表。`Scenario.variables: dict[str, str|int|float|bool]` 新字段。`recorder._record_lineage_scripts` 渲染后再喂 analyzer，history entry 多 `variables_substituted` / `variables_missing`。仅匹配标识符形态（不跟 Jinja 混）。前端 `renderSql()` 镜像同正则；模板变量面板用 HTML entity 显示字面 `{{name}}`（Vue mustache parser 会把 `{{ '{{name}}' }}` 当未闭合字符串报错）。+13 tests
- **切片 16：slow-sql Oracle / DM EXPLAIN PLAN**（commit `487554d`）—— `analyze_sql` 按 datasource 类型派发：MySQL 走单条 `EXPLAIN`，Oracle/DM 走 `EXPLAIN PLAN SET STATEMENT_ID FOR ... → SELECT FROM PLAN_TABLE` 两步（DM 复用 Oracle 路径，PLAN_TABLE 协议一致）。`detect_oracle_issues` 6 条规则（full_table_scan / sort_order_by / sort_group_by / nested_loops_high_card / high_cost / high_row_scan）+ `build_oracle_suggestions` 6 类建议。`enrich_via_ai` 加 `dialect` 参数透传给 LLM prompt（提示 Oracle PLAN_TABLE 字段 vs MySQL EXPLAIN 字段差异）。`SlowSqlEnrichRequest` / `/enrich` endpoint 接 `dialect`。+23 tests
- **切片 17：AI filler v2 分布参数**（commit `f0782b4`）—— `realistic` 列从「均匀抽样样本池」升级成「真实概率分布」。`ColumnDef.dist_params: dict | None` 新字段，generator `_realistic_value` 优先级改 `dist_params > values > 类型 fallback`。`_sample_distribution` 支持 4 分布族：lognormal（金额 / 时长右偏长尾）/ normal（年龄 / 评分对称）/ uniform / exponential（间隔），min/max 对非 uniform 起 clamp；`_round_for_type` 按列类型收敛（INT → int / `DECIMAL(p,s)` → s 位 / FLOAT → 4 位）。ai_filler 加 `column_distributions` fill scope + `DISTRIBUTION_PROMPT`，对 realistic 数值列问 LLM 要分布参数（校验 kind 闭集 + 只保留已知数值键），写进 `col.dist_params`；非数值 realistic 列跳过。`FillReport.filled_distributions` + 三处 report dict（materialize / ai-fill / run-all endpoint）+ 前端 ai_fill 卡展示。同 seed 同分布输出。+26 tests
- **切片 18：CI scenario lint + 夜间回归 workflow 模板**（commit 本次）—— `scripts/scenario_lint.py` 纯 Python 静态体检（不连库 / 不调 LLM）：① loader 跑通（DSL 校验）② `generate_scenario()` 内存冒烟，catch 未知 dist kind / 坏 range / anomaly 配错列等运行期才炸的 bug ③ 交叉引用（anomaly.table / derives_from / column_overrides.from / compare_task source·target 都存在）④ workload.sql `{{var}}` 都能在 `scenario.variables` 找到。`lint_scenarios(dir) → LintReport` 纯函数 + `main()` CLI（`--dir` / `--strict` 把 warning 也算失败）。`ci.yml` 的 `backend-tests` job 加 `python scripts/scenario_lint.py --strict` step。`.github/workflows/scenario-nightly.yml` —— DB-backed 夜间回归模板（`workflow_dispatch` 触发 + 注释掉的 `schedule`）：docker compose `--profile demo-db` 起 app + demo MySQL → 登录拿 token → 注册 datasource → 逐个 scenario POST `run-all` 用 `.ok` 判定。+19 tests

**Phase 12 全部交付，无剩余 enhancement。** 长期 backlog：AI filler v3 接 Faker locale / lineage_script 模板变量做条件分支 / 把 scenario-nightly.yml 的 schedule 取消注释转正。

### Phase 13 · 可用性收尾（2026-05-23,3 commits / 6 项）

deep-research 报告(`G:\work\deep-research-report.md`)外审 DataOpsStudio 把「可用性安全」列为最大缺口,要求 7 项 P0/P1 补丁。先做 audit 实情比对 —— **绝大多数已在 `f24dfe7` 那波落地**(只是默认 warn 模式),实际剩余 6 项收口为 Phase 13:

- **切片 1：Oracle / DM 语句超时**(commit `f480418`)—— `Dialect.apply_call_timeout(conn, sec) -> bool` 走 `connection.callTimeout` 毫秒;`OracleDialect` 实现(`conn.callTimeout = int(sec*1000)`,oracledb / cx_Oracle 全支持);`DmDialect` 继承(dmPython 多数版本兼容,setattr 失败 try/except 吞)。`factory._apply_statement_timeout(cursor, db_type, connection=None)` 双路径派发:优先试 `dialect.apply_call_timeout(conn, sec)` 返 True 即生效,失败 fallback SQL 路径(MySQL 走);caller 不传 connection 时直接走 SQL(向后兼容)
- **切片 2：JobInfo 三字段补全**(commit `f480418`)—— `owner_user_id` / `project_id` / `target_run_id` 落 `JobInfo` model;`jobs.py submit_task_run` / `submit_workflow_run` 加 keyword-only kwarg;4 个 API caller(`tasks.py` / `workflows.py` / `workflow_runs.py` / `scheduler.py`)落字段。`target_run_id` 在 success 分支从 `result.run_id` getattr 抓。authz 不变(仍走 task/workflow lookup),数据模型卫生 + 后续 audit / 告警直接读字段
- **切片 3：RunLimits.query_timeout_seconds 单任务覆盖**(commit `f480418`)—— `RunLimits` 加可选 `int` 字段(范围 [0, 86400])。`factory.py` ContextVar `_query_timeout_override` + `query_timeout_override(sec)` context manager。`runner.run_task` 入口包 `with query_timeout_override(task.limits.query_timeout_seconds)`,下游 fetch_rows / iter_rows / fetch_column_details 三处自动取这个值而非全局 env。慢但合法的 ETL 提到 1800s,日常 preview 任务缩到 60s
- **切片 4：mid-run 磁盘水位检查**(commit `f480418`)—— `resource_guard.DiskWatermarkExceeded(RuntimeError)` + `check_disk_critical(config=None) -> tuple[bool, str | None]`。阈值跟 admission control 共享(`RESULTS_MIN_FREE_GB=5` / `RESULTS_MAX_DISK_USAGE_PERCENT=85`),剩余空间优先报。`runner.py` 双 streaming 分支(`use_stream_compare_to_writer` + `use_streaming_writer`)每写 `_DISK_WATERMARK_CHECK_INTERVAL=5000` 行查一次,critical 即 raise + `_cleanup_partial_parquet(writer)` rmtree 临时 run 目录避免半成品累积
- **切片 5：per-run 磁盘配额**(commit `ef53fe2`)—— `RunLimits.run_disk_quota_mb`(None=无限,范围 1..1048576);`check_run_quota(run_dir, quota_mb)` 累计 `run_dir/**` 字节折 MB。新异常 `RunQuotaExceeded(DiskWatermarkExceeded)` 子类共享 cleanup 路径(caller `except DiskWatermarkExceeded` 一并接住主机水位 + 单 run 配额两种 mid-run 中止)。runner `_check_mid_run_disk(writer, task, rows_written)` 统一入口同时跑两个 check。**per-project 配额**(跨 run 累计)涉及 registry 暂不做
- **切片 6：DB2 语句超时**(commit `d00b1c3`)—— `Db2Dialect.apply_call_timeout` 走 `ibm_db.set_option(conn_handle, {SQL_ATTR_QUERY_TIMEOUT: sec}, 1)` —— `1` 是 SQL_ATTR_CONNECTION 选项类,影响该连接所有后续 cursor.execute。handle 取 `conn.conn_handler`(老版本)/ `conn_handle`(新版本)。**ibm_db 不在 build 默认装** —— `import ibm_db` ImportError 时返 False 安全降级,行为退化为「无超时」与本切片前一致。方言矩阵 4/4 收尾(MySQL / Oracle / DM / DB2 全 ✅)

**测试覆盖**:`test_db_statement_timeout.py` 30(20 已有 + 6 query_timeout_override + 4 DB2)、`test_jobs.py` 8(5 已有 + 3 owner/project/target_run_id)、`test_resource_guard.py` 43(31 已有 + 5 check_disk_critical + 7 check_run_quota)、`test_runner_streaming.py` 13(9 已有 + 2 mid-run abort + 2 quota integration)、`test_sensors.py` 一处 `fake_submit` 补 `**kwargs`(scheduler 现传 owner_user_id/project_id,旧 fake 签名缺 → TypeError 被 try/except 吞 → 假象"没触发")。全套 backend 1589 passed / 1 skipped。

**ADR 摘录**:
- 双路径派发用 `apply_call_timeout(conn) -> bool` + `statement_timeout_sql(sec) -> str | None` —— 连接属性优先,SQL fallback。新方言只 override 真正分叉的一个,基类默认实现兜底
- `RunQuotaExceeded(DiskWatermarkExceeded)` 子类共享 cleanup 路径 —— caller 一个 except 同时接两种 mid-run 中止
- ContextVar 而非函数传参 —— 避免 fetch_rows / iter_rows / fetch_column_details 三个对外 API 签名都加 limits 参数,任务覆盖透明传到所有 DB 调用
- `_check_mid_run_disk` 统一入口 —— 两个 streaming 分支共享 watermark + quota 两个 check,减少重复 if 块
- DB2 ibm_db 不在 build 装 —— ImportError 时 return False 安全降级,不强制装驱动

**云端部署**:三次 bundle 法部署 —— f480418 / ef53fe2 / d00b1c3 各 tick 1 即 healthy,smoke `spa:200 / auth:401` 全绿。具体 IP / SSH / nginx 配置见 memory(per `never_commit_server_login` 硬规则不入 repo)。

**Phase 13 全部交付,无剩余 enhancement。** 长期 backlog:`sql_preflight` 加 EXPLAIN(报告自己说"逐步开")/ per-project 跨 run 配额(registry 设计) / API-worker 分离部署(架构重构,单用户场景过度工程)。

**前端 typecheck**：`ci.yml` 的 `frontend-build` job 跑 `npm run typecheck`。当前 **0 红**(`c1c4616` P0.5 收尾把 14 个 view 全清，157 → 0；Phase 12 切片 17 顺手修了 `ScenarioSandboxView` 一处 `renderSql` 引用未定义 `selected`；2026-05-23 重测 typecheck/build/vitest 全绿)。新增 view 时仍要保持 typecheck 绿。

**通用未做**：

- **字段级血缘解析端深化**：Oracle PL/SQL 深度场景（可视化 ✓ + transform 细化 ✓ + cursor 来源跟踪 ✓ + package 变量声明 ✓ + UDF 调用追溯 ✓ + 变量不污染字段映射 ✓ + procedure/function 不当 fake target ✓ + 显式 cursor 声明 ✓ + cursor 参数化 + INSERT ALL fan-out ✓ + BULK COLLECT/FORALL ✓ + PACKAGE BODY 多嵌套 proc ✓ + variables 渲染 ✓ + TRIGGER 源表 ✓ + 匿名 PL/SQL 块 ✓ + proc-body 局部变量过滤 ✓ + batch report 变量聚合 ✓ + MERGE 列级映射 ✓ + RETURNING INTO 子句剥离 ✓ + CTE 链穿透 ✓ 已落）。S5 PR21：`WITH cte_a AS (... FROM ods.tree), cte_b AS (... FROM cte_a a JOIN ods.names t)` 这种 CTE 链，cte_b body 引用 `cte_a a`，旧逻辑只到 `a` 别名就停。`columns._add_derived_select_columns()` 现场补 local_alias_map（CTE 名也参与别名解析）；`source_info` 在 alias_map.get 后再用 subquery_map 二次查找穿透到底层物理表。S5 PR20：Oracle PL/SQL `INSERT/UPDATE/DELETE ... RETURNING col INTO :var, :var` 让 sqlglot 整脚本解析失败。preprocess 新增 `_strip_returning_into` 用等长空白替换剥离 RETURNING ... INTO 尾巴（保行号），主体 INSERT/UPDATE/DELETE 血缘完整保留。S5 PR18-19：批量报告 `_build_batch_report` 透传 `variables` + `summary.variable_count`，`batch_analyzer` 给每个 file 加 `variable_decls` 完整字典 + 顶层聚合按 (name, kind) 去重带 `file_name` 标记，前端 SummaryPanel 多一列"来源脚本"。MERGE 子句 `merge_table_mappings` 扩展：扫 `WHEN MATCHED THEN UPDATE SET col = src.col` + `WHEN NOT MATCHED THEN INSERT (cols) VALUES (exprs)` 产列级映射（`dml_type=MERGE_UPDATE / MERGE_INSERT`），表级 mapping 仍保留向后兼容。S5 PR17：`v_row ods.orders%ROWTYPE; SELECT INTO v_row FROM ods.orders;` 这种声明，sqlglot 把 SELECT INTO 改写为 CREATE TABLE v_row AS，让 v_row 错落进 tables 列表。新增 `variables.all_plsql_local_names()` 扫所有 PROCEDURE 体 IS/AS 段 + DECLARE 块 + PACKAGE BODY 顶层声明，得到全脚本局部变量名集合：(1) 注入 PR5 的 source_info 过滤集（v_row.id 不被误归到表）(2) 后处理 flat_tables 过滤掉 sqlglot 误识别的变量名表。proc_local 不进 result.variables 列表，前端面板仍只显示 package/declare 有效变量。S5 PR16：顶层 `DECLARE ... BEGIN ... END;` / `BEGIN ... END;` 没 CREATE 前缀，sqlglot 整脚本解析失败。新增 `_RE_ANON_PLSQL_BLOCK` 在 extract_procedure_segments 头部先扫，落在 CREATE 范围外的当 `procedure_kind=ANONYMOUS` 处理 —— cursor 解析、变量提取、TRIGGER 源表等所有 PR1-15 能力都对匿名块生效。S5 PR15：`CREATE TRIGGER trg AFTER INSERT ON ods.orders ... BEGIN INSERT INTO dwd.audit_log VALUES (:NEW.id); END;` 这种 trigger 的 INSERT 过去只看到 `dwd.audit_log` 一端没源。新增 `_RE_TRIGGER_SOURCE` 抽 `[BEFORE|AFTER|INSTEAD OF] event(s) [OF cols] ON <table>` + `procedure_segments[*].trigger_source` 字段 + `_trigger_supplemental_edges()` 补 `ods.orders → dwd.audit_log` 边（`edge_type=TRIGGER` / `confidence=medium`）。S5 PR14：把 PR3 抽出来的 PL/SQL 变量列表（package_constant/variable + declare_constant/variable + 模板变量）透传到 `report.variables` + `report.summary.variable_count`，前端 LineageSummaryPanel 在 8 卡片下方渲染变量表格（变量名 + kind 友好标签 + assigned_value）—— PR3 的后端工作终于在前端可见。S5 PR13：旧逻辑只识别 PACKAGE BODY 第一个 PROCEDURE，多 proc 后续都漏。新增 `_find_nested_proc_scopes()` 在包体范围内扫所有 `PROCEDURE/FUNCTION name [(params)] [RETURN type] IS|AS BEGIN ... END;`，每个独立 scope；包级 cursor 声明合并进每个嵌套 proc 的 declaration_region 共享。procedure_name 限定为 `pkg_name.proc_name`。S5 PR9：`SELECT BULK COLLECT INTO v FROM tabA; FORALL i ... INSERT INTO tabB VALUES (v(i).col)` 这种 PL/SQL 数组中转模式：`_bulk_collect_supplemental_edges()` 建立 var → source_tables 映射，扫 INSERT/UPDATE/MERGE 段是否引用同名 var(<idx>)，补 supplemental 边（`edge_type=BULK_COLLECT` / `confidence=medium`）。S5 PR4：`INSERT INTO X VALUES (pkg.fn(...))` 这种调用 UDF 的 DML 语句，过去 INSERT 自身 source_tables 是空 → 无血缘边。新增 `_udf_supplemental_edges()`：从 procedure_segments 的 `procedure_kind=FUNCTION` 段提取 udf_reads（fn 名 → 函数体 SELECT 的源表），扫每个 statement SQL 看引用了哪些已知 UDF + DML target 是什么，补 source → target 边（`edge_type=UDF_CALL` / `confidence=medium`），CREATE FUNCTION 自身定义语句跳过避免误补。S5 PR5：`SELECT g_app_id FROM ods.orders` 不再把 `g_app_id` 误归为 ods.orders 的 source_column —— `source_info()` 接 `variable_names` 集合，无 table 限定的 Column 名落在该集合时跳过，不污染 source_columns/source_tables；mapping `source_type="variable"`（区分纯常量），ods.orders 自动落到 `graph_groups.dependency_tables` 而非 source_tables。S5 PR1：`FOR rec IN (SELECT FROM tabA) LOOP INSERT INTO tabB VALUES (rec.col)` 这种 INSERT 没 source_tables 的场景，靠 `procedure_segments[*].cursor_sources` + `_cursor_supplemental_edges()` 补 `tabA → tabB` 边（`edge_type=CURSOR_LOOP_INSERT` / `confidence=medium` 区分静态推断）。S5 PR2：`_collect_loop_scopes()` 二次扫描 body 找所有 `FOR ... LOOP ... END LOOP` 范围（含嵌套），cursor LOOP 体内多个 DML 段（INSERT / UPDATE / DELETE / MERGE）都继承同一份 cursor_sources（嵌套时取最内层）。S5 PR3：`variables.package_variables(sql)` 抽 `PACKAGE BODY` 顶层和 `DECLARE` 块的常量与变量声明（`g_app_id CONSTANT VARCHAR2(32) := 'JY';` / `v_cnt NUMBER := 100;`），合入 `result.variables` 列表，每条带 `kind` 字段（`package_constant` / `package_variable` / `declare_constant` / `declare_variable`）。PROCEDURE 体内的局部变量不进列表（避免串味）。
- ✅ **TypeScript 渐进迁移**（S3.B 全 10 store + S4.A api.ts + composable + S4.B codegen 落地，2026-05-10 view 迁移 5 个 batch 全部完成）：10/10 store + api.ts（含泛型 apiGet&lt;T&gt; / apiJson&lt;T&gt;）+ useLineageGraphData composable 全部 ts；openapi-typescript 从 /openapi.json 自动生成 `src/types/api-schema.ts`，友好别名在 `src/types/api.ts`（auth / project / datasource / task / workflow 已用）。20 个 view 全部转 `<script setup lang="ts">`，分 5 batch：(1) BatchView / DatasourceView / WorkbenchView / WorkflowView / LineageView —— 加 StepId / SubPageId union；(2) HistoryView / LoginView / WorkflowTemplateView / AuditLogView —— TemplateItem / AuditLogEntry interface；(3) LineageReportView / WorkflowListView / UserManagementView / ProjectManagementView —— TabId / Role union + UserItem / ProjectItem interface；(4) SchedulerMonitorView / LineageWorkbenchView / WorkflowRunView / AIConfigView —— ModeId / Pipeline union、AIConfigPayload / AITestResult interface、`apiGet<T>` 泛型化；(5) AspectGovernanceView / WorkflowDetailView / AssetDetailView 大头（含 906 行的 AssetDetailView）—— AspectTypeSpec / AspectEntry / AspectRecord / AspectHistoryRecord / ColumnEntry / ChainItem / EventTypeMeta interface 全收口。无 template / 行为变更，仅 script 加类型，让后续 view-level 改动有 IDE 提示和编译期校验
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
