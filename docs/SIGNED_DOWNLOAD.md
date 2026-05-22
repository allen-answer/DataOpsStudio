# 签名下载 token

安全加固方案 P1。`/results/*` 是路径式访问 —— URL 可猜（`results/<run_id>.xlsx`），
知道路径且对项目有权就能反复下、永久有效。本切片加短时签名 token：下载链接
带 HMAC 签名、有 TTL，泄露也只在窗口内有效。

实现：`app/services/download_token.py` + `runs.py` / `system.py` 两个端点。

## 流程

1. 前端 `POST /api/runs/{run_id}/downloads` body `{kind: "result" | "excel"}`。
2. 后端校验项目权限 → 解析出该 run 的文件相对路径 → 签发 token，返回
   `{token, download_url, expires_in}`。
3. 前端用 `download_url`（`/api/downloads/{token}`）拉文件。
4. `GET /api/downloads/{token}`：验签 + 校验 `purpose` + `exp` → 用**当前
   用户实时项目权限**再校验 → 路径 traversal 防御 + 后缀白名单 → 返回文件。

## token

JWT 风格（HMAC-SHA256）。claims：`purpose=download` / `run_id` / `rel`
（相对 RESULTS_DIR 的路径）/ `project_id` / `sub` / `iat` / `exp`。

- 密钥从 auth 的 `JWT_SECRET` 用 sha256 派生 —— 跟着 JWT_SECRET 一起轮换，不
  另设 env；`purpose` claim 防止下载 token 跟登录 token 混用。
- TTL：`DATAOPS_DOWNLOAD_URL_TTL_SECONDS`，默认 300 秒。
- token 签名防篡改 —— 用户改不了 `rel` 指向别的文件。

## 纵深防御

即使 token 合法签发，`GET /api/downloads/{token}` 仍做：

- `purpose != "download"` → 401（拒绝拿登录 token 来下载）。
- 当前用户对 `project_id` 无权 → 403（token 里的 project_id 仅记录，以实时
  权限为准）。
- 解析后路径必须在 `RESULTS_DIR` 之下 → 防 path traversal。
- 后缀必须是 `.json` / `.xlsx` / `.parquet`。

## 未覆盖（后续切片）

- **前端接入**：HistoryView / 批量导出等现仍直接拼 `/results/<path>`，应改为
  先 `POST .../downloads` 再用 `download_url`。
- **一次性消费**：现在 token 在 TTL 内可重复使用；如需一次性，加一张 nonce
  消费表（同 `revoked_tokens` 套路）。
- **锁死 `/results/*`**：现 `/results/*` 仍按路径 + 项目权限放行（兼容）；前端
  全切签名 URL 后可把它收紧为仅内部调用。
- 单个 parquet 桶文件下载（现 `kind` 仅 `result` / `excel`）。
