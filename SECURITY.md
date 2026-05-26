# Security Policy

DataOps Studio 处理数据库连接、SQL 执行、对比结果文件等敏感操作，安全漏洞会被认真对待。本文档说明支持版本、漏洞披露渠道、响应预期，以及项目内置的安全机制。

---

## 支持的版本

| 版本 | 安全更新 |
|------|---------|
| `0.2.x`（最新） | ✅ 持续 |
| `0.1.x` | ⚠️ 仅 critical CVE |
| `0.0.x` 及更早 | ❌ 不再维护，升级到 0.2.x |

安全补丁通过 [release-please](https://github.com/googleapis/release-please) + Conventional Commits 自动出 PR：

- `fix(security): ...` 走 patch（如 0.2.0 → 0.2.1）
- `fix(security)!: ...` 或 `BREAKING CHANGE:` 走 major

历次版本见 [`CHANGELOG.md`](CHANGELOG.md)。

---

## 报告漏洞

**请勿在公开 issue 报告安全漏洞。**

走以下任一渠道私下披露：

1. **GitHub Security Advisory（推荐）** —— 在仓库 [Security](https://github.com/allen-answer/DataOpsStudio/security/advisories/new) tab 点 "Report a vulnerability"，仅 maintainer 可见
2. **邮件** —— 暂未设立专用安全邮箱；先走 GitHub Security Advisory，紧急情况在 issue 标题写 `[SECURITY contact request]` 后我会私下回联系方式

### 报告建议附上

- 漏洞类型（XSS / SQL 注入 / 权限绕过 / SSRF / 信息泄露 / 拒绝服务 / ...）
- 受影响版本（commit hash 或 release tag 最好）
- 复现步骤（最小化 PoC）
- 影响评估（攻击者能拿到什么 / 做什么）
- 你建议的修复方向（可选）

### 响应时间预期

| 阶段 | 目标 |
|------|------|
| 收到确认 | 3 个工作日内 |
| 初步评估 + 严重程度分级 | 7 个工作日内 |
| 修复 + 发版 | critical/high 30 天内；medium/low 下一个 minor |
| 公开披露 | 修复发版后 30~90 天（视严重程度） |

不接受单纯 dependency CVE 报告（已由 Dependabot 自动监控）；除非该 CVE 在本项目里有具体可利用路径。

---

## 内置安全机制

本节列出当前已落地的安全防线，方便外部审计 / 渗透测试时定位边界。

### 认证 / 会话
- **JWT HS256** + bcrypt 密码哈希（`app/services/auth.py`）
- **MFA TOTP**（`docs/MFA.md`） + recovery codes，admin 可强制开启
- **Refresh token rotation** + 重放检测（`docs/REFRESH_ROTATION.md`） —— 老 refresh token 复用会撤销整条 chain
- **Step-up 再认证**（`docs/STEP_UP_AUTH.md`） —— AI key / 配置导入 / 用户删除等敏感操作要求重输密码
- **HttpOnly cookie** + Secure + SameSite（`docs/SESSION_HARDENING.md`）
- **Rate limit** —— 登录 / MFA / refresh 端点

### 授权
- **三档 RBAC**：admin / editor / viewer，router 级 `Depends(require_role(...))`
- **多项目空间**：所有资源带 `project_id`，列表 endpoint 接 `?project_id=` 过滤；详见 [`docs/PROJECT_AUTHORIZATION.md`](docs/PROJECT_AUTHORIZATION.md)
- **13 个 API 模块的角色矩阵 SOT** —— [`docs/AUTHORIZATION_MATRIX.md`](docs/AUTHORIZATION_MATRIX.md)
- **数据源环境策略** —— prod / staging / sandbox 三档红线（[`docs/DATASOURCE_ENVIRONMENT_POLICY.md`](docs/DATASOURCE_ENVIRONMENT_POLICY.md)）

### SQL 安全
- **只读白名单** —— SQL Workbench / 诊断 / preflight 全走 `app/utils/sql_guard.py`：仅 SELECT / WITH，拒 DML / DDL / 多语句 / `SELECT FOR UPDATE` / 注释绕过
- **回归用例**：`tests/test_sql_guard.py`，新加方言 / 新保留词时必跑
- **`allow_select` 二级开关**：即使 SQL 过 guard，prod 数据源仍要 admin 显式开启
- **DB 语句超时**：MySQL `MAX_EXECUTION_TIME` / Oracle/DM `callTimeout` / DB2 `SET_OPTION`（[`docs/DB_STATEMENT_TIMEOUT.md`](docs/DB_STATEMENT_TIMEOUT.md)）
- **静态 preflight**：[`docs/SQL_PREFLIGHT.md`](docs/SQL_PREFLIGHT.md) AST 检查不连库

### 资源 / 拒绝服务防护
- **Cell / 总结果内存防护**：单 cell 截 64KB / 整结果硬上限 64MB（SQL Workbench executor）
- **磁盘水位** + per-run quota + per-project quota（[`docs/RESOURCE_GUARD.md`](docs/RESOURCE_GUARD.md)）
- **并发配额**：per-user / per-datasource / per-project cap
- **查询中断**：MySQL 主动发 `KILL QUERY`；Oracle/DM 走 callTimeout

### 数据传输 / 文件
- **签名下载 token** + 一次性 nonce（[`docs/SIGNED_DOWNLOAD.md`](docs/SIGNED_DOWNLOAD.md)） —— 取代可猜的 `/results/*` 直链
- **Excel 公式注入防御**：导出 Excel 时 `= + - @ \t \r` 开头的字符串 prepend `'`（[`docs/SQL_EXPORT.md`](docs/SQL_EXPORT.md)）
- **API key 加密落盘**：`config/lineage_ai.json` 用 secret_crypto 加密
- **配置 0600 权限**：所有 `config/*.json` 落盘权限收紧

### 审计 / 观测
- **审计日志**：`logs/audit.jsonl` append-only，所有 mutating endpoint 自动落
- **结构化 JSON 日志**：`DATAOPS_LOG_FORMAT=json` 启用，含 request_id 链路
- **Prometheus `/metrics`**：HTTP 计数 / 耗时 / AI 用量 / lineage index 计数

### CI / 供应链
- **secret-scan**：CI `gitleaks` job 扫工作树
- **本地 pre-commit hook**：拦私钥 / SSH 登录串 / 云密钥（详见 [`CONTRIBUTING.md`](CONTRIBUTING.md)）
- **Dependabot**：依赖每周扫
- **SBOM**：CI 出 CycloneDX SBOM（backend + frontend），90 天 retention
- **SLSA attestation**：release.yml 给 Windows offline zip 签构建来源
- **dependency-review-action**：PR 拦 high/critical CVE
- 详见 [`docs/CI_SECURITY.md`](docs/CI_SECURITY.md)

---

## 已知边界 / 非威胁模型

本项目**不**针对以下场景设计防护，部署方需自行处置：

- **公网无 reverse proxy 直接暴露 8010 端口** —— 假定上游有 nginx / Cloudflare 做 TLS 终止 + IP allowlist
- **共享部署的多租户隔离强保证** —— 当前 project_id 是 application-level 隔离，不是数据库 schema 级；高敏感场景请按项目独立部署实例
- **物理机被攻破** —— `config/*.json` 加密 API key 而已，数据库密码是 reversible 加密（不是 hash），主机 root 能解
- **DDoS 流量层防护** —— 应用层限流仅防业务侧滥用，L3/L4 DDoS 走上游基础设施

---

## 历史安全补丁

完整列表见 [`CHANGELOG.md`](CHANGELOG.md)。重要节点：

- **v0.2.0**（2026-05-24）—— 安全加固 27 commits 收工：MFA + recovery codes + refresh rotation + reuse detection + rate limit + HttpOnly cookie + audit enrich + 签名下载
- **v0.1.x** —— P0.4 后端 endpoint 强制鉴权：13 个 API 文件全挂 `Depends(get_current_user)` + role check

---

## 致谢

如你的报告被采纳，我会在 CHANGELOG 和 GitHub Security Advisory 致谢（除非你要求匿名）。
