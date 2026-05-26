<div align="center">

# DataOps Studio

**一个工作台搞定数据对比 · SQL 血缘 · 作业流编排 · SQL 优化**

[![CI](https://github.com/allen-answer/DataOpsStudio/actions/workflows/ci.yml/badge.svg)](https://github.com/allen-answer/DataOpsStudio/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/allen-answer/DataOpsStudio?include_prereleases&label=release)](https://github.com/allen-answer/DataOpsStudio/releases)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/)
[![Vue](https://img.shields.io/badge/vue-3.x-42b883.svg)](https://vuejs.org/)

[快速开始](#-快速开始) · [功能总览](#-功能总览) · [文档](#-文档) · [部署](#-部署形态) · [贡献](CONTRIBUTING.md)

</div>

---

## 这是什么

数据工程师 / DBA / 数据分析师日常会被以下问题反复打断 —— DataOps Studio 把它们装进同一个工作台:

| 痛点 | 传统做法 | DataOps Studio |
|---|---|---|
| 「这两个库 / 这张 Excel 跟 MySQL 数据对不上」 | 手写 SQL diff、Excel VLOOKUP | 多源对比任务(SQL × SQL / Excel × SQL / CSV × Parquet)+ 字段映射 + 流式归并 |
| 「这个 ETL 改完会影响哪些下游?」 | 翻代码 / 手画流程图 | sqlglot 静态解析 + 9-tab 血缘报告 + 字段级多跳追溯 |
| 「这个慢 SQL 怎么优化?」 | EXPLAIN + 凭经验改 | 方言 EXPLAIN + 规则推断 + AI 复核 + plan diff |
| 「把对比 + 血缘 + 通知串起来定时跑」 | crontab + shell 胶水 | DAG 作业流 + `${var}` 变量 + 局部重跑 + sensor 触发 |
| 「想拿真实业务数据测我的对比脚本,但不能用 prod」 | 手写造数据脚本 | yml DSL 描述场景 + AI 填业务血肉 + 一键生成 |

**支持的数据库**:MySQL · DM 达梦 · Oracle · DB2 · OceanBase(MySQL+Oracle 兼容)。

**部署形态**:本地开发 · Docker(推荐) · Windows 离线 portable 包(74 MB 免装即用) · 一键云端部署。

---

## 🚀 快速开始

最快上手方式 —— Docker 起一个完整环境:

```bash
git clone https://github.com/allen-answer/DataOpsStudio.git
cd DataOpsStudio
docker compose --profile demo-db up -d --build
```

打开 <http://localhost:8010>,用初始管理员账号登录(首次启动会在 `config/users.json` 看到打印的默认密码,登录后立刻在「用户管理」改掉)。

`--profile demo-db` 会顺带起一个内置 MySQL 8 样例库(端口 3307),里面有 `users / users_archive`、`orders / orders_v2` 等带预置差异的表,直接拿来玩对比 / 血缘 / 作业流 demo。

> 其它部署方式(本地 dev / Windows 离线包 / 云端 deploy.sh)见下方 [部署形态](#-部署形态)。

---

## ✨ 功能总览

### 🔍 数据对比

- 多源:**SQL × SQL** · Excel × SQL · CSV × Parquet · 任意组合
- 字段映射(源端 / 目标端不同名列对齐)、数值容差、字符串归一化、忽略列、`schema_policy=strict`
- **流式归并**:按主键有序结果集边读边对,降低对比阶段内存占用
- **大结果落 parquet**:目录 + meta.json + 按 bucket 分页 API,Excel 异步导出
- 结果四桶 `only_source / only_target / diff / same` + Excel 报告

### 🌳 SQL 血缘

- 基于 **sqlglot** 静态解析,**12 个 aspect 模块**:CTE / UNION / 子查询 / 存储过程深度解析 / 动态 SQL / 字段级
- **方言**:MySQL / Oracle / DM 达梦 / OceanBase(MySQL+Oracle)
- **批量 ETL 血缘**:上传 `.sql / .txt / .zip` 一键汇总表级数据流、跨脚本依赖、风险提示
- **9-tab 血缘报告**:概览 / 输入资产 / 输出资产 / 处理过程 / 表级 / 字段级 / 语义 / 影响分析 / AI 兜底
- **资产图谱**:表 / 字段当一等资产,反向引用 + 业务分组 DAG + 多 aspect(PII / SLA / Owner)

### 🛠 SQL 工作台 + 慢 SQL 诊断

- **`/sql-workbench`** —— 多 tab SELECT 工作台,**只读**,18+ endpoint:
  - 元数据树 + 对象搜索 + 表详情
  - 模板库(跨用户 / 跨数据源沉淀常用 SQL)
  - 4 格式导出(CSV / Excel / JSON / SQL Insert,含 Excel 公式注入防御)
  - Explain + 4 条静态规则(`select_star` / `no_where` / `leading_wildcard` / `order_no_limit`)
  - **异步执行 + KILL QUERY 中断**(MySQL 真驱动级)
  - 别名补全 + 6 snippets + 草稿
- **`/sql-diagnosis`** —— 慢 SQL 深度诊断,方言 EXPLAIN + 规则推断 + AI 复核 + plan diff

### 🔄 作业流 + 调度

- DAG 拓扑 + 5 节点类型(params / compare / lineage / http / excel_export)
- 变量插值 `${var}` / `${nodes.X.Y}` + `sql_in` 过滤器 + `when:` 条件
- **局部重跑** `from_node_id`、上游沿用上次 output
- APScheduler cron + `file` / `workflow_success` sensor
- 通知三 channel:webhook / 企业微信 / 邮箱
- OpenLineage emitter:webhook / Marquez / DataHub

### 🧪 场景测试沙盒

- yml DSL 描述虚拟业务场景:表 schema + 偏差注入 + 工作负载消费
- **一键链**:AI 填业务血肉 → 落库 → 建对比任务 → 跑分析 → 校验
- 7 个 column generator + 6 anomaly kind + 4 分布族(lognormal / normal / uniform / exponential)
- 从 datasource 反查 schema → scenario yml(30 分钟手抄变 30 秒)

### 🔐 平台 / 安全

- **JWT + bcrypt** + admin/editor/viewer 三档 RBAC + 多项目空间
- **MFA TOTP** + recovery codes(可强制)
- **Refresh token rotation** + 重放检测
- **Step-up 再认证** —— AI key / 配置导入 / 用户删除等敏感操作要求重输密码
- **签名下载 token** + 一次性 nonce
- **资源防护**:磁盘水位 + per-user / per-project quota + DB 语句超时(4 方言)+ cell 64KB / 总 64MB 内存防护
- **审计日志** + Prometheus `/metrics` + 结构化 JSON 日志
- **CI 安全**:gitleaks + Dependabot + SBOM + SLSA attestation + dependency-review

---

## 📦 部署形态

| 场景 | 入口 | 文档 |
|---|---|---|
| 日常开发(热重载) | `npm run dev` + `uvicorn --reload` | — |
| 生产 / 内部演示 | `docker compose up -d --build` | — |
| 内置样例库演示 | `docker compose --profile demo-db up -d --build` | — |
| 一键本地 → 云端 | `bash scripts/deploy.sh` | — |
| 客户离线现场(Windows) | `scripts/build_offline_windows.ps1` → portable / wheels zip | [`docs/RELEASE_PACKAGES.md`](docs/RELEASE_PACKAGES.md) |

**离线 portable 包**:74 MB 内含 Python 3.12 embeddable + 53 wheels + 应用 + SPA build,目标机器解压双击 `start.bat` 即用,**不需要装系统 Python**。

---

## 📚 文档

```
README.md              ← 你在这
CLAUDE.md              架构 + phase-by-phase 设计决策(深度阅读)
CONTRIBUTING.md        提交 issue / PR 规范 + git hook
SECURITY.md            安全漏洞披露策略
CHANGELOG.md           Conventional Commits 自动生成版本历史
docs/                  设计文档 / 运维手册 / 模块文档(25 份)
```

### 高频文档

| 想做什么 | 看这个 |
|---|---|
| 部署 / 升级 / 发版前跑冒烟 | [`docs/SMOKE_TEST.md`](docs/SMOKE_TEST.md) |
| 选数据库驱动 / 打离线包 | [`docs/DRIVER_MATRIX.md`](docs/DRIVER_MATRIX.md) |
| 当下重点 + 这一轮明确不做 | [`docs/ROADMAP.md`](docs/ROADMAP.md) |
| oncall 备份 / 升级 / 回滚 | [`docs/RUNBOOK.md`](docs/RUNBOOK.md) |
| SQL Workbench 怎么用 | [`docs/SQL_WORKBENCH.md`](docs/SQL_WORKBENCH.md) |
| 作业流变量 / 参数引用语法 | [`docs/PARAMETERS.md`](docs/PARAMETERS.md) |

<details>
<summary><b>完整文档树</b>(点击展开)</summary>

#### 数据对比 / 血缘
- [`COMPARE_RESULT_STORAGE.md`](docs/COMPARE_RESULT_STORAGE.md) — 大结果 parquet 落盘设计
- [`STREAMING_COMPARE_WRITER.md`](docs/STREAMING_COMPARE_WRITER.md) — 流式 writer 内部
- [`PARAMETERS.md`](docs/PARAMETERS.md) — 作业流变量语法

#### SQL 工作台 / 诊断 / 沙盒
- [`SQL_WORKBENCH.md`](docs/SQL_WORKBENCH.md) — 主文档
- [`SQL_TEMPLATES.md`](docs/SQL_TEMPLATES.md) — 模板库
- [`SQL_EXPORT.md`](docs/SQL_EXPORT.md) — 4 格式导出
- [`SQL_EXPLAIN_HINTS.md`](docs/SQL_EXPLAIN_HINTS.md) — Explain 增强 + hints
- [`SQL_DIAGNOSIS.md`](docs/SQL_DIAGNOSIS.md) — 慢 SQL 深度诊断
- [`SQL_PREFLIGHT.md`](docs/SQL_PREFLIGHT.md) — 静态 SQL preflight
- [`SCENARIO_LAB.md`](docs/SCENARIO_LAB.md) — 场景测试沙盒
- [`SCHEMA_IMPORT.md`](docs/SCHEMA_IMPORT.md) — datasource → scenario yml

#### 安全 / 权限
- [`SECURITY.md`](SECURITY.md) — 漏洞披露策略 + 内置安全机制清单
- [`AUTHORIZATION_MATRIX.md`](docs/AUTHORIZATION_MATRIX.md) — 13 模块角色矩阵 SOT
- [`PROJECT_AUTHORIZATION.md`](docs/PROJECT_AUTHORIZATION.md) — 多项目授权
- [`DATASOURCE_ENVIRONMENT_POLICY.md`](docs/DATASOURCE_ENVIRONMENT_POLICY.md) — prod/staging/sandbox 红线
- [`MFA.md`](docs/MFA.md) — TOTP
- [`STEP_UP_AUTH.md`](docs/STEP_UP_AUTH.md) — 敏感操作再认证
- [`REFRESH_ROTATION.md`](docs/REFRESH_ROTATION.md) — token 轮换
- [`SESSION_HARDENING.md`](docs/SESSION_HARDENING.md) — HttpOnly cookie
- [`SIGNED_DOWNLOAD.md`](docs/SIGNED_DOWNLOAD.md) — 签名下载 + nonce
- [`RESOURCE_GUARD.md`](docs/RESOURCE_GUARD.md) — 并发 / 磁盘 / quota
- [`DB_STATEMENT_TIMEOUT.md`](docs/DB_STATEMENT_TIMEOUT.md) — 多方言语句超时
- [`CI_SECURITY.md`](docs/CI_SECURITY.md) — gitleaks/SBOM/SLSA

#### 部署 / 运维
- [`SMOKE_TEST.md`](docs/SMOKE_TEST.md) — 10 分钟冒烟
- [`DRIVER_MATRIX.md`](docs/DRIVER_MATRIX.md) — 驱动选择
- [`RUNBOOK.md`](docs/RUNBOOK.md) — oncall 手册
- [`RELEASE_PACKAGES.md`](docs/RELEASE_PACKAGES.md) — 发布包索引 + 选包决策树

#### 治理
- [`ROADMAP.md`](docs/ROADMAP.md) — 当下重点 + 不做清单

</details>

---

## 🧱 技术栈

**后端**:Python 3.12 · FastAPI · Pydantic v2 · sqlglot · APScheduler · pymysql · oracledb · dmPython

**前端**:Vue 3 · Vite · Pinia · TypeScript · Tailwind CSS · CodeMirror 6 · G6 · Cytoscape · vue-i18n

**存储**:纯 JSON 文件(`config/*.json` mtime 缓存),无外部数据库依赖

**测试**:pytest(1589+ unit/integration)· vitest(48+)· Playwright(e2e)

**CI/CD**:GitHub Actions · release-please · Docker 多阶段构建 · CycloneDX SBOM · SLSA attestation

---

## 🔒 SQL 安全

执行前所有用户提交的 SQL 经过 `app/utils/sql_guard.py` 校验:

- 仅允许 `SELECT` / `WITH`
- 拒绝 `INSERT` / `UPDATE` / `DELETE` / `MERGE` / `DROP` / `ALTER` / `TRUNCATE` 等 DML / DDL
- 拒绝多语句、`SELECT ... FOR UPDATE`、注释绕过

回归用例 `tests/test_sql_guard.py`,新加方言 / 新保留词必跑。

---

## 🧪 测试

```bash
# 后端全量
pytest

# Docker 模式
docker exec dataops-studio pytest

# 前端
cd frontend/frontend && npm test && npm run typecheck

# e2e(可选)
pytest tests/e2e/
```

Swagger UI:<http://localhost:8010/docs>

---

## 🛠 项目结构

```
.
├── app/                    FastAPI 后端
│   ├── api/                19 个领域 router 子模块
│   ├── sqlide/             SQL Workbench(executor / runtime / format / explain / metadata)
│   ├── scenarios/          场景沙盒 DSL + materializer + verifier
│   ├── lineage/            sqlglot 血缘解析(12 aspect)
│   ├── compare/            对比引擎 + parquet writer
│   ├── dbclients/          方言 dialect + 连接池
│   ├── services/           跨领域 service
│   ├── ai/                 provider 抽象 + prompts
│   └── models/             Pydantic 收口
├── frontend/frontend/      Vue 3 SPA 源码
├── docs/                   设计文档
├── tests/                  pytest 单元 + 集成
├── scripts/                构建 / 离线打包 / deploy
├── .github/                workflows / dependabot / templates
├── main.py                 入口
├── Dockerfile              多阶段:node → python
└── docker-compose.yml
```

---

## 🤝 贡献

欢迎 PR — 提交前请阅读 [`CONTRIBUTING.md`](CONTRIBUTING.md):

- 使用 **Conventional Commits**(`feat:` / `fix:` / `docs:` ...)
- 跑 `pytest` + `npm test` + `npm run typecheck` 全绿
- 安装本地 pre-commit hook 防止误提交真实 IP / SSH key / 云密钥
- 真实运维 / 部署 / 客户信息**永不入仓库**

发现安全漏洞请走 [`SECURITY.md`](SECURITY.md) 私下披露,**不要开公开 issue**。

---

## 📄 License

[MIT](LICENSE) — 提交 PR 即表示你同意将代码以 MIT 协议授权。

---

<div align="center">

由数据工程师为数据工程师打造 · [⭐ Star on GitHub](https://github.com/allen-answer/DataOpsStudio)

</div>
