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

## 项目结构

```
app/          # 后端代码
frontend/     # Vue 3 SPA 源码
tests/        # 单元测试
```

## License

MIT — 提交 PR 即表示你同意将代码以 MIT 协议授权。
