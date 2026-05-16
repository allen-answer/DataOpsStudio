# 冒烟测试（Smoke Test）

每次部署 / 升级 / 发版前跑一遍，覆盖关键路径，10 分钟内可完成。所有命令默认在仓库根目录、应用监听 `http://localhost:8010` 的前提下执行。

只跑「能不能用」的最小闭环；详细回归走 `pytest` 全量。

---

## 0. 准备

所有 `curl` 示例都基于一个统一的环境变量，按你的部署方式 export 一次：

```bash
# 已有部署（Docker）或本地 uvicorn（同端口）
export BASE_URL=http://localhost:8010
```

### 起后端

```bash
# 方式 A：Docker
docker compose ps                    # 期望看到 app 容器 healthy
curl -fsS "$BASE_URL/api/drivers" | head -c 200

# 方式 B：本地 uvicorn
uvicorn main:app --host 0.0.0.0 --port 8010 --reload
```

### 起前端（dev 模式 / 改前端代码时必跑）

```bash
cd frontend/frontend
npm install       # 首次或依赖变更
npm run dev       # vite dev server，默认监听 5173
```

- Vite 把 `/api/*`、`/results/*` 等代理到 `vite.config.js` 里配置的后端地址（默认 `http://app:8000`，本地 uvicorn 起在 8010 时要把代理目标改成 `http://localhost:8010`）
- 开发访问地址：**http://localhost:5173**（vite）
- 生产 / Docker 构建产物访问地址：**http://localhost:8010**（FastAPI `/static/spa/`）

冒烟阶段如果只验后端，不需要起前端 dev。

### 登录态

```bash
TOKEN=$(curl -fsS -X POST "$BASE_URL/api/auth/login" \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin"}' | jq -r .access_token)
echo "${TOKEN:0:20}..."   # 非空即可
```

后续 `curl` 加 `-H "Authorization: Bearer $TOKEN"`。

---

## 1. 基础可用性

| # | 检查项 | 命令 / 操作 | 期望 |
|---|--------|------------|------|
| 1.1 | 容器健康 | `docker compose ps` | `Up ... (healthy)` |
| 1.2 | 首屏 | 浏览器打开 `/` | 进登录页（未登录）/ 进首屏（已登录） |
| 1.3 | 静态资源 | 浏览器开 DevTools Network | `assets/*.js` / `*.css` 200，不 404 |
| 1.4 | API 健康 | `curl -fsS "$BASE_URL/api/drivers"` | 返回各驱动 `available` 状态 |
| 1.5 | Metrics | `curl -fsS "$BASE_URL/metrics" \| head` | Prometheus 文本，含 `http_requests_total` |
| 1.6 | OpenAPI | 浏览器开 `/docs` | Swagger UI 正常加载 |

---

## 2. 鉴权 / 项目空间

| # | 检查项 | 操作 | 期望 |
|---|--------|------|------|
| 2.1 | 登录 | 用 admin 账号登录 | 成功，跳工作台 |
| 2.2 | 错口令 | 故意输错密码 | 401 + 中文提示 |
| 2.3 | 切项目 | sidebar dropdown 切到另一项目 | 列表数据按 project 过滤 |
| 2.4 | viewer 权限 | viewer 账号登录后试图编辑数据源 | 编辑按钮灰掉或 403 |

---

## 3. 数据源

| # | 检查项 | 操作 | 期望 |
|---|--------|------|------|
| 3.1 | 列表 | 打开「数据源」页 | 现有数据源全列出 |
| 3.2 | 新建 MySQL（Docker 内 app）| 填 demo MySQL：**host=`mysql8`、port=`3306`**（容器内网） | 「连接成功」 |
| 3.2′ | 新建 MySQL（本地 uvicorn 访问 compose 起的 demo MySQL）| 填 demo MySQL：**host=`localhost`、port=`3307`**（暴露给宿主机的端口） | 「连接成功」 |
| 3.3 | 错配置 | 故意改错 port + 测试 | 友好错误（不要 stacktrace） |
| 3.4 | 驱动缺失 | 选 DB2（未装驱动）+ 测试 | 提示「DB2 driver is not installed」 |

> demo MySQL 容器名 `mysql8`，容器内监听 3306，`docker-compose.yml` 把它映射到宿主机的 3307。app 在容器里时跟它走 Docker 网络（用容器名 + 3306）；app 跑在本地 uvicorn 时只能走宿主机映射（用 localhost + 3307）。

---

## 4. 数据对比（核心路径）

| # | 检查项 | 操作 | 期望 |
|---|--------|------|------|
| 4.1 | 单 SQL 模式 | 选两 datasource + `SELECT id, name FROM users ORDER BY id` + 主键 id | 4 桶计数符合预期 |
| 4.2 | 双 SQL 模式 | source `users` / target `users_archive` | diff 行展开后能看到 changes |
| 4.3 | 流式对比 | 同 4.1 + 勾「流式对比」 | 结果一致，内存不爆 |
| 4.4 | Excel↔SQL | 上传 Excel + 选 datasource | 4 桶 + Excel 导出可下载 |
| 4.5 | 异步运行 | 「后台执行」按钮 | 跳运行详情页，进度条更新，最后 success |
| 4.6 | 取消 | 跑大任务后立刻点「取消」 | 状态变 `cancelled`，不留半成品 |
| 4.7 | 历史 | 「历史」页 | 刚跑的 run 出现，可二次导出 |

---

## 5. SQL 血缘

| # | 检查项 | 操作 | 期望 |
|---|--------|------|------|
| 5.1 | 单脚本 | 贴 `INSERT INTO t1 SELECT * FROM t0` | 图含 t0 → t1 边 |
| 5.2 | 方言 | 切 Oracle / DM + 贴 PL/SQL 存储过程 | 表级血缘 + procedure_segments 非空 |
| 5.3 | 批量 | 上传 ZIP（含多个 .sql） | 跨脚本血缘聚合 |
| 5.4 | 字段血缘 | 切「字段血缘」tab | edges 非空，可点字段展开上下游 |
| 5.5 | 解析错误 | 故意贴一段坏 SQL | UI 给红色 banner + parse_errors 列表 |

---

## 6. 作业流

| # | 检查项 | 操作 | 期望 |
|---|--------|------|------|
| 6.1 | 列表 | 打开「作业流」 | 现有 workflow 列出 |
| 6.2 | DAG 渲染 | 进详情页 | 节点 + 边正常画出 |
| 6.3 | 运行 | 点「运行」 | run 进度条更新；终态 success / failed 显式 |
| 6.4 | 局部重跑 | 选某节点 → rerun from here | 上游 `reused=true`，下游重跑 |
| 6.5 | 取消 | 跑长任务时点取消 | 节点 `cancelled`，下游 `skipped` |
| 6.6 | Artifact | run 完成后看 artifacts | Excel / JSON 可下载 |

---

## 7. SQL 安全（防回归）

```bash
# 这些必须被拒（任意一条没拒掉就是严重 bug）
for SQL in \
  "DROP TABLE t" \
  "INSERT INTO t VALUES (1)" \
  "SELECT 1; SELECT 2" \
  "SELECT * FROM t FOR UPDATE"; do
  echo "--- $SQL"
  python -c "
from app.utils.sql_guard import validate_readonly_sql
try:
    validate_readonly_sql('$SQL')
    print('FAIL: 应该被拒')
except ValueError as e:
    print('OK:', e)
"
done
```

或者跑 `pytest tests/test_sql_guard.py -v`。

---

## 8. 配置导入 / 导出

| # | 检查项 | 操作 | 期望 |
|---|--------|------|------|
| 8.1 | 导出 | `curl -fsS "$BASE_URL/config/export" -o /tmp/cfg.json` | 文件非空，含 datasources / tasks |
| 8.2 | 密码脱敏 | `grep -o 'password' /tmp/cfg.json \|\| echo OK` | API 默认不导出口令 |
| 8.3 | 导入 | UI 上传 `/tmp/cfg.json` | 数据源 / 任务恢复 |

---

## 9. 关键 endpoint 巡检

```bash
# 期望全部 200 / 401（401 表示需要 token，把上面的 $TOKEN 加进去再试）
for EP in \
  "/api/drivers" \
  "/api/bootstrap" \
  "/api/datasources" \
  "/api/tasks" \
  "/api/workflows" \
  "/api/history" \
  "/metrics" \
  "/docs"; do
  CODE=$(curl -s -o /dev/null -w '%{http_code}' "$BASE_URL$EP")
  echo "$CODE  $EP"
done
```

---

## 10. 完成判定

冒烟通过 = 上面 1~9 全部期望达成，且：

- 浏览器 Console 没红色错（白色 warning 可接受）
- `docker logs dataops-studio --tail 200` 没未捕获 stacktrace
- `/metrics` 里 `http_requests_total{status=~"5.."}` 增量 = 0

任意一项不达预期 → 不放行，回滚 + 排查。常见排查走 `docs/RUNBOOK.md`。
