# 贡献指南

感谢你对 DataOps Studio 的关注。

## 提交 Issue

- 使用 Bug Report 模板描述问题，附上复现步骤、预期行为和实际行为
- 使用 Feature Request 模板提出新功能建议

## 提交 PR

1. Fork 本仓库并创建功能分支
2. 代码风格与现有代码保持一致
3. 新增功能请补充单元测试
4. 确保全部测试通过：`python -m unittest discover -s tests`
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

## Git Hooks（强烈建议装）

仓库带一个 pre-commit hook 拦截**服务器登录信息**（IP / SSH key 路径 /
`ssh -i ... user@host` 命令）误提交。clone 后跑一次：

```bash
# Linux / macOS / WSL / Git Bash
bash scripts/install-git-hooks.sh

# Windows PowerShell
powershell -ExecutionPolicy Bypass scripts/install-git-hooks.ps1
```

Hook 源在 `scripts/git-hooks/`，会被 copy 到 `.git/hooks/`。如要新增需要拦截
的模式（比如新生产实例 IP），改 `scripts/git-hooks/pre-commit` 里的
`PATTERNS` 变量，再重跑安装脚本。

**规则**：服务器 IP / SSH key 路径 / 数据库 host 不进仓库；只进运维渠道
或本地 `.claude/` 配置（已 gitignore）。

## 项目结构

```
app/          # 后端代码
frontend/     # Vue 3 SPA 源码
tests/        # 单元测试
```

## License

MIT — 提交 PR 即表示你同意将代码以 MIT 协议授权。
