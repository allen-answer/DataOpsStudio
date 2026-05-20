# 贡献指南

感谢你对 DataOps Studio 的关注。

## 提交 Issue

- 使用 Bug Report 模板描述问题，附上复现步骤、预期行为和实际行为
- 使用 Feature Request 模板提出新功能建议

## 提交 PR

1. Fork 本仓库并创建功能分支
2. 代码风格与现有代码保持一致
3. 新增功能请补充单元测试
4. 提交前确保全部测试通过：

   ```bash
   # 后端
   pytest

   # 前端
   cd frontend/frontend && npm test && npm run typecheck && npm run build
   ```

5. PR 标题使用 [Conventional Commits](https://www.conventionalcommits.org/) 格式：
   - `feat:` 新功能
   - `fix:` 修复
   - `chore:` 杂项
   - `docs:` 文档
   - `refactor:` 重构

## 开发环境

```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

## 敏感信息红线（必读）

**真实的运维 / 部署 / 客户信息一律不进仓库。** 包括但不限于：

- 服务器 IP / 主机名 / 域名（生产、测试、内网都算）
- SSH 私钥、`.pem` / `.key` / `.ppk` 文件、`ssh -i ... user@host` 登录命令
- 数据库连接串、真实账号 / 口令、云厂商 Access Key
- 客户名称、客户数据、内部业务标识

这些信息只放运维渠道或本地的 `.claude/` 配置（已 gitignore）。仓库里的
`config/*.example.json`、`.secret-patterns.example` 等模板**只放占位示例**。

两道防线挡住误提交：

1. **本地 pre-commit hook**（强烈建议装，见下）
2. **CI secret scan**（`gitleaks`，`.github/workflows/ci.yml` 的 `secret-scan`
   job，扫工作树 tracked 文件，命中即红）

## Git Hooks（强烈建议装）

仓库带一个 pre-commit hook，拦截私钥 / SSH 登录串 / 云密钥误提交。clone 后跑一次：

```bash
# Linux / macOS / WSL / Git Bash
bash scripts/install-git-hooks.sh

# Windows PowerShell
powershell -ExecutionPolicy Bypass scripts/install-git-hooks.ps1
```

Hook 源在 `scripts/git-hooks/`，会被 copy 到 `.git/hooks/`。

### 通用规则 vs 私有规则

- `scripts/git-hooks/pre-commit` 里只放**通用规则**（私钥块 / SSH 私钥登录命令 /
  `user@IP` 登录串 / AWS Key），**不含任何真实 IP / 主机名 / key 文件名**。
- 跟你环境绑定的私有规则（某台生产实例 IP、部署 key 文件名等）写进仓库根的
  `.secret-patterns.local`：

  ```bash
  cp .secret-patterns.example .secret-patterns.local
  # 编辑 .secret-patterns.local，一行一个 ERE 正则，# 开头是注释
  ```

  `.secret-patterns.local` 已 gitignore，永不入库。pre-commit hook 会自动读取
  并附加到通用规则后面。改完无需重装 hook，下次 commit 即生效。

> 注意：`.secret-patterns.example` 是模板，本身只放占位示例，不要往里写真实值。

## 项目结构

```
app/          # 后端代码
frontend/     # Vue 3 SPA 源码
tests/        # 单元测试
docs/         # 设计文档（含授权矩阵 / 项目级授权规则）
```

## License

MIT — 提交 PR 即表示你同意将代码以 MIT 协议授权。
