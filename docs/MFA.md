# MFA (TOTP)

第二因子认证。用户密码泄露不再 = 账号沦陷 —— 还需要绑过 TOTP app
（Google Authenticator / Authy / 微软 Authenticator）的设备上的 6 位码。

实现：`app/services/mfa.py`（pyotp） + `app/api/mfa.py` + `app/api/auth.py`
登录两步流。TOTP 标准 RFC 6238，30s 窗口，valid_window=1 容忍前后各 30s
时钟漂移。

## Enroll 流程（admin 绑定 MFA）

1. **登录态下**调 `POST /api/auth/mfa/enroll`（要 step-up）→ 服务端：
   - `pyotp.random_base32()` 生成 32 字符 base32 secret
   - 用 `secret_crypto` 加密后落 `users.json` 的 `mfa_secret_encrypted`
   - **暂不**置 `mfa_enabled=True`，等 verify
   - 返回 `{secret, provisioning_uri, verified: false}`

2. 前端用 `provisioning_uri`（`otpauth://totp/DataOpsStudio:admin?secret=...&issuer=DataOpsStudio`）
   渲染 QR；同时显示明文 secret 给手动填的用户。

3. 用户打开 TOTP app 扫码 → app 立刻显示 6 位轮换码。

4. 用户把 6 位码填进前端 → `POST /api/auth/mfa/verify` body `{code}`（要 step-up）。
   服务端用 STORED secret 验码：
   - 通过 → `mfa_enabled=True`，返回 `{ok: true}`
   - 不通过 → 401，`mfa_enabled` 仍 False（用户可以重试 / 重新 enroll）

## 登录两步流（启用 MFA 后）

```
POST /api/auth/login {username, password}
  →  user.mfa_enabled = False:  返回 LoginResponse {access_token, user, ...}（同前）
  →  user.mfa_enabled = True:   返回 {mfa_required: true, mfa_token, expires_in: 300}
                                 access_token 空、user 空（不泄露）

POST /api/auth/mfa/challenge {mfa_token, code}
  →  code 对：    返回 LoginResponse {access_token, user, ...}
  →  code 错：    401
  →  mfa_token 无效/过期： 401
```

`mfa_token` 是 5 分钟有效的 JWT，purpose=`mfa_challenge`，签名密钥从
`JWT_SECRET` sha256 派生（隔离登录 token），仅 `/mfa/challenge` 认。

## 关闭（admin 自己关）

`POST /api/auth/mfa/disable` body `{code}` —— 必须**同时**满足：
- 当前 token 近 300s 内重新输过密码（step-up）
- 提交一个有效的 OTP 6 位码

双重确认防止 token 泄露后被悄悄关 MFA 锁死用户。

## 安全设计

- **secret 加密落盘**：`mfa_secret_encrypted` 用 `secret_crypto`（Fernet）加密，
  跟 AI API Key 同一套密钥。`users.json` 漏出去也别人看不到。
- **`/api/auth/me` 不泄露 secret**：`_redact()` 把 `mfa_secret_encrypted` + `password_hash`
  都抹空。
- **enroll/verify/disable 都要 step-up**：阻止「token 被偷 → 攻击者悄悄换 MFA secret」。
- **`valid_window=1`**：允许 30s 时钟漂移；超过 30s 的 OTP 拒绝。
- **TOTP 不是 HOTP**：基于时间，没 counter 同步问题；丢失设备只能 disable + 重新 enroll。

## 未做（recovery codes）

掉手机 + 没绑别的设备 = 进不去。**MVP 没生成 recovery codes**。补丁路径：
admin 直接编辑 `users.json` 把 `mfa_secret_encrypted` 和 `mfa_enabled` 清掉 +
`user_store.invalidate_cache()`。或者后续切片加 recovery code（enroll 时生成 10
个一次性码，store hashed，OTP 失败时可走 recovery 路径）。

## API 总览

| 端点 | 鉴权 | step-up | 用途 |
|---|---|---|---|
| `GET /api/auth/mfa/status` | 登录 | 否 | 返回 `{enabled, enrolled}` |
| `POST /api/auth/mfa/enroll` | 登录 | 是 | 生成 secret 落盘 + 返 QR URI |
| `POST /api/auth/mfa/verify` | 登录 | 是 | 提交 OTP 启用 MFA |
| `POST /api/auth/mfa/disable` | 登录 | 是 | 关 MFA（要 OTP + step-up）|
| `POST /api/auth/login` | anon | 否 | 验密码;启 MFA 时返 mfa_token |
| `POST /api/auth/mfa/challenge` | anon | 否 | 用 mfa_token + OTP 换正式 access token |

## 单用户场景的 ROI 说明

doc 评审里 MFA 排在「单用户场景 ROI 一般」—— 但**真做了以后 ROI 不低**：
admin 账号是公网 IP 上唯一的认证入口,密码 = 唯一防线时,keylogger / 钓鱼
/ 撞库一旦得手就完全沦陷。MFA 让攻击者**还要**拿到你手机才能登。

代价:每次登录多一步 6 位码。前端启用 MFA 入口在 AI 配置 / 账户设置(留前端
切片接)。后端目前已完整支持,可以直接 curl 走一遍 enroll/verify 流程开启。
