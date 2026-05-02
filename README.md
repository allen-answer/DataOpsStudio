# DataOps Studio

一个轻量 DataOps 工作台：多数据库数据对比、SQL 血缘、ETL 多脚本流程分析、可编排的作业流和 Schema 元数据辅助。

支持三种部署形态：开发模式、Docker 生产部署、Windows 离线包。

## 功能

- **多数据库支持**：DM（达梦）、MySQL、Oracle、DB2，按需启用驱动
- **数据对比**：单 SQL / 双 SQL 模式 + Excel vs SQL / Excel vs Excel；字段映射、数值误差容忍、字符串归一化、忽略字段等规则
- **流式对比**：按主键有序结果集边读边归并，降低大结果集内存占用
- **作业流**：参数节点 + 对比节点 + 血缘节点 + HTTP 节点 + Excel 导出节点；DAG 拓扑、`when:` 条件、`${var}` 变量插值、局部重跑
- **后台任务**：异步执行、状态查询、协作式取消
- **SQL 血缘分析**：基于 `sqlglot` 静态解析，支持 CTE、UNION、子查询、存储过程深度解析、动态 SQL 识别
- **多脚本 ETL 分析**：批量上传脚本文件或 ZIP，汇总表级数据流、跨脚本依赖和风险提示
- **血缘图形化**：G6 渲染 + focal/N 跳 BFS、Schema combo 折叠、表视图逃生通道
- **Schema 元数据**：导入 DDL / JSON / TXT 元数据展开 `SELECT *` 和未限定列名
- **SQL 辅助**：格式化、跨方言转换、只读校验、字段提取、候选 key 提示
- **配置管理**：数据源 / 任务 / 作业流配置存为本地 JSON，支持导入 / 导出
- **结果导出**：JSON / Excel，历史多选合并导出

应用访问地址：**http://localhost:8010**。

## 部署形态选择

| 场景 | 选什么 | 入口 |
|------|--------|------|
| 日常开发 | 开发模式 | `npm run dev` + `uvicorn ... --reload` |
| 生产部署 / 内部演示 | Docker 模式 | `docker compose up -d --build` |
| 客户离线现场 | Windows 离线包 | `scripts/build_offline_windows.ps1` 打包 |

---

## 开发模式

后端用本地 Python，前端走 Vite 开发服务器，API 请求自动代理到后端。**改前端立即热重载，改后端 `--reload` 自动重启**。

### 后端

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8010 --reload
```

### 前端

```bash
cd frontend/frontend
npm install
npm run dev
```

Vite 默认把 `/api/*`、`/results/*` 等请求代理到 `http://app:8000`（在 docker compose 里跑后端时的地址）。如果后端用 `--port 8010` 跑在本地，把 `vite.config.js` 里的代理目标改成 `http://localhost:8010`。

### 跑测试

```bash
pytest                              # 全部 unit + HTTP 集成测试
pytest tests/test_compare_engine.py # 单文件
```

浏览器 e2e（可选，catch render-time throw 那种 UI bug）：

```bash
pip install -r requirements-e2e.txt
playwright install chromium
# 应用必须在 :8010 跑（docker compose up -d / 本地 uvicorn）
pytest tests/e2e/
```

主 `pytest` 默认跳过 e2e，避免 unit 流程被 chromium 二进制拖慢。

前端构建（一次性产 SPA 用于本地预览）：

```bash
cd frontend/frontend && npm run build
# 输出落到 ../../static/spa/，由 FastAPI 在 /static/spa/ 下提供
```

> **注意**：`static/spa/index.html` 和 `static/spa/assets/` 已被 `.gitignore` —— 这是 build 产物，不入库。手写资源 `static/spa/favicon.svg` 和 `static/spa/icons.svg` 仍然跟踪。

---

## Docker 生产部署

`Dockerfile` 是多阶段的：node 阶段构建前端 → python 阶段安装依赖并打入 SPA 产物。**不依赖宿主机预先 `npm run build`**。

```bash
docker compose up -d --build
```

服务起来后访问 `http://localhost:8010`。MySQL 8 暴露在 `localhost:3307`（容器名 `mysql8`，内部端口 3306），首次启动会执行 `init_db/01_init.sql` 初始化测试数据。

挂载到宿主机的目录（容器重启后保留）：

- `./config` —— 数据源、任务、作业流、jobs JSON
- `./results` —— 每次运行的结果文件
- `./logs` —— 应用日志

`static/` 不再挂载 —— 镜像里 build 出的产物就是权威。修改前端代码后必须 `docker compose up -d --build` 才能进镜像；如果想热改前端，请用上面的「开发模式」（vite dev server）。

修改 Python 代码同样要重建镜像：

```bash
docker compose up -d --build app
```

---

## Windows 离线模式

适用于客户离线现场、不能装 Node.js / 不能联网的部署环境。打包脚本会一次性产出含 SPA 产物 + Python wheels 的 zip，目标机器只需要装 Python 3.12 即可。

### 在有网的机器上打包

```powershell
# Windows PowerShell
.\scripts\build_offline_windows.ps1
```

或指定版本号：

```powershell
.\scripts\build_offline_windows.ps1 -Version 0.2.0
```

输出 `DataOpsStudio-win-offline-{version}.zip`。

### 在离线机器上部署

1. 把 zip 拷过去解压；
2. 双击 `install.bat`（创建 venv，离线装 wheel）；
3. 双击 `start.bat`（启动 uvicorn，监听 8010）；
4. 浏览器打开 `http://localhost:8010`。

详见解压后的 `README_OFFLINE.md`。

数据库驱动按需准备：DM (`dmPython`)、MySQL (`pymysql`)、Oracle (`oracledb`)、DB2 (`ibm_db`)。打包脚本会把 `requirements.txt` 中声明的驱动（默认 `pymysql`）一起 download 进 wheels 目录。

---

## 项目结构

```
.
├── app/
│   ├── api/                    # HTTP 端点（按领域拆 10 个子模块）
│   │   ├── routes.py           #   聚合 router（include 各子模块）
│   │   ├── _shared.py          #   跨子模块共用校验 helper
│   │   ├── system.py           #   /, /spa, /api/drivers, /api/bootstrap, /results
│   │   ├── datasources.py      #   /api/datasources/*
│   │   ├── tasks.py            #   /api/tasks/* (含 preview)
│   │   ├── runs.py             #   /api/runs/{job_id}/* (异步任务状态/cancel)
│   │   ├── workflows.py        #   /api/workflows/*
│   │   ├── workflow_runs.py    #   /api/workflow-runs/* (含 rerun)
│   │   ├── history.py          #   /api/history + /history/export
│   │   ├── lineage.py          #   /api/lineage/*
│   │   ├── uploads.py          #   /api/preview/columns + /api/sql/assist + /api/uploads/excel
│   │   └── config_io.py        #   /config/import + /config/export
│   ├── models/                 # Pydantic schema（按领域拆 5 个子模块）
│   │   ├── common.py           #   DatabaseType / SqlMode / SourceKind
│   │   ├── datasource.py       #   DataSource(Create)
│   │   ├── compare.py          #   CompareTask / CompareResult / HistoryItem 等
│   │   ├── workflow.py         #   Workflow / Node / Asset / Artifact / Run / Job
│   │   └── responses.py        #   API 响应专用 schema（含 lineage 结果）
│   ├── workflow/               # 作业流执行运行时
│   │   ├── nodes/              #   每种节点一个文件：params/compare/lineage/http/excel_export
│   │   └── registry.py         #   NODE_RUNNERS 注册表
│   ├── compare/engine.py       # 数据对比引擎
│   ├── lineage/                # SQL 血缘分析（analyzer + batch_analyzer）
│   ├── dbclients/              # 数据库驱动与连接工厂
│   ├── readers/                # SQL / Excel 读取抽象
│   ├── services/               # 配置存储、任务调度、作业流引擎、历史归档
│   └── utils/                  # 日志、SQL 安全校验、路径工具
├── frontend/frontend/          # Vue 3 SPA 源码（Tailwind + G6 + CodeMirror）
├── static/spa/                 # SPA build 产物（gitignore，docker / release 时生成）
├── config/                     # 运行时 JSON 配置（gitignore，每个 clone 独立）
├── init_db/                    # docker mysql 初始化 SQL
├── tests/                      # 单元 / 集成测试（pytest）
├── scripts/                    # 构建 / 离线打包脚本
├── results/                    # 运行产物（gitignore）
├── logs/                       # 应用日志（gitignore）
├── main.py                     # FastAPI 入口
├── Dockerfile                  # 多阶段 build：node → python
├── docker-compose.yml          # 本地一键起：app + mysql8
├── README.md                   # 本文件
└── README_OFFLINE.md           # 离线模式专用说明（也会进 release zip）
```

详细架构与关键设计决策见 `CLAUDE.md`。

## API 概览

`http://localhost:8010/docs` 有 Swagger UI 完整契约。常用端点：

| 接口 | 说明 |
|------|------|
| `GET /api/bootstrap` | 首屏拉取（数据源 / 任务 / 作业流 / 历史） |
| `GET /api/drivers` | 数据库驱动可用性 |
| `POST /api/datasources/{id}/test` | 数据源连通性测试 |
| `POST /api/tasks/{id}/run-async` | 后台执行对比任务 |
| `POST /api/workflows/{id}/run-async` | 后台执行作业流 |
| `POST /api/workflow-runs/{run_id}/rerun` | 从指定节点局部重跑 |
| `POST /api/lineage/batch/analyze` | 批量 SQL 血缘分析 |
| `POST /api/sql/assist` | SQL 格式化 / 校验 / 字段提取 |
| `GET /config/export` / `POST /config/import` | 配置导入导出 |

## SQL 安全

执行前会校验所有用户提交的 SQL：

- 仅允许 `SELECT` / `WITH` 查询
- 禁止多语句和 `INSERT`、`UPDATE`、`DELETE`、`MERGE`、`DROP`、`ALTER`、`TRUNCATE` 等写入 / DDL
- 禁止 `SELECT ... FOR UPDATE`

## License

MIT
