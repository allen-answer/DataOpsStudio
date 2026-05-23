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
  - `GET /config/export?include_passwords=true`（导出含密码配置）
  - `POST /config/import`（覆盖式导入 datasources + tasks）
  - `DELETE /api/users/{user_id}`（不可逆删除用户）

## 前端

`AppTopBar.vue` 的 `exportConfig(true)`：fetch → 若 403 含
`step_up_required` → prompt 密码 → verify-password → 写新 token →
`_doExportFetch` 重试一次。

通用 helper `frontend/.../utils/stepUpRetry.ts`：

```ts
await withStepUpRetry(() => apiJson(`/api/users/${id}`, 'DELETE'))
```

- 已接入：`UserManagementView.deleteUser`。
- 未接入：`AIConfigView` 的 AI key 保存（后端已 gate 在 PUT /api/lineage/ai/config
  之外没挂；该端点 admin 偶尔用，被 403 后手动重登可接受，留后续）。

## 后续

- AI 密钥保存端点（`PUT /api/lineage/ai/config`）后端 gate + `AIConfigView` 接
  `withStepUpRetry`。本切片暂未挂。
- 把 `window.prompt` 换成正式 modal 组件，更好的 UX。
- 跟 [token 吊销](./SESSION_HARDENING.md) / refresh rotation（未做）配合：
  每次 verify-password 同时吊销老 jti，杜绝并发会话错乱。
- `AppTopBar.exportConfig` 用 `withStepUpRetry` 重写一次，去掉内联的重试代码。
