# Changelog

## [Unreleased]

### Added
- SQL 编辑器组件（Monaco Editor），支持语法高亮和代码补全
- 数据源编辑功能
- 历史记录单条删除功能
- PyMySQL + cryptography 驱动支持

### Changed
- 全面迁移至纯 SPA 架构，移除 Jinja2 服务端模板渲染
- 根路径 `/` 重定向至 `/spa`
- 组件改为异步懒加载（`defineAsyncComponent`）

## [0.1.0] - 2026-04-28

- chore: 清理内部开发文档和 macOS 垃圾文件
- fix: 添加 ZIP bomb 防护、任务状态持久化和单元测试
- feat: 拆分执行历史为数据对比和血缘分析两个标签页
- feat: v7 — SPA 前端静态构建、历史服务排序、任务配置优化
- feat: 添加 Dockerfile 和 docker-compose.yml
- init: 项目初始化

[Unreleased]: https://github.com/allen-answer/DataOpsStudio/compare/v0.1.0...main
[0.1.0]: https://github.com/allen-answer/DataOpsStudio/releases/tag/v0.1.0
