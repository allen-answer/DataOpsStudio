# DataOps Studio

面向离线 Windows 环境的 DataOps 工具台——多数据库数据对比、SQL 血缘、ETL 多脚本流程分析和 Schema 元数据辅助，一次部署、离线可用。

## 功能

- **多数据库支持**：DM（达梦）、MySQL、Oracle、DB2
- **数据对比**：单 SQL / 双 SQL 模式，支持字段映射、数值误差容忍、字符串归一化、忽略字段等规则
- **流式对比**：按主键有序结果集边读边归并，降低大结果集内存占用
- **后台任务**：异步执行、状态查询、协作式取消
- **SQL 血缘分析**：基于 `sqlglot` 静态解析表级/字段级血缘，支持 CTE、UNION、子查询、存储过程、动态 SQL
- **多脚本 ETL 分析**：批量上传脚本文件或 ZIP，汇总表级数据流、跨脚本依赖和风险提示
- **血缘图形化**：D3.js 渲染来源表 → 目标表流程图
- **Schema 元数据**：导入 DDL / JSON / TXT 元数据展开 `SELECT *`
- **SQL 辅助**：格式化、只读校验、字段提取、候选 key 提示
- **配置管理**：数据源与任务配置存为本地 JSON，支持导入/导出
- **结果导出**：JSON / Excel，历史结果多选合并导出
- **SPA 前端**：Vue 3 + Tailwind CSS 构建，生产环境仅需静态文件，无需 Node.js
- **Docker 支持**：提供 Dockerfile 与 docker-compose.yml

## 快速开始

```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

浏览器打开 `http://127.0.0.1:8000/spa`。

## 离线 Windows 部署

1. 确保目标机器已安装 Python 3.12
2. 在有网的同版本环境中准备依赖：

```bat
pip download -r requirements.txt -d wheels
```

3. 将项目目录和 `wheels` 拷贝到离线机器：

```bat
py -m venv .venv
.venv\Scripts\activate
pip install --no-index --find-links=.\wheels -r requirements.txt
```

4. 启动：

```bat
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

或双击 `start.bat`。

数据库驱动按需准备离线包：DM（`dmPython`）、MySQL（`pymysql`）、Oracle（`oracledb`）、DB2（`ibm_db`）。

## Docker

```bash
docker compose up --build -d app
```

浏览器打开 `http://127.0.0.1:8000`。`config`、`logs`、`results` 挂载到宿主机，容器重启后数据保留。

## 项目结构

```
.
├── app/
│   ├── api/routes.py          # API 路由
│   ├── compare/engine.py      # 数据对比引擎
│   ├── lineage/               # SQL 血缘分析
│   ├── dbclients/             # 数据库驱动与连接工厂
│   ├── services/              # 配置、导出、任务调度、历史管理等
│   └── utils/                 # 日志、SQL 安全校验、路径工具
├── frontend/frontend/         # Vue 3 SPA 源码
├── static/spa/                # SPA 构建产物（生产用）
├── templates/                 # Jinja2 经典页面
├── config/                    # 数据源和任务配置 JSON
├── tests/                     # 单元测试
├── main.py                    # 应用入口
├── Dockerfile
└── docker-compose.yml
```

## API 概览

| 接口 | 说明 |
|------|------|
| `GET /api/drivers` | 检测可用数据库驱动 |
| `GET/POST /api/datasources` | 数据源 CRUD |
| `POST /api/datasources/{id}/test` | 连通性测试 |
| `GET/POST /api/tasks` | 对比任务 CRUD |
| `POST /api/tasks/{id}/run` | 执行对比任务 |
| `POST /api/tasks/{id}/run-async` | 后台执行对比 |
| `POST /api/tasks/{id}/preview` | 预览源/目标数据 |
| `GET /api/runs/{job_id}` | 后台任务状态 |
| `POST /api/runs/{job_id}/cancel` | 取消后台任务 |
| `GET /api/history` | 历史结果 |
| `POST /api/sql/assist` | SQL 格式化/校验/提取 |
| `GET /config/export` | 导出配置 JSON |
| `POST /config/import` | 导入配置 JSON |

## SQL 安全

执行前会校验 SQL：
- 仅允许 `SELECT` / `WITH` 查询
- 禁止多语句和 `INSERT`、`UPDATE`、`DELETE`、`MERGE`、`DROP`、`ALTER`、`TRUNCATE` 等写入/DDL
- 禁止 `SELECT ... FOR UPDATE`

## 测试

```bash
python -m unittest discover -s tests
```

## License

MIT
