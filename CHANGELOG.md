# Changelog

## [Unreleased]

### 🐛 Phase 14 修缮:多方言索引 introspect + SPA cache header

- **DB2 / Oracle / DM `introspect_indexes`** —— P1-1 之前只 MySQL,现补齐:Oracle/DM 走 `ALL_INDEXES` + `ALL_IND_COLUMNS` join + `ALL_CONSTRAINTS` 二次 query 标 PK;DB2 走 `SYSCAT.INDEXES` + `SYSCAT.INDEXCOLUSE` join,`uniquerule='P'/'U'/'D'` 解析 unique + PK。各方言任一步失败安全降级返 `[]`,不阻塞 yml_importer。7 新测试覆盖三方言 + 失败降级 + 标识符校验
- **SPA cache-bust 根治** —— `main.py` 加 `_SpaStaticFiles(StaticFiles)` 子类:`index.html` 强制 `no-cache, no-store, must-revalidate`(deploy 后浏览器立刻拉新版);hash 化的 `assets/*.js/.css` 加 `Cache-Control: public, max-age=31536000, immutable`(永不 revalidate)。**解决用户反馈的"deploy 后进不去系统 / 白屏"**(老 index.html 引用已替换 hash bundle → 404)。4 新测试覆盖 index.html / hashed assets / nonexistent

### 🚀 Phase 14 P0-2 / P0-3 / P1-1 / P1-2 / P2 · SQL 优化沙盒生产化

把 SQL 优化沙盒从「demo / 测试工具」升级成「不连生产做 SQL 性能优化」生产级工作台,5 个切片一气交付。

- **P0-2 streaming generator + 流式 materialize** —— 新增 `iter_table_rows_streaming` 按 batch yield 行 + `materialize_streaming` 走 streaming insert + 派生表 SQL 端 `INSERT INTO derived SELECT FROM source` 零 Python 内存复制 + anomaly 三档处理(row-level inline / missing_rows 预采样跳过 / extra+dup 末批追加)。runtime 默认开 streaming。**内存 O(batch×col_width) 恒定,千万行不爆**(老路径 100k 行就 OOM 风险)
- **P0-3 materialize 后自动 ANALYZE** —— `MaterializeDialect.analyze_table_sql` 新抽象 + MySQL `ANALYZE TABLE` / Oracle/DM `DBMS_STATS.GATHER_TABLE_STATS` 实现。每表 materialize 完自动跑,best-effort 吞失败不阻塞。**优化器 cardinality 从默认估算变成真实数据采样**,EXPLAIN plan 接近生产
- **P1-1 SHOW CREATE TABLE → yml** —— `app/scenarios/yml_importer.py` 走 introspect_columns + 新加的 introspect_indexes (MySQL `SHOW INDEX FROM`) + introspect_row_count (info_schema.TABLES/USER_TABLES/SYSCAT)。`POST /api/scenarios/import-from-datasource` body `{datasource_id, table_names, scenario_id, save?}` → yml 文本(可选直接落 config/scenarios/<id>.yml)。列类型 + 列名启发推断 generator(int→sequence/random_int / varchar→realistic / datetime→timestamp / ENUM 解析字面值)。**手抄 schema 翻 yml 的 30 分钟变 30 秒**
- **P1-2 plan diff** —— `slow_sql_plans` SQLite 表 + `sql_hash` 归一化(空白折叠 / 保大小写)+ `save_plan` / `list_plans_for_sql` / `list_plans_for_scenario` / `diff_plans`。`/api/slow-sql/analyze` 自动落 history,新 endpoints `GET /api/slow-sql/plan-history` + `GET /api/slow-sql/plan-diff`。diff 算 max-rows 变化 / type 变化 / Extra token 增删 / issues 修复 vs 新引入。前端 SqlOptimizeView 加 plan diff 紫色卡片,绿/红 banner 标改善/退化 + step-level type/Extra 着色(老 strike-through,新 underline)
- **P2 UX 步骤式重构(完整版)** —— 顶部加 4-step 视觉导航条(schema → 生成数据 → SQL 优化 → 回归校验),当前步骤紫色高亮。新增「从 datasource 导入」主按钮 → inline 对话框接 P1-1 endpoint。**SqlOptimizeView.vue 1689 → 322 行**(81% 减),抽 `stores/sandbox.ts` Pinia store(510 行)+ `types/sandbox.ts`(269 行)+ 4 个子组件(`views/sql-optimize/`:`ImportDialog.vue` 74 行 / `ScenarioListPanel.vue` 35 行 / `SlowSqlCards.vue` 267 行 / `ResultPanels.vue` 241 行)。每个文件 < 300 行,可读性显著提升
- **scenarios router 权限 admin → editor+** —— 配合 P0-1 重定位,后端跟前端权限对齐;datasource / project 级权限仍由各 endpoint 内部 `require_datasource_access` / `require_project_access` 保护

### 🔬 Phase 14 P0-1 · SQL 优化沙盒重定位

scenario 沙盒(Phase 12 起的「admin 测试沙盒」)实际用途是数据工程师 / DBA 日常处理慢 SQL 工单 —— 不是 admin 工具。重定位:

- 路由 `/admin/sandbox` → `/sql-optimize`(老路径保留 301 重定向兼容老书签)
- 权限 `adminOnly` → `editor+`(SQL 优化不是 admin 特权;`require_datasource_access` 仍约束)
- 视图 `views/admin/ScenarioSandboxView.vue` → `views/SqlOptimizeView.vue`(git mv 保留 history)
- 图标 Beaker → Microscope(语义更贴 SQL 性能调优)
- 升级到顶级一级菜单(原在 admin 子菜单),i18n key `adminNav.sandbox` → `nav.sqlOptimize`
- 标题「测试沙盒」→「SQL 优化沙盒」+ 副标题改成业务用途描述
- 后端 API + scenario DSL + 沙盒能力完全不变 —— 只是 UI 位置 + 命名 + 权限调整

### 🧹 Phase 14 · backlog 清零(P2/P3 + 8 处陈旧 doc 同步)

- **8 处陈旧 doc 同步** —— MFA/SESSION_HARDENING/STEP_UP_AUTH/REFRESH_ROTATION/COMPARE_RESULT_STORAGE/RESOURCE_GUARD/SIGNED_DOWNLOAD/CLAUDE.md 把 "未做" 段改成 ✅ 已落地的真实状态
- **scenario-nightly.yml schedule 转正** —— 每天 UTC 18:00 自动跑 scenario 回归(`cron: "0 18 * * *"`)
- **resource_guard per-user cap** —— `DATAOPS_MAX_JOBS_PER_USER=1` 默认 + `JobInfo.owner_user_id` 维度;`active_compare_owner_ids` helper;system / 空 owner 跳过
- **per-project 跨 run 配额** —— `DATAOPS_PROJECT_DISK_QUOTA_MB`(0=无限);`_project_disk_usage_mb` 扫 results/ 折成 per-project 累积 MB,超限 deny
- **DB2 estimate_rows_from_explain** —— `EXPLAIN PLAN FOR <sql>` + `SELECT MAX(STREAM_COUNT) FROM EXPLAIN_STREAM`(ibm_db 不在 build 时返 None);方言矩阵 4/4 ✅
- **签名下载一次性 nonce** —— `download_nonces` SQLite 表 + `consume_download_nonce(jti)`;同 token 第二次访问 410 Gone(防截获重放);老 token 无 jti 兼容直接放行
- **签名下载单 parquet 桶 kind** —— `bucket_only_source / bucket_only_target / bucket_diff / bucket_same` 4 个 kind,直接拿桶 parquet 文件签名链接
- **CI security 三件套** —— `release.yml` `actions/attest-build-provenance@v2` 给 Windows offline zip 加 SLSA-style 来源证明 + `ci.yml` SBOM job(CycloneDX,backend `cyclonedx-bom` / frontend `@cyclonedx/cyclonedx-npm`,90 天 retention)+ `dependency-review-action` PR 拦 high/critical CVE
- **lineage_script 模板变量条件分支** —— `templating.py` 加 `{% if var %}...{% endif %}`(不嵌套 / 不 else / 不比较运算符,YAGNI),Python truthy 语义(`""` / `0` / `False` / `None` / 未定义都 false),`RenderedSql.conditions_evaluated` 多一栏
- **AI filler v3 Faker locale fallback** —— `faker>=24.0` + `app/scenarios/faker_fallback.py`,provider=off 时仍能给 `column_values` 填业务样本(`detect_locale_from_scenario` 推断 zh_CN/en_US;curated `column_name → faker method` mapping 25 条;`table_descriptions` / `column_distributions` 仍需 provider 在场)
- **`/api/sql/preflight` 前端 Workbench UI** —— `WorkbenchSummary.vue` 多 `🔬 估算 plan` 按钮(在「更多操作」折叠区,SQL 源 + 已选 ds 时启用),调 `run_explain=true&datasource_id=` + 紫色卡片渲染 risk 徽章 + 规则列表 + suggestion;safe degrade 时 banner 提示

### 🛡 Phase 13 · 可用性收尾(deep-research 报告剩余项)

- **Oracle / DM 语句超时** —— `Dialect.apply_call_timeout(conn, sec)` 走 `connection.callTimeout` 毫秒;factory `_apply_statement_timeout` 双路径派发(连接属性优先,SQL fallback)。补 docs/DB_STATEMENT_TIMEOUT.md 方言矩阵
- **JobInfo 三字段补全** —— `owner_user_id / project_id / target_run_id` 落 model + jobs.py submit + API caller(tasks/workflows/workflow_runs)+ scheduler(`owner_user_id="system"`)。authz 不变,数据模型卫生 + 后续 audit 直接读字段
- **RunLimits.query_timeout_seconds** —— 单任务覆盖全局 DB 超时。ContextVar + runner `with query_timeout_override(...)` 包,fetch_rows / iter_rows / fetch_column_details 三处自动取这个值。慢但合法的 ETL 可提到 1800s,日常 preview 任务可缩到 60s
- **mid-run 磁盘水位检查** —— `resource_guard.check_disk_critical()` + `DiskWatermarkExceeded`。runner 双 streaming 分支每写 5000 行查一次,达 critical 主动 raise + `_cleanup_partial_parquet` rmtree 临时 run 目录避免半成品累积
- **per-run 磁盘配额** —— `RunLimits.run_disk_quota_mb`(None=无限);`check_run_quota(run_dir, quota_mb)` 累计 run_dir/** 字节折 MB;超额抛 `RunQuotaExceeded(DiskWatermarkExceeded)`(子类共享 cleanup 路径);runner mid-run 检查跟主机水位走同一 `_check_mid_run_disk` 入口
- **DB2 语句超时** —— `Db2Dialect.apply_call_timeout` 走 `ibm_db.set_option(conn_handle, {SQL_ATTR_QUERY_TIMEOUT: sec}, 1)` 连接级 option。ibm_db 不在 build 默认装 → 返 False 安全降级。方言矩阵收尾(MySQL / Oracle / DM / DB2 全 ✅)
- **typecheck 技术债清零确认** —— `npm run typecheck` / `build` / `vitest` 全绿,CLAUDE.md 陈旧记录修正(此前已被 `c1c4616` 修完,文档没同步)
- **sql_preflight EXPLAIN 集成(MySQL)** —— `Dialect.estimate_rows_from_explain(conn, sql) -> int | None` 给静态体检加 plan 估算。MysqlDialect 跑 `EXPLAIN <sql>` 取 `rows` 列 max(避免 sum 高估 / last 漏 fan-out)。`sql_preflight.assess_with_explain` 静态不阻塞时调,估算超 `max_rows × 10` 加 warn finding。DB2 留口返 None
- **EXPLAIN 扩 Oracle / DM** —— `OracleDialect.estimate_rows_from_explain` 走 `EXPLAIN PLAN SET STATEMENT_ID='...' FOR <sql>` + `SELECT MAX(cardinality) FROM PLAN_TABLE WHERE statement_id='...'` 两步,`finally` DELETE + commit 清理防 PLAN_TABLE 累积膨胀。statement_id 用 uuid hex 隔离并发。DM 继承自动支持
- **`/api/sql/preflight` 接 EXPLAIN** —— body 加 `run_explain=true&datasource_id=<id>` 即走 EXPLAIN 路径。`require_datasource_access` 一次完成存在性 + project 权限校验。连接错误 / driver 没装 / EXPLAIN 异常都 fallback 纯静态 + 200,不让 preflight 整体崩

## [0.2.0] - 2026-05-23

`0.1.0 → 0.2.0` 这一个 minor 涵盖一段长链路工作(Phase 11/12 + 安全加固全栈)。
后续小版本将由 release-please 自动维护。

### 🔐 安全加固(Auth 全栈)

- token 吊销 + 真 logout(服务端 jti 表 + 前端 POST /api/auth/logout)
- step-up 再认证(300s 窗口 + verify-password + withStepUpRetry helper)—— 含密码导出 / 配置导入 / 删用户 / AI 密钥保存
- 结果文件签名下载 token(取代可猜的 /results/* 直链)
- resource_guard 跨维度并发配额(per-project / per-datasource cap)
- sql_preflight run/run-async 强制 block 通道
- DB 语句超时 900s(MySQL 生效)
- Dependabot 配置 + CI 依赖审计 job
- RedactingFilter 覆盖 extra 字段 + 裸 JWT / 连接串凭据
- MFA (TOTP) — enroll/verify/disable + 登录两步流 + QR + secret 加密落盘
- MFA recovery codes — 10 个一次性后备码 + bcrypt 哈希 + 重新生成 + 登录页恢复码模式
- Refresh token rotation (OAuth2 风格) + reuse detection(已替换的 refresh 又被用 → 整链 revoke)
- Access JWT TTL 8h → 30min(配合 refresh)
- Rate limit /login + /mfa/challenge + /refresh + /verify-password — per-IP + per-username 双滑窗 + 429 Retry-After + metric
- HttpOnly + Secure + SameSite=strict cookie 存 refresh(XSS 偷不走)
- Audit log enrich — login_success/failure / refresh_rotation / mfa_* / step_up_* / rate_limit_hit / logout 全套
- 自签 HTTPS 部署(nginx-rp + cert/key + 80→443 redirect)—— X-Forwarded-Proto 判 cookie Secure

### 🚀 Features

- **AI 测试沙盒(Phase 12,18 commits)** —— scenario DSL + generator + materializer + recorder + admin UI + slow-sql 规则分析 + AI 复核 + AI filler + regression verifier + 一键链 orchestrator + lineage_script workload + Oracle/DM 方言扩展 + verifier tolerance + SQL 模板变量 + slow-sql Oracle EXPLAIN + AI filler v2 分布参数 + CI scenario lint + 夜间回归 workflow 模板
- **大结果落盘** —— ParquetResultWriter + meta.json + same 桶 count_only + bucket 分页 reader API + Excel write_only 流式异步导出
- **trace-compare** —— 沿血缘逐层对比 + 链式着色 + 「首次偏离 hop N」诊断
- **字段血缘 tracing UI 多跳** + procedure refresh mode 语义深化
- **数据库方言模块化** spike —— `app/dbclients/dialects/*.py` 收口 ~10 处 `if db_type == ...`
- **TypeScript 渐进迁移** —— 10 stores + 20 views + composables + openapi-typescript codegen
- **i18n** —— vue-i18n 11.x + zh/en 镜像 + topbar 切换 + 全 view 覆盖
- 命令面板(CommandPalette) + 全局通知 popover + 路由 lazy loading

### 🐛 Bug Fixes

- /results/* 直链 13 处全部切 fetch+blob(修浏览器导航不带 token 必 401)
- parquet authz 端到端覆盖 + build_excel max_rows 默认走 meta.limits
- 直接 datasource_id 接口强制 require_datasource_access
- 校验内部引用资源防止间接越权
- Counter.inc 用 kwargs 不是 labels=dict(auth_rate_limit_hits_total label 为空 bug)
- cookie Secure 用 X-Forwarded-Proto 判断(nginx 终端 TLS 场景)
- qrcode 装到 frontend/frontend 而不是仓库根

### 📝 Documentation

- COMPARE_RESULT_STORAGE.md + STREAMING_COMPARE_WRITER.md
- MFA.md + REFRESH_ROTATION.md
- PROJECT_AUTHORIZATION.md §4.3

## [0.1.0] - 2026-04-28

- chore: 清理内部开发文档和 macOS 垃圾文件
- fix: 添加 ZIP bomb 防护、任务状态持久化和单元测试
- feat: 拆分执行历史为数据对比和血缘分析两个标签页
- feat: v7 — SPA 前端静态构建、历史服务排序、任务配置优化
- feat: 添加 Dockerfile 和 docker-compose.yml
- init: 项目初始化

[Unreleased]: https://github.com/allen-answer/DataOpsStudio/compare/v0.2.0...main
[0.2.0]: https://github.com/allen-answer/DataOpsStudio/releases/tag/v0.2.0
[0.1.0]: https://github.com/allen-answer/DataOpsStudio/releases/tag/v0.1.0
