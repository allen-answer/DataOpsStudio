# Refresh Token Rotation

OAuth2 风格的会话延展机制。配合短 access token 把「token 被盗的暴露窗口」
从默认 8 小时砍短到几分钟级别 + 提供**重放检测**让被盗的 refresh 立刻自毁。

实现：`app/services/refresh.py` + `sqlite_store` 的 `refresh_tokens` 表 +
`app/api/auth.py` 的 login / logout / refresh 端点。

## 机制

### 登录 → 拿双 token

```
POST /api/auth/login {username, password}
  → 200 LoginResponse {
      access_token,             # 短期（默认 8h，env 调）
      refresh_token,            # 长期（默认 7 天）
      expires_in, refresh_expires_in,
      user, ...
    }
```

前端把 `access_token` 留在 localStorage 当主凭据；`refresh_token` 单独存（
也在 localStorage，但读取路径不同，给将来切 secure HttpOnly cookie 留口子）。

### access 过期 → 用 refresh 换新对

```
POST /api/auth/refresh {refresh_token}
  → 200 LoginResponse { 新 access, 新 refresh_token, user, ... }
  → 401 if token 无效 / 过期 / 已重放
```

**关键性质：每次 refresh 老 refresh 立即失效**。DB 里老 refresh 标记
`replaced_by=新jti` —— 同一 refresh 不能用第二次。

### logout → 整条 refresh 链一锅端

```
POST /api/auth/logout
  → 200 (调用方已带 access token)
```

服务端：
- 把当前 access token 的 jti 加入 `revoked_tokens` 表（已有）
- **同时** `revoke_refresh_chain(user_id)` —— 用户名下所有 active refresh 一并标
  `revoked_at`

退出后任何老 token（access 或 refresh）都死。

## 重放检测（refresh rotation 的核心安全性质）

按 RFC 6749 Sec 10.4 + OAuth 2.0 BCP（RFC 8252）。

**正常路径**：用户的浏览器持续 rotation —— 每次 refresh 后老的标
`replaced_by`，下次浏览器拿新的去换。

**被盗路径**：攻击者偷到一个 refresh，去 rotation —— 服务端给攻击者签出
新对，攻击者跑路；同时 DB 里老 refresh 的 `replaced_by` 已填。

**用户回来正常浏览，他的浏览器还存着老 refresh**，去 rotation —— 服务端
看到 `replaced_by` 非空 → 「**这个 refresh 已经被用过了**」→ **视为盗用** →
**整条用户 refresh 链立即全部 `revoked_at`**。

效果：
- 攻击者拿到的新 refresh：也被 revoke 了，访问 `/api/auth/refresh` 401
- 用户的浏览器：refresh 也死，必须输密码重新登录
- 这给用户一个明确的「我被盗了」信号 → 改密码 / 检查

代码里这一段：

```python
# services/refresh.py:verify_refresh_token
if replaced_by:
    logger.warning("refresh reuse detected user_id=%s ... revoking chain")
    revoke_refresh_chain(user_id)
    return None
```

## env 配置

| 变量 | 默认 | 含义 |
|---|---|---|
| `DATAOPS_JWT_TTL_SECONDS` | `28800`（8h） | access token 寿命。**配合 refresh 时建议调短到 1800（30min）**，前端用 refresh 续期 |
| `DATAOPS_REFRESH_TTL_SECONDS` | `604800`（7d） | refresh token 寿命。`0` 关 refresh 机制（login 不返 refresh，前端走老路径） |

## 不做的（明确延后）

- **HttpOnly secure cookie 存 refresh**：localStorage 存 XSS 可读，cookie
  HttpOnly+SameSite=Strict 更紧。改动涉及前端 CSRF 防护（双 token cookie /
  SameSite 策略），单独切片。
- **前端自动刷新拦截器**：access 401 时静默调 /refresh 重试原请求；本提交
  只做后端 MVP。
- **短 access TTL 切换**：默认仍 8h，等前端 interceptor 上线后切 30min。

## 测试

`tests/test_refresh_rotation.py` 18 测，覆盖：

- issue / verify / rotate 正反例
- **reuse detection**：rotate 后老 refresh 再用 → None + 整条 chain revoke
  + 用户名下别的 refresh 也死
- revoke 隔离不影响别的用户
- prune 删过期记录
- env=0 关闭 refresh
- 端点：login 返双 token / refresh 正常 rotation / refresh 重放 401 + 触发
  chain revoke / refresh 空 400 / 错 token 401 / logout 真清干净

## 单用户场景的真实价值

doc 评审里 refresh rotation 排在「单用户 ROI 一般」。本切片做完后的实际收益：

- **正常使用**：零变化（access 仍 8h，前端不动）。
- **真出事时**：access token 被 keylogger / 浏览器漏洞偷了 → 攻击者用了一次
  refresh → 你下次登录用浏览器里的老 refresh → 服务端立即识别 → 你被踢出 +
  攻击者也被踢 + 你知道「有问题」。

→ **是「事后审计 / 被盗检测」机制**，不是「预防」。配合 MFA 才完整：MFA 防
预防偷,refresh rotation 防偷了后的延展利用 + 给受害者信号。
