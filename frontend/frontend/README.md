# DataOps Studio Frontend

这是 DataOps Studio 的 Vue 3 + Vite SPA 前端源码。

开发构建在 Docker 前端容器中执行，依赖安装在 Docker volume 中；Windows 离线生产环境只需要后端服务和 `static/spa/` 构建产物，不需要安装 Node.js、npm 或 Vue。

常用命令：

```bash
docker compose run --rm --no-deps frontend npm run build
```
