# DataOps Studio SPA 版 Docker 开发部署说明

本目录是 DataOps Studio 的独立 SPA 改造副本。Docker 主要用于开发、构建和验证；Windows 离线生产部署请优先参考 `WINDOWS_OFFLINE_DEPLOY.md`。

## 本地开发启动

```bash
docker compose up -d app frontend
```

- SPA 开发入口：`http://127.0.0.1:5173/static/spa/`
- 后端 API / 原 Jinja 页面：`http://127.0.0.1:8010`
- 后端直接托管的生产构建入口：`http://127.0.0.1:8010/spa`

前端依赖安装在 Docker volume 中，不写入本地项目目录。生产运行只需要 `static/spa/` 构建产物，不需要 Node.js / npm / Vue。

## 前端构建

```bash
docker compose run --rm --no-deps frontend npm run build
```

构建产物输出到：

```text
static/spa/
```

FastAPI 通过 `/spa` 返回 `static/spa/index.html`，静态资源走原有 `/static` 挂载。

## 使用的镜像

当前 SPA 副本使用：

```text
node:20-alpine
data_compare_tool_spa-app:latest
data_compare_tool-app:latest
python:3.12-slim
```

其中 `data_compare_tool_spa-app:latest` 基于已有 `data_compare_tool-app:latest` 派生，并从本地 wheel 安装：

```text
wheels/sqlglot-30.6.0-py3-none-any.whl
```

## Windows Docker 离线部署建议

在线机器准备镜像包：

```bash
docker save node:20-alpine data_compare_tool-app:latest data_compare_tool_spa-app:latest -o data_compare_tool_spa_images.tar
```

离线 Windows 导入：

```bash
docker load -i data_compare_tool_spa_images.tar
docker compose up -d app frontend
```

如果只运行生产构建入口 `/spa`，可以不启动 `frontend` 服务：

```bash
docker compose up -d app
```

前提是 `static/spa/` 已经随项目包一起带过去。
