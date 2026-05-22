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

已具备：

- token TTL（`DATAOPS_JWT_TTL_SECONDS`，默认 8 小时）。
- JWT 算法已显式 pin（`algorithms=[HS256]`，无 `alg=none` 混淆攻击面）。
- **token 吊销 / 真 logout**（本切片）。

仍未做（后续切片 / 文档评审 P1 目标态）：

- **refresh token + rotation**：现在是单一 access token，无刷新机制。
- **缩短 access token TTL**：8 小时偏长，目标态约 30 分钟（需配合 refresh）。
- **敏感操作再认证**：下载发放 / 权限变更 / 密钥查看等高危操作前再验一次。
- **MFA**：管理员 / break-glass 强制多因子。
- **前端接入**：Workbench / 顶栏的「退出登录」应调 `POST /api/auth/logout`
  （现仅前端丢 token）。
