# Changelog

## [0.3.0](https://github.com/allen-answer/DataOpsStudio/compare/v0.2.0...v0.3.0) (2026-05-27)


### ⚠ BREAKING CHANGES

* **workbench:** SQL 控制台键入光标跳回开头 (debounced save race)
* **sql-workbench:** 3 个 P0 修复 (字段补全 / DB2 PATH 溢出 / 大 schema)
* **deploy:** bind mount data/ 防 SQLite 重启丢数据
* **security:** Wave 2 — GuardConfig 生产 enforce + Dockerfile 非 root + CI SHA pin

### 🚀 Features

* **availability:** DB2 语句超时 + typecheck 债清零确认 ([d00b1c3](https://github.com/allen-answer/DataOpsStudio/commit/d00b1c3cdda34d4b50545970b0ae8534ed3b48fa))
* **availability:** per-run 磁盘配额(RunLimits.run_disk_quota_mb) ([ef53fe2](https://github.com/allen-answer/DataOpsStudio/commit/ef53fe23f9053cdefe62abe31feda1e5e6f3a4b0))
* **availability:** Phase 13 可用性收尾 4 项(deep-research 报告剩余) ([f480418](https://github.com/allen-answer/DataOpsStudio/commit/f480418b78710bc3a4a2e0c09cdb205635b4ea6b))
* **backlog:** Phase 14 backlog 清零 — P2/P3 + 8 处陈旧 doc 同步 ([d8ef27a](https://github.com/allen-answer/DataOpsStudio/commit/d8ef27aaa6daeee9f436b82247228b0f91d090ac))
* **build:** Windows 自包含 portable 打包脚本 ([5da968e](https://github.com/allen-answer/DataOpsStudio/commit/5da968e8a1f6c49528ffd50079ee9882818a05a1))
* **compare:** Wave 3 [#13](https://github.com/allen-answer/DataOpsStudio/issues/13) — run_index SQLite 表 + 统一 compare 入口 ([93c9561](https://github.com/allen-answer/DataOpsStudio/commit/93c9561d55dd232ad248c444c54cce6a7d782b1e))
* **observability/safety:** 包 B 日志结构化脱敏 + 包 C 并发限制 / 统计 / 分页 ([b7b9340](https://github.com/allen-answer/DataOpsStudio/commit/b7b934012a5c655786b9653f2b5fef78e1b2e009))
* **observability:** scripts/log_diagnose.py 日志离线诊断脚本 ([2b936dc](https://github.com/allen-answer/DataOpsStudio/commit/2b936dc8e2aa2f4d867c3868c4f7f1f55355076f))
* **offline:** import-db-drivers.bat 把现有 dmPython/oracledb/ibm_db 迁到 portable ([c68786e](https://github.com/allen-answer/DataOpsStudio/commit/c68786e81cada80ce60468549269063576cf8cb7))
* **offline:** portable 升级包 4 件套 .bat + start.bat .env 加载 ([0d9c94a](https://github.com/allen-answer/DataOpsStudio/commit/0d9c94aa29923ed0838bc39f496e05a7a621b4d2))
* **offline:** upgrade.bat 入仓 + build 脚本自动复制 + 修 21 处 redirect bug ([da4f04e](https://github.com/allen-answer/DataOpsStudio/commit/da4f04e33a315e2257e18e38e2f13113b91f53e0))
* **performance:** Wave 4 — memory_guard + writer 双阈值 + 大任务自动 promote ([2813100](https://github.com/allen-answer/DataOpsStudio/commit/281310010e31a292eeb92a3feb8918515ad6fadb))
* **scenario-builder:** Phase 14 [#3](https://github.com/allen-answer/DataOpsStudio/issues/3) Round 4 — 可视化 yml 编辑器(MVP) ([9d86669](https://github.com/allen-answer/DataOpsStudio/commit/9d866698bb33ea168b6fb5967731facbcfd829a4))
* **scenario-builder:** Round 5 — 列默认折叠 + 粘贴 DDL 批量添加列 ([1f0cbdc](https://github.com/allen-answer/DataOpsStudio/commit/1f0cbdcd847553f70d9f70c5061598a07442b999))
* **scenarios:** [#1](https://github.com/allen-answer/DataOpsStudio/issues/1) datasource 环境标签 + 沙盒写入合规防御 ([55ada6e](https://github.com/allen-answer/DataOpsStudio/commit/55ada6ed0d50d147be8dd108f5df2a7c65d2db98))
* **scenarios+sql:** mysql collation 防线 + rows cap 100M + slow_sql_enhance 模块 ([8cf0149](https://github.com/allen-answer/DataOpsStudio/commit/8cf0149afd5a14d6020affc775c735cacb11ea0e))
* **scenarios:** Phase 14 [#3](https://github.com/allen-answer/DataOpsStudio/issues/3) Round 6 H.4 — anomaly + workload 可视化编辑器 ([8dfe46b](https://github.com/allen-answer/DataOpsStudio/commit/8dfe46bf23fc20a54bb455c6dcedf807933533fc))
* **scenarios:** Phase 14 [#3](https://github.com/allen-answer/DataOpsStudio/issues/3) Round 6 Session 1 — referential integrity 后端 ([2e53322](https://github.com/allen-answer/DataOpsStudio/commit/2e533227b5dccfd86272b486db15363487f38b69))
* **scenarios:** Phase 14 [#3](https://github.com/allen-answer/DataOpsStudio/issues/3) Round 6 Session 2 — FK UI + 行业模板 + faker provider + metadata csv ([7732764](https://github.com/allen-answer/DataOpsStudio/commit/773276432c1a457983b90d1751bb11c4adf957b9))
* **security:** Phase 14 [#3](https://github.com/allen-answer/DataOpsStudio/issues/3) — fail-safe DataSource env + operation_policy 决策层 ([7d16f7d](https://github.com/allen-answer/DataOpsStudio/commit/7d16f7d3d5cd8a875731271b22bc0f0b4d1a0c2b))
* **security:** Wave 5 — Prometheus compare metrics + run_index endpoint + 依赖 lock 流程 ([e9ca000](https://github.com/allen-answer/DataOpsStudio/commit/e9ca0007fb77c69f38f29d9e7c8198f4f36ebb9d))
* **slow-sql,scenarios:** Phase 14 [#3](https://github.com/allen-answer/DataOpsStudio/issues/3) — DM 独立 EXPLAIN + API 接 operation_policy ([1880f72](https://github.com/allen-answer/DataOpsStudio/commit/1880f727c9147a66825e1690985b4ceb734f1ae4))
* **slow-sql:** [#2](https://github.com/allen-answer/DataOpsStudio/issues/2) lineage-aware 索引建议 — 加索引会拖慢谁 / 受益于谁 ([5c7de35](https://github.com/allen-answer/DataOpsStudio/commit/5c7de353496d97ef7447876601bd172344518b9a))
* **slow-sql:** 规则建议升级 — schema introspect + 具体 CREATE INDEX DDL ([a338ed8](https://github.com/allen-answer/DataOpsStudio/commit/a338ed8003051c5dda8102e4e928b5e5f873453a))
* **sql-editor:** 别名补全 —— FROM users t 后键入 t. 列 users 字段 ([e9383ee](https://github.com/allen-answer/DataOpsStudio/commit/e9383eef96b2995429bea993fe243c7630447809))
* **sql-optimize:** Phase 14 P0-2 / P0-3 / P1-1 / P1-2 / P2 五合一 ([ca13684](https://github.com/allen-answer/DataOpsStudio/commit/ca13684fa887024ebf663f04141f73b10c2dbf70))
* **sql-optimize:** 加快速优化默认 mode — 不用模板,粘 SQL 即可分析 ([c44375b](https://github.com/allen-answer/DataOpsStudio/commit/c44375b41e46c12e5209562c02a3b8b6d263bc06))
* **sql-preflight:** EXPLAIN 集成 (MySQL plan 估算超阈值加 warn) ([83f6292](https://github.com/allen-answer/DataOpsStudio/commit/83f6292cedfa348c127641d8f609992b2b92392b))
* **sql-preflight:** 扩 EXPLAIN 到 Oracle/DM + 接 /api/sql/preflight endpoint ([19362c5](https://github.com/allen-answer/DataOpsStudio/commit/19362c522d0412bbae1ca24e8e2d10193a636472))
* **sql-tools:** 无 alias 复合表达式自动起短别名(防 UI 撑爆) ([4537646](https://github.com/allen-answer/DataOpsStudio/commit/453764679e5c902440a084595731e01ef862204d))
* **sql-tools:** 自动 SQL 改写注入别名 (rewritten_sql + UI 一键应用) ([e00e0f1](https://github.com/allen-answer/DataOpsStudio/commit/e00e0f1b0a6177cbda17d315b7c830a16070e2d0))
* **sql-workbench:** execution job 状态机 + timeout + 页面离开提示(v0.5+) ([7ba9cc3](https://github.com/allen-answer/DataOpsStudio/commit/7ba9cc39d6830cb0bcb7259932793bd50912927e))
* **sql-workbench:** Explain 增强 + 4 条静态规则 + 慢 SQL 标记(v0.5) ([698947a](https://github.com/allen-answer/DataOpsStudio/commit/698947a059bb16209cc52b73f025f436da6b69e7))
* **sql-workbench:** metadata 缓存 + 对象搜索 + 表详情(v0.3) ([509e5a6](https://github.com/allen-answer/DataOpsStudio/commit/509e5a6db21bf9e8190be931eb80c135c2c37748))
* **sql-workbench:** Phase 1 — 后端 API + JSON 存储 + SELECT 执行 ([6106f8b](https://github.com/allen-answer/DataOpsStudio/commit/6106f8bc25ef1bc0fbe9db98de7f0b7b33319e66))
* **sql-workbench:** Phase 2 — 前端多 tab + CodeMirror + 结果/历史面板 ([5521520](https://github.com/allen-answer/DataOpsStudio/commit/5521520f1525d59be16675f56d7b67981af72254))
* **sql-workbench:** Phase 3 metadata tree + Phase 4 跟血缘/对比/诊断打通 ([51be27c](https://github.com/allen-answer/DataOpsStudio/commit/51be27c36f419f163f36506674af6be2b5d33c45))
* **sql-workbench:** SQL 模板库(v0.4) ([ea1088c](https://github.com/allen-answer/DataOpsStudio/commit/ea1088c2a081fae1c4c3b233f4c9814998fd51be))
* **sql-workbench:** SQL 编辑器补全 + 6 个 snippets + 本地草稿 ([b8f5cdf](https://github.com/allen-answer/DataOpsStudio/commit/b8f5cdfc76ad884501435af5219c1b78ee167dcc))
* **sql-workbench:** v0.2 — 格式化 + Explain + 查询中断(异步执行模型) ([9655c5d](https://github.com/allen-answer/DataOpsStudio/commit/9655c5d2adb65cff8b49c28167834fc0a743c1af))
* **sql-workbench:** 一键展开 SELECT * 成完整列名 (IDE 风格) ([06a93d1](https://github.com/allen-answer/DataOpsStudio/commit/06a93d1e81c7ee8b268968a9b1b0057cd0d89ae2))
* **sql-workbench:** 包 A Workbench 三路径策略 + config.yml 统一加载 ([d949f37](https://github.com/allen-answer/DataOpsStudio/commit/d949f37f23c5cc08f8edf3c4628481c17e07deae))
* **sql-workbench:** 字段补全 bulk 预热 — 写 SQL 立即提示字段 ([83c0419](https://github.com/allen-answer/DataOpsStudio/commit/83c04198afd3144fce12c92b3f6417d552ddd981))
* **sql-workbench:** 结果导出 CSV/Excel/JSON/SQL + 异步任务 + 公式注入防御 ([c2a2e8d](https://github.com/allen-answer/DataOpsStudio/commit/c2a2e8dfd3bbd2bc9a11710d5c0aee9dc54d5643))
* **ui:** Phase 14 [#3](https://github.com/allen-answer/DataOpsStudio/issues/3) — 拆 /sql-optimize 为 /sql-diagnosis + /scenario-lab + /schema-import + 风险面板 ([b441336](https://github.com/allen-answer/DataOpsStudio/commit/b441336b87b41af1d169764ed956a95e5ebff406))
* **ui:** 登录页改控制台风 + DS 改列表 + sidebar 重排 + SqlEditor 全屏按钮挪位 ([c4aed07](https://github.com/allen-answer/DataOpsStudio/commit/c4aed073e5fa4c93ea21729ec554f26ab640c512))
* **workbench:** metadata 全量重新加载 + 进度条 (DataGrip 风格) ([fa70846](https://github.com/allen-answer/DataOpsStudio/commit/fa708462d469e5a9714902df0d83fb34f3b09723))
* **workbench:** 数据来源 toggle 加 CSV + Parquet (前端 UI 暴露) ([443b96c](https://github.com/allen-answer/DataOpsStudio/commit/443b96cab2e9ef4307b4b47d01c7b36f97eca27c))


### 🐛 Bug Fixes

* **datasources:** 编辑表单 env select 也补 unknown option ([1badbaf](https://github.com/allen-answer/DataOpsStudio/commit/1badbafb306782fe1861eb69b99446435a55a939))
* **datasources:** 连接测试结果 inline 显示在行内,不再用顶部 banner ([accd369](https://github.com/allen-answer/DataOpsStudio/commit/accd3695ce804f0610cd84a50f5bfbc47bf6a846))
* **datasources:** 高级配置 checkbox 被 [@layer](https://github.com/layer) base 拉满宽 — 显式 h-3.5 w-3.5 ([eba7a7a](https://github.com/allen-answer/DataOpsStudio/commit/eba7a7a3400c8bffd7c122cf3980dea02f926ad4))
* **datasources:** 高级配置补 unknown 环境支持 + 未验证徽章 ([9b56130](https://github.com/allen-answer/DataOpsStudio/commit/9b56130b055f29bf133a3ce422c65dfebff32ced))
* **dbclients:** DM 缺 dmPython.libs DLL search path ([33558cc](https://github.com/allen-answer/DataOpsStudio/commit/33558cc4d47342236ba6c152d01f1e24605fdcdc))
* **deploy:** bind mount data/ 防 SQLite 重启丢数据 ([c8a8e81](https://github.com/allen-answer/DataOpsStudio/commit/c8a8e810bae1fc2447f8ec43d732f711ef0630be))
* **deps:** Windows 离线包必须显式带 tzdata —— 否则 APScheduler 启动崩 ([978a382](https://github.com/allen-answer/DataOpsStudio/commit/978a382452be88832aa839f41aebb42a8ee687f6))
* **login:** 登录成功后主动 reload bootstrap + project — 修首次登录列表空白 ([3626d70](https://github.com/allen-answer/DataOpsStudio/commit/3626d7003cba1f12c880576a5e11ea14f3ea4c1e))
* **offline:** .bat 全英文 ASCII + 强制传路径 + 去 PowerShell/chcp ([685049d](https://github.com/allen-answer/DataOpsStudio/commit/685049d0970db4fefddf570140115514d25a5691))
* **offline:** .bat 文件转 CRLF 行尾 + chcp 65001 UTF-8 显示 ([21e5649](https://github.com/allen-answer/DataOpsStudio/commit/21e5649df6640a41e856871a2d7c6a9471f0e784))
* **offline:** import-db-drivers.bat verify 行 importlib.util 加载错误 ([82fc6c6](https://github.com/allen-answer/DataOpsStudio/commit/82fc6c6fcb2154067b562e521b625f4c1eb4c6f2))
* **offline:** import-db-drivers.bat 漏复制 dmpython.libs ([a8edac7](https://github.com/allen-answer/DataOpsStudio/commit/a8edac7eecf726751a63f62a7da560db81309c78))
* **offline:** start.bat 加日志 + 错误诊断 + 退出码,防"闪退看不到错误" ([eeb1651](https://github.com/allen-answer/DataOpsStudio/commit/eeb165136bcbacbaa9b6facdf72172e9aa4dc0d9))
* **refresh:** 重放检测只 revoke 当前 chain,不再误踢同账号其他独立 session ([53bbc03](https://github.com/allen-answer/DataOpsStudio/commit/53bbc037b030bfb9c40dc7e2c52f90de1a5de50f))
* **scenario-builder:** 模板库显眼化 + 表清空 / 表单重置按钮 ([06d29ed](https://github.com/allen-answer/DataOpsStudio/commit/06d29ed44072da80a97121bd2ea05a57c3ba613a))
* **scenarios/mysql:** materialize 显式 utf8mb4_unicode_ci collation 兜底 ([c77a3ad](https://github.com/allen-answer/DataOpsStudio/commit/c77a3ad283061b3464144b8e4f63ffe20baac01c))
* **scenarios:** TableDef.rows cap 提到 1 亿 ([2b246dd](https://github.com/allen-answer/DataOpsStudio/commit/2b246dd69895c5f01c8c04f37cadef74860cd39e))
* **scenarios:** templates 端点路由顺序 — 必须在 /{scenario_id} 之前 ([40a0298](https://github.com/allen-answer/DataOpsStudio/commit/40a029863a242a6d9bd7f22724816dd21d78a2f1))
* **security:** Phase 14 [#3](https://github.com/allen-answer/DataOpsStudio/issues/3) Round 2 — plan-history + verify 跨项目泄露收口 ([9b52aa5](https://github.com/allen-answer/DataOpsStudio/commit/9b52aa5f6a0d59068fde8443a2b8ab3013727d8a))
* **security:** Wave 1 P0 加固 — 生产 fail-fast / refresh cookie-only / 老下载路径关停 ([07891a6](https://github.com/allen-answer/DataOpsStudio/commit/07891a6a8947bf953d057662983be4d50a8864df))
* **security:** Wave 2 — GuardConfig 生产 enforce + Dockerfile 非 root + CI SHA pin ([1ac3bcb](https://github.com/allen-answer/DataOpsStudio/commit/1ac3bcb1e35a91dc4af281be5f38dcf464d1b09c))
* **spa+introspect:** SPA cache 根治 + Oracle/DM/DB2 索引 introspect ([ad187e0](https://github.com/allen-answer/DataOpsStudio/commit/ad187e0c8d1a8b978197a41b4d84c4985ae82486))
* **sql-diagnosis:** facade store 用 storeToRefs 穿透 reactive 引用 ([791036e](https://github.com/allen-answer/DataOpsStudio/commit/791036e5ebd8f504afae0976a4d98ae023df5bf1))
* **sql-optimize:** step bar 可点 + 引导文案 + 不再自动选 scenario ([a303147](https://github.com/allen-answer/DataOpsStudio/commit/a3031471a4b122102cc1331bfd8996a8bf2ed8e3))
* **sql-tools:** SUM 字段输出列名显示为 6/7/8 序号 ([b213051](https://github.com/allen-answer/DataOpsStudio/commit/b21305178170b192ba4e23203347d20d337f3163))
* **sql-workbench:** _execution_envelope 透传 cancel_reason/timeout_seconds ([41baee7](https://github.com/allen-answer/DataOpsStudio/commit/41baee7b58a3a186104a834152285feb0ab3ca4d))
* **sql-workbench:** 3 个 P0 修复 (字段补全 / DB2 PATH 溢出 / 大 schema) ([19ee9dc](https://github.com/allen-answer/DataOpsStudio/commit/19ee9dc10e3995af0e2230f34e09d1340fb4b953))
* **sql-workbench:** execute_sql 加双层内存防护,防 OOM 把容器搞崩 ([f5cc265](https://github.com/allen-answer/DataOpsStudio/commit/f5cc26573b9f602155ac9c1f0cdb6f3780bd3622))
* **sql-workbench:** 导出下载用 apiDownload 替换 window.location.href ([a7ce190](https://github.com/allen-answer/DataOpsStudio/commit/a7ce19038e719d992024de3156a3a20b851b8c2f))
* **sql-workbench:** 搜索条挪到顶部,跨 tab 都可见 ([c1e200f](https://github.com/allen-answer/DataOpsStudio/commit/c1e200f5534faf92925123737278d90dacc074b9))
* **sql-workbench:** 预热所有 schema 的 tables 让 select * from &lt;schema&gt;. 立即补全 ([8bc70a7](https://github.com/allen-answer/DataOpsStudio/commit/8bc70a773c7b50a37a7f78bd7b1309e309782277))
* **ui:** Phase 14 [#3](https://github.com/allen-answer/DataOpsStudio/issues/3) Round 3 — 数据源管理表单加 allow_* 控件 + 环境预设 ([5f245ec](https://github.com/allen-answer/DataOpsStudio/commit/5f245ec95a5ffad2081aac9e5f27b1b919b3b535))
* **ui:** 登录页字段图标遮挡 + 删默认账号提示 + 编辑展开 checkbox 也修 ([d772346](https://github.com/allen-answer/DataOpsStudio/commit/d772346faade70e7d5a19f9f4b6478319ef88086))
* **workbench:** DB2 / Oracle / DM schema 名小写输入也能触发补全 ([ea8f63b](https://github.com/allen-answer/DataOpsStudio/commit/ea8f63b4cb11cf133a083fe014c6d709a82d40ef))
* **workbench:** SQL 控制台键入光标跳回开头 (debounced save race) ([ca4b8c9](https://github.com/allen-answer/DataOpsStudio/commit/ca4b8c99c2e3315f22b1142ef84299b021bf09bd))
* **workbench:** 主键选择改 toggle 支持多列(原行为是替换) ([fc8f037](https://github.com/allen-answer/DataOpsStudio/commit/fc8f037967d3c7d1160e7f2970743cb464fac376))


### ♻️ Refactor

* **sandbox:** SQL 优化沙盒重定位 (Phase 14 P0-1) ([6fc23a2](https://github.com/allen-answer/DataOpsStudio/commit/6fc23a27b8a272b7ad048b3b411b8feb25243b39))
* **scenarios:** 独立 /scenarios 一级菜单 + SqlOptimizeView 瘦身 ([16f624f](https://github.com/allen-answer/DataOpsStudio/commit/16f624fdc1decee3f44c0e63ca031becbb5e74e1))
* **sql-optimize:** P2 完整版 view 拆分 — 1689 -&gt; 322 行 ([08096dd](https://github.com/allen-answer/DataOpsStudio/commit/08096dd376984bd681b385e525da84961a41d1e3))
* **ui:** Phase 14 [#3](https://github.com/allen-answer/DataOpsStudio/issues/3) Round 2 — DM/Oracle 支持 + facade store + OperationPreviewModal + 业务化 RiskPanel + 旧 /sql-optimize 迁移页 ([160a5e7](https://github.com/allen-answer/DataOpsStudio/commit/160a5e77b7aa4158e3348d14f749846321f6adc9))
* **ui:** Phase 14 [#3](https://github.com/allen-answer/DataOpsStudio/issues/3) Round 3 — IA 收紧为「两页 + 一个子流程」 ([6611f8d](https://github.com/allen-answer/DataOpsStudio/commit/6611f8dd0a9a4d68a393374633637b7f03f4422d))


### 📝 Documentation

* **claude:** CLAUDE.md 收 Phase 13 章节 + 路线图更新 ([c5c1c81](https://github.com/allen-answer/DataOpsStudio/commit/c5c1c8182fe1d3f53bd1c4629fc698868f5461f6))
* 同步 21 个 commit 的功能/工程化变化 ([ea93a02](https://github.com/allen-answer/DataOpsStudio/commit/ea93a02f994a3f72768fc9c0a6af3c5d41c94516))
* 文档大盘梳理 — README 重写 + SQL Workbench v0.5+ 同步 + 新增 SECURITY.md ([9564087](https://github.com/allen-answer/DataOpsStudio/commit/9564087348fc697e6e50345f341ffdbd72b06452))

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
