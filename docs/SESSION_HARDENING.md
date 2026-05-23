# 会话加固 —— token 吊销 / 真 logout

安全加固方案 P1。JWT 是无状态的：签发后到 `exp` 前一直有效，原本的 logout
只是客户端丢弃 token —— 挡不住已泄露 / 已复制的副本。本切片加服务端吊销表，
让登出 / 泄露的 token **立刻失效**。

实现：`app/services/auth.py` + `app/api/auth.py` + `sqlite_store` 的
`revoked_tokens` 表。

## 机制

1. **`jti` claim**：`create_access_token` 给每个 token 带一个唯一 `jti`。
2. **吊销表**：`revoked_tokens(jti, exp, revoked_at, user_id)`，落 SQLite，
   重启后仍生效。
3. **`POST /api/auth/logout`**：登录态下把当前 token 的 `jti` 写入吊销表。
4. **校验**：`get_current_user` 每次解码 token 后查吊销表，命中 → 视为无效
   （401）。
5. **prune**：每次吊销时顺带删掉已自然过期（`exp` 已过）的记录，表不膨胀。

## 平滑兼容

本次改动前签发的 token 没有 `jti` claim。`is_token_revoked(None)` 恒为
`False` —— 老 token 不会被误判吊销，在 8 小时 TTL 内自然失效，无需强制所有
在线用户重新登录。

## 现状与缺口

**v0.2.0 全部落地 + Phase 13 + 后续**:

- token TTL —— `DATAOPS_JWT_TTL_SECONDS` 默认 `1800`(30 分钟,云端 docker-compose env)
- JWT 算法显式 pin(`algorithms=[HS256]`)
- token 吊销 / 真 logout(`a4e86e7` + `19fb7c7` 前端接入)
- **refresh token + rotation + reuse detection**(`92bd4b3`):OAuth2 风格,replay 整链 revoke
- **HttpOnly + Secure + SameSite=Strict cookie 存 refresh**(`da93c24`):XSS 偷不到
- **敏感操作再认证(step-up)**(`8057309` + 后续):300s 窗口 + verify-password +
  `withStepUpRetry` helper,覆盖含密码导出 / 配置导入 / 删用户 / AI 密钥保存
- **MFA (TOTP)** + recovery codes(`7f6478d` + `fcc34eb` + `4b81528`):enroll/verify/
  disable + 登录两步流 + 10 个一次性 recovery codes,详见 [MFA.md](./MFA.md)
- **Rate limit**(`5a8605a`):per-IP 10/min + per-username 5/min 滑窗 + 429 + Retry-After
- **Audit log enrich**(`da93c24`):login_success/failure / refresh / mfa_* /
  step_up_* / rate_limit_hit / logout 全套

整套 auth 防御栈现状见 [project-security-hardening memory](../../memory/project_security_hardening.md)。
