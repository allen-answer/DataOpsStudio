# Step-up 再认证

安全加固方案 P1。某些 admin 动作（含密码导出 / 删用户 / 改 AI 密钥 / 等）
即便用户已登录也应**重新输入一次密码**确认 —— 防误点、防 token 被借用后
立刻执行最危险的事。

实现：JWT `iat` claim 兼作「最近认证时间戳」，stateless，不依赖额外表。

## 流程

1. 用户登录 → token `iat = now`。
2. 用户 5 分钟内点敏感按钮 → 后端检查 `now - iat <= 300` → 直接放行。
3. 用户 5 分钟后点敏感按钮 → 后端 **403** detail 起头 `step_up_required:...`。
4. 前端识别该 detail → `window.prompt` 取密码 → `POST /api/auth/verify-password`。
5. 后端验密码 → 签发新 token（`iat = now`）返回 → 前端写回 localStorage。
6. 前端用新 token 自动重试原请求 → 通过。

## 后端

- `app/services/auth.py`：
  - `ensure_recent_auth(request, max_age=300)` —— 校验 helper，超时抛 403 +
    `step_up_required: ...` detail。
- `app/api/auth.py`：
  - `POST /api/auth/verify-password` body `{password}` —— 验密码 → 签新 token。
- 已挂的敏感端点：
  - `GET /config/export?include_passwords=true`

## 前端

`AppTopBar.vue` 的 `exportConfig(true)`：fetch → 若 403 含
`step_up_required` → prompt 密码 → verify-password → 写新 token →
`_doExportFetch` 重试一次。

## 后续

- 把更多敏感端点挂上 `ensure_recent_auth`：`POST /config/import`、
  `DELETE /api/users/{id}`、`POST /api/lineage/ai/config`（AI 密钥）等。
- 把 `window.prompt` 换成正式 modal 组件，更好的 UX。
- 跟 [token 吊销](./SESSION_HARDENING.md) / refresh rotation（未做）配合：
  每次 verify-password 同时吊销老 jti，杜绝并发会话错乱。
