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

`main.py` 初始化 FastAPI，挂载 `/static`，并注册来自 `app/api/routes.py` 的唯一路由器（聚合 14 个领域子模块：`system / auth / projects / datasources / tasks / runs / scheduler / workflows / workflow_runs / history / lineage / uploads / config_io / ai_utils`）。新增 endpoint 加到对应子模块；不属于任何领域时新建子模块再 include 进 `routes.py`。

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

**DB 驱动** — `dbclients/drivers.py` 声明各 `DatabaseType` 对应 Python 模块，`dbclients/factory.py` 在连接时动态导入。当前启用：`pymysql + cryptography`（MySQL）、`dmPython`（DM）。Oracle、DB2 为可选。`dbclients/pool.py` LIFO 连接池：每 datasource_id 一个池，`max_size=4`，`idle_seconds=600`，MySQL 路径 acquire 时 `SELECT 1` ping 验活。`extra.disable_pool=true` / 改 host/port 时池自动失效。

### 前端

Vue 3 SPA。状态管理走 **Pinia 渐进引入**：10 个 store —— `notice / datasource / task / workflow / lineage / batch / history / bootstrap / auth / project`。`App.vue` 顶部 `useXxxStore() + storeToRefs`，`provide('app', {...})` 仍 backward compat 把 store 字段平铺给 `inject('app')` 用。新代码直接 `useStore`。

**视图层**：`DatasourceView / WorkbenchView / WorkflowView / LineageWorkbenchView / HistoryView / LoginView` + admin 子路由 5 个（`UserManagement / AuditLog / ProjectManagement / AIConfig / SchedulerMonitor`）。Hash router（`createWebHashHistory`）+ `beforeEach` 守卫读 localStorage 跳 login + adminOnly 守卫拦非 admin。

**作业流视图**按职责拆到 `components/workflow/`：`WorkflowDagCanvas`（SVG 画布）+ `WorkflowSettingsPanel`（元数据 sidebar）+ `WorkflowHistoryPanel`（运行历史 + mini gantt）+ `WorkflowRunNodeDetail`（节点详情）+ 4 个节点编辑器（params/compare/lineage/excel_export）。

**血缘视图**：`LineageReportView` 统一 9-tab（总览 / 输入资产 / 输出资产 / 处理过程 / 表级血缘 / 字段血缘 / 语义血缘 / 影响分析 / 风险 / AI 辅助 / AI 兜底推断）。`SemanticLineagePanel` 消费 `result.semantic_lineage`，渲染 observations / risks / 业务分组 DAG / 目标表 / procedures step 表。`LineageGraphPanel` 双引擎切换（G6 稳定 / Cytoscape 实验），数据派生抽到 `composables/useLineageGraphData.js` 共享。`LineageAIInferredPanel` 三栏分组展示 AI 兜底（红色 = parse_error / 黄色 = dynamic_sql / 蓝色 = column_attribution）。

**AI 集成**：4 个独立场景共享 provider 抽象 ——
1. **enrichment**（`/api/lineage/analyze` 默认开）：summary + suggestions + risks + column_hints
2. **inference 兜底**（admin 配置 `enable_inference` 开关）：对 `parse_errors` + `dynamic_sql_segments` + 字段歧义做白名单约束推断，结果落 `result.ai_inferred`
3. **错误翻译**（`/api/ai/translate-error`）：5xx / 长 4xx 错误的中文翻译 + 排查建议
4. **字段映射推荐**（`/api/ai/suggest-column-mapping`）：compare workbench `StepMapping` 紫色 ✨ 按钮触发

前端独有的 Figma → Vue 设计系统规则在 `frontend/frontend/CLAUDE.md`（Figma MCP 输出 React+Tailwind 参考代码 → 适配 Vue 3 SFC + 现有 `.btn`/`.card`/`.pill` 短类）。

构建产物输出到 `static/spa/`，由 FastAPI 在 `/static/spa/` 服务。`static/spa/index.html` 和 `static/spa/assets/` 已 gitignore，由 CI / Docker / release 脚本生成；手写资源 `static/spa/favicon.svg` 仍跟踪。`/spa` endpoint 加 `Cache-Control: no-cache`，避免 index.html 缓存住引用旧 hash bundle。

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

整体路径：**血缘稳定 → 多来源对比 → 作业流 → 工程治理 → 血缘语义增强 → 领域模型收口与 AI 异步化（当前）**。

### 当前 sprint：Phase 9 · 领域模型收口 + AI 包独立 + 异步化 + 错误响应统一

**为什么做**：项目已经从"工具"长成"小平台"，下阶段最缺的不是功能，是**稳定的领域语言**。当前最频繁的 bug 不是 JSON 写并发，是 dict 契约漂移（如 `t.get("name")` vs `t.get("table")` 让 AI 兜底白名单永远空）。先固定数据结构骨架，后面 AI 包独立、TS codegen、SQLite schema 才有锚点。

**6 day 计划**

#### Day 1：领域 schema 骨架

新文件 `app/models/lineage.py` —— **纯 Pydantic v2，禁止** `from app.lineage.*` 反向 import（避免循环）。

定义 schema：`TableRef / ColumnRef / ColumnEdge / TargetOperation / TargetSummary / ProcessStep / AIInferredEdge / AIColumnHint / AIInferenceResult / LineageReport`。每个 model `ConfigDict(populate_by_name=True, extra="ignore")` —— 让现有 dict 多余字段不抛错。`__init__.py` re-export。

测试 `tests/test_lineage_models.py`：~12 case，验证 dict round-trip 等价 + Literal 闭集 + AIInferredEdge confidence 拦截 high。

**Deliverable**：1 文件 + 1 测试 + __init__ 改 1 行。零业务代码动。

#### Day 2：API 出口包 model（不动内部 helper）

仅在 result 出口处用 model 校验 + `model_dump(by_alias=True)`，让 API contract 不变但 schema 集中。**不动解析器内部 helper**。

改动：`analyzer.analyze_sql_lineage` / `batch_analyzer.analyze_lineage_batch` / `lineage_service._attach_ai_inference`。

**关键约束**：旧测试零变更通过 = 向后兼容证明；旧测试发现失败时停修，不掩盖。

#### Day 3：AI inference 模型化

`lineage_ai_inference.py` 内部用 model：`_validate_and_filter_edges` 返回 `tuple[list[AIInferredEdge], int]`；`_validate_and_filter_column_hints` 返回 `tuple[list[AIColumnHint], int]`。22 个 inference 测试零修改通过。

#### Day 4：`app/ai/` 包独立

```
app/ai/
  providers/{base,openai_compatible,anthropic,mock,ollama}.py
  prompts/{enrichment,inference,dynamic_sql,column_attr,error_translate,column_mapping}.py
  schemas.py    # re-export from app.models.lineage
  filters.py    # _validate_and_filter_*, _normalize_name
  usage_log.py  # 新增：每次调用记 model/tokens/elapsed/status → logs/ai_usage.jsonl
```

老文件保留 thin shim re-export 给现有 import path 不破。

#### Day 5：AI inference 异步化

复用 `enqueue_lineage_ai_enrichment` 模式：
- `enqueue_lineage_ai_inference(...)` 跟 enrichment 一样开 thread
- `_attach_ai_inference` 默认改异步：`result["ai_inferred"] = {"status": "pending", "job_id": "..."}`
- 复用 `/api/lineage/ai/jobs/{job_id}` —— enrichment + inference 共享 jobs dict（`kind` 字段区分）
- 前端 `LineageAIInferredPanel` 加 pending/running/done 状态展示

**用户感知最强的一项**：大批量分析不再等 30~50s。

#### Day 6：统一错误响应 + AI 翻译改"按需"

后端：新增 `app/api/_error_handler.py` 统一翻成 `{code, message, detail, request_id, retryable, ai_translation?, suggestions?}`。`request_id` 用 ContextVar + middleware 注入。

前端：`api.js` 移除自动调用，保留 `translateError()` 显式 action。`AppShell` 错误卡片底部加 ✨"AI 解释"按钮 → 用户主动点击才烧 token。AdminAIConfigView 加可选 toggle `enable_auto_translation`（默认 off）。

**Phase 9 决策记录（ADR-style）**

- **schema 放 `app/models/lineage.py`**：跟 `Workflow / CompareTask / User` 同级，OpenAPI codegen 一处管，避免 `app/lineage/` 包内循环
- **`NodeOutput` 名字废弃**：跟 workflow node output 容易混；改用 `AIInferenceResult` / `LineageReport`
- **AI 翻译改按需**：默认 off，用户主动点 ✨"AI 解释"按钮才调
- **不上 Storybook**：Vitest + Vue Testing Library 性价比更高
- **不全量 store 拆分**：先治跨 store 时序耦合（`saveTask → reload bootstrap → selectTask` 这种隐式链），再考虑拆细
- **Repository 抽象拆两步**：(1) 短期把高并发写的 `audit.jsonl` + `jobs.json` 切 SQLite；(2) 长期再统一接口
- **Day 2 范围收窄**：只做"出口 model 化"，不一口气重写 analyzer / aggregation / roles 内部

**完成判定**：每天 push + 全测试套通过（≥ 645 测试 + Phase 9 新增的 ~25 个）。Sprint 收口时合并到"已完成"。

**分工**：Day 1（Claude 阻塞）→ Day 2（codex）∥ Day 3（Claude）→ Day 4（codex）→ Day 5（codex），Day 6（Claude 独立可在 Day 1 后随时插）。

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

**Phase 9** ✅ 部分（持续 sprint，见上）。

### Phase 7 长期参考的设计方向

按用户调研，以下五个项目代表 SQL 血缘 / 数据治理的成熟思路。本仓库已在 Track B 走 Dataedo 方向，后续如要深做再分别参考：

- **Dataedo**（PL/SQL 拆 step + step-level lineage + 不支持的 step 标 `parse_status=unsupported`）
- **Gudu SQLFlow**（先产中间血缘模型再画图：objects / columns / relations / process_steps / target_summary）
- **sqlglot**（AST + 方言适配 + column-level lineage API）—— 已在用，继续做底座
- **DataHub**（schema-aware 字段级血缘 / 解析失败时降级）—— 字段级必须 schema-aware
- **OpenLineage**（job / run / dataset / facet 四元模型）—— 已对接 emitter

**DM 达梦**：路线是 `dialect=dm` 内部继承 oracle，再补 DM 特有语法、函数、系统表、分页写法。第一阶段：表级血缘 + DML 聚合 + DELETE+INSERT 识别；字段级精细血缘第二阶段。**别追求一步到位字段级 100% 准确** —— 先把 step 拆分、DML 计数、refresh 模式、动态 SQL 误报修准，可信度立刻上一个台阶。

### 还可以做（未排期）

- **Repository 抽象 + SQLite**（Phase 9 ADR 第 6 条：先 audit/jobs，再统一）
- **字段级血缘解析端深化**：UDF / 包变量 / cursor 来源跟踪等 Oracle PL/SQL 深度场景（可视化 ✓ + transform 细化 ✓ 已落，剩解析端精细化）
- **Phase 4 procedure refresh mode 语义模式**（轨道 A 增量后续）
- **TypeScript 渐进迁移**：Pinia store + composable 先于 view，schema 从 Pydantic codegen
- **Vitest 关键组件单测**（不上 Storybook）
- **`/metrics` + structured logging**（Prometheus + JSON log + request_id）
- **API `/v1/` 版本化前缀**
- **App.vue 收尾**：剩下的跨 store handler 拆到对应 store，移除 `provide('app')`
- **路由 lazy loading**：admin views / workflow detail / lineage 全 `defineAsyncComponent`
- **i18n（vue-i18n）**：先 sidebar / login / global notice，详情页后跟
- **全局 ErrorBoundary**：捕获组件渲染异常 + 降级 UI
- **release-please / changesets**：自动生成 release notes from conventional commits
- **生产 runbook**：备份 / 升级 / 回滚 / 灾备

## 血缘图设计（双引擎：G6 稳定 + Cytoscape 实验）

参考 DataHub / Dagster / dbt Explorer / Atlan 的可扩展模式，避免 dagre 在 50+ 节点时把图压成一列：

- **默认 focal + N 跳 BFS**：`focusMode = neighborhood`，`hopDepth = 1`；节点数 > 30 时 `autoFocalId` 自动取最高度数节点
- **Schema 折成 combo 节点（G6）** vs **compound parent（Cytoscape）**：`localStorage.lineage-graph-prefs-v1` 持久化引擎选择；前者跨 schema 多边聚合 `×N`，后者每个 schema 是 dashed 紫色容器
- **逃生通道**：`viewMode = 'graph' | 'table'`（G6）；> 100 节点时显示推荐切表横幅
- **数据派生抽到 composable**：`useLineageGraphData.js` —— `allGraphData` → `filteredBase`（角色 / 边类型 / 可信度 / 脚本过滤）→ `projectedBase`（schema combo，仅 G6）→ `graphData / cyData`（focal+hop BFS）。两引擎共享派生只换渲染层
- **引擎切换**：`LineageGraphPanel.vue` 顶部切换器，两组件 `defineAsyncComponent` 懒加载（cytoscape ~534KB；G6 ~1.4MB）
- **真实大图验证**：等用户拿真实 Oracle 多脚本 lineage（300+ 节点）跑两引擎对比，再决定是否替换 G6。当前 G6 稳定 / Cytoscape 共存
