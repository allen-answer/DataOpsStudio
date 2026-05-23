# Changelog

## [Unreleased]

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
