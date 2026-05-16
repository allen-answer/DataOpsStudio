# DataOps Studio

轻量 DataOps 工作台：**多数据库数据对比 + SQL 血缘 + 参数化作业流**三件套，配套调度 / 通知 / 鉴权 / 项目空间。

支持开发模式、Docker 生产部署、Windows 离线包三种部署形态。访问地址默认 `http://localhost:8010`。

---

## 快速开始

### Docker（推荐）

```bash
docker compose up -d --build              # 仅 app
docker compose --profile demo-db up -d --build   # app + 内置 MySQL 样例库（端口 3307）
```

### 本地开发

```bash
# 后端
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8010 --reload

# 前端（另开终端）
cd frontend/frontend && npm install && npm run dev
```

### Windows 离线包

```powershell
.\scripts\build_offline_windows.ps1
# 输出 DataOpsStudio-win-offline-<version>.zip
# 目标机器解压 → install.bat → start.bat
```

---

## 功能

- **多数据库**：MySQL / DM 达梦 / Oracle / DB2 / OceanBase（MySQL+Oracle 兼容），按需启用驱动
- **数据对比**：SQL × SQL / Excel × SQL / CSV × Parquet 等任意组合；字段映射、数值容差、字符串归一化、忽略列
- **流式对比**：按主键有序结果集边读边归并，大结果集不撑内存
- **SQL 血缘**：基于 sqlglot 静态解析；CTE、UNION、子查询、存储过程深度解析、动态 SQL 识别
- **多脚本 ETL**：批量上传 `.sql` / `.txt` / `.zip` 汇总表级数据流、跨脚本依赖、风险提示
- **作业流**：DAG 拓扑 + `${var}` 变量插值 + `when:` 条件 + 局部重跑 + 异步执行 + 取消
- **调度 / 通知**：APScheduler cron + file / workflow sensor + webhook / 企业微信 / 邮箱
- **鉴权 / 项目空间**：JWT + admin/editor/viewer 三档 + 多项目过滤 + 审计日志

---

## 部署形态对照

| 场景 | 用什么 | 入口 |
|------|-------|------|
| 日常开发（热重载） | Dev 模式 | `npm run dev` + `uvicorn ... --reload` |
| 生产 / 内部演示 | Docker | `docker compose up -d --build` |
| 内置样例库演示 | Docker + demo profile | `docker compose --profile demo-db up -d --build` |
| 客户离线现场 | Windows 离线包 | `scripts/build_offline_windows.ps1` |

各形态详细步骤、依赖清单、坑见 [`docs/`](docs/) 目录。

---

## 文档导航

| 文档 | 看这个如果你想… |
|------|---------------|
| [`docs/SMOKE_TEST.md`](docs/SMOKE_TEST.md) | 部署 / 升级 / 发版前跑一遍 10 分钟冒烟 |
| [`docs/DRIVER_MATRIX.md`](docs/DRIVER_MATRIX.md) | 选数据库驱动、打离线包前避坑 |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | 知道当下重点 + 这一轮明确不做的事 |
| [`docs/RUNBOOK.md`](docs/RUNBOOK.md) | oncall 备份 / 升级 / 回滚 / 故障排查 |
| [`docs/PARAMETERS.md`](docs/PARAMETERS.md) | 作业流变量 / 参数引用语法（`${var}` / `${nodes.X.Y}` / 过滤器）|
| [`docs/COMPARE_RESULT_STORAGE.md`](docs/COMPARE_RESULT_STORAGE.md) | 设计大数据比对结果的落盘方案 |
| [`CLAUDE.md`](CLAUDE.md) | 架构、设计决策、phase-by-phase 历史 |
| `README_OFFLINE.md` | 仅在离线包内部，给客户机部署同事看 |

---

## API / 测试

- Swagger UI：`http://localhost:8010/docs`
- 全量测试：`pytest`（Docker：`docker exec dataops-studio pytest`）
- 前端单测：`cd frontend/frontend && npm test`
- e2e（可选）：见 `tests/e2e/README` 或 `pytest tests/e2e/`

---

## SQL 安全

执行前所有用户提交的 SQL 经过 `app/utils/sql_guard.py` 校验：

- 仅允许 `SELECT` / `WITH`
- 拒绝 `INSERT` / `UPDATE` / `DELETE` / `MERGE` / `DROP` / `ALTER` / `TRUNCATE` 等 DML / DDL
- 拒绝多语句、`SELECT ... FOR UPDATE`、注释绕过

回归用例见 `tests/test_sql_guard.py`，新加方言 / 新加保留词时必跑。

---

## 项目结构

```
.
├── app/                # FastAPI 后端
├── frontend/frontend/  # Vue 3 SPA 源码
├── static/spa/         # SPA build 产物（gitignore）
├── config/             # 运行时 JSON（gitignore）
├── results/            # 运行产物（gitignore）
├── logs/               # 应用日志（gitignore）
├── docs/               # ★ 文档（本 README 之外的所有细节）
├── tests/              # pytest 单元 + 集成
├── scripts/            # 构建 / 离线打包脚本
├── main.py             # 入口
├── Dockerfile          # 多阶段：node → python
├── docker-compose.yml  # 默认起 app；demo-db profile 起样例 MySQL
└── README.md
```

---

## License

MIT
