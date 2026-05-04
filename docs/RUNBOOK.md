# 生产 runbook

oncall 速查 —— 备份 / 升级 / 回滚 / 灾备 / 监控告警 / 故障排查的具体命令清单。
所有命令默认在仓库根目录执行（`/path/to/DataOpsStudio`），WSL / Linux 通用。

---

## 1. 备份

应用所有状态在 4 类落盘文件里，备份这 4 类即可全量恢复：

```bash
# 一键备份（推荐：每天 cron / 每次升级前手动）
TS=$(date +%Y%m%d-%H%M%S)
tar czf "backups/dataops-${TS}.tar.gz" config/ results/ logs/audit.jsonl logs/ai_usage.jsonl
```

| 路径 | 内容 | 备份频率 |
|------|------|---------|
| `config/*.json` | 数据源 / 任务 / 作业流 / 用户 / 项目 / AI 配置 | **每天** |
| `results/` | 历史运行的对比结果 + workflow runs JSON + 导出 Excel | 按需（一次跑大的之后） |
| `logs/audit.jsonl` | 审计日志（admin 通过 `/api/audit-logs` 查） | **每天**（合规留存） |
| `logs/ai_usage.jsonl` | AI 调用 token 消耗记录 | 按需（成本核算用） |

**API key 安全**：`config/lineage_ai.json` 里 `api_key_encrypted` 字段已用
`config/.dataops_secret.key` 加密。备份时**两个文件都要拿到**才能恢复，缺一个
就解不出 API key。`.dataops_secret.key` 不要提交版本控制（已 gitignore）。

---

## 2. 升级

```bash
# 1. 备份（见上）
# 2. 拉新代码
git fetch origin && git checkout main && git pull origin main

# 3. 重建 + 重启容器（前后端都打入镜像，一次 build 全覆盖）
docker compose up -d --build

# 4. 验证：等容器 healthy（HEALTHCHECK 30s 周期，start_period 30s）
docker compose ps
# 期望看到 STATUS = "Up 30 seconds (healthy)"

# 5. 跑测试基线 + smoke check
docker exec dataops-studio python -m pytest -x  # -x 失败立即停
curl -fsS http://localhost:8010/api/drivers | head -c 200
curl -fsS http://localhost:8010/metrics | head -20
```

**注意**：

- `config/*.json` / `results/` / `logs/` 走 bind mount，重建镜像不会丢
- 跑中的对比 / 作业流任务在重启时变 `failed` 状态（jobs.json 里标记），重启后**不会自动续跑**——需要手动 `/api/runs/{job_id}/cancel` 清理
- 如果跑的是 `git pull` 拿了 schema 不兼容的新版（极少，但比如 `app/models/lineage.py` 加了 required 字段），重启时 `JsonStore` 会以 lenient 模式加载，老数据照常用 —— 见 `app/models/compare.py::CompareTask.validate_inputs(strict=False)`

---

## 3. 回滚

```bash
# 1. 找到上次稳定 commit（一般是 release tag 或上次 main 合并点）
git log --oneline main -10

# 2. 切到上次稳定版本
git checkout <stable-commit-sha>

# 3. 重建容器
docker compose up -d --build

# 4. （如果数据格式不向后兼容）从备份恢复 config
tar xzf backups/dataops-<TS>.tar.gz config/
docker compose restart app

# 5. 验证 healthcheck
docker compose ps
```

**Schema 不兼容回滚检查清单**：
- 回滚跨过 Phase 9 Day 1（领域 schema 收口）的 commit 时，新版 `LineageReport` envelope 加了 `extra="allow"` 字段透传，所以即使你回滚到 Phase 8 之前，新数据也能正常加载
- 回滚跨过 Phase 6（多项目空间 / RBAC）的 commit 时，**`config/users.json` / `config/projects.json` 在老版没用**，老版会忽略它们（不会报错），但这意味着所有 endpoint 重新变成无 auth 状态。生产环境慎重

---

## 4. 灾备

**单机部署**（当前架构）：硬件挂了 / 盘坏了 → 在新机器上 clone 仓库 + 恢复备份。

```bash
# 在新机器上
git clone https://github.com/allen-answer/DataOpsStudio.git
cd DataOpsStudio
git checkout <release-tag>

# 恢复 config + secret key + logs（critical）
tar xzf <backups>/dataops-<TS>.tar.gz

# 起服务
docker compose up -d --build
```

**跨设备 dev**（多人协作）：
- 不要在 main 直接开发；走 `feat/...` 分支
- push 前必先 `git fetch origin && git pull --rebase` 防冲突（codex / 其它设备可能并行 push）
- `config/*.json` 不入库 —— 每台设备有自己一份运行时状态

**多 worker / 高可用**（未来场景）：
- 当前 `JsonStore` 基于文件锁 + mtime 缓存，单进程 OK；多 worker 启动会让 `audit.jsonl` 写并发竞争（已知风险）
- Phase 9 ADR 第 6 条规划：先把 `audit.jsonl` + `jobs.json` 切 SQLite，再考虑统一 Repository 接口
- 真要上多 worker，等 SQLite 抽象完成后再拆

---

## 5. 监控 / 告警

应用暴露三个观测面：

### 5.1 `/metrics` —— Prometheus text format

```
# 抓配置（prometheus.yml 片段）
scrape_configs:
  - job_name: dataops-studio
    metrics_path: /metrics
    scrape_interval: 15s
    static_configs:
      - targets: ['dataops-studio:8010']
```

**告警建议**（Grafana / Alertmanager）：

| 指标 | 阈值 | 含义 |
|------|------|------|
| `rate(http_requests_total{status=~"5.."}[5m]) > 0.05` | 5% 请求 5xx | 服务异常 |
| `histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m])) > 2` | P95 > 2s | 性能退化 |
| `ai_jobs_inflight > 5` | AI 队列堆积 | provider 慢 / 卡死 |
| `rate(ai_usage_calls_total{status="error"}[15m]) / rate(ai_usage_calls_total[15m]) > 0.2` | 20% AI 错误率 | provider 失效 |
| `up == 0` for 1m | 服务挂了 | 容器没跑或 healthcheck 失败 |

### 5.2 `logs/audit.jsonl` —— 审计日志

每条 mutating endpoint（`POST` / `PUT` / `DELETE`）落一行 JSON：
```json
{"ts": "...", "user_id": "...", "username": "...", "method": "POST", "path": "/api/tasks", "status": 200, "request_id": "..."}
```

admin 可通过 `GET /api/audit-logs?limit=100` 查询。**留存合规**：建议每天打包归档到 S3 或类似存储，原文件保留 90 天滚动。

### 5.3 `logs/ai_usage.jsonl` —— AI 调用记录

每条 `app/ai/usage_log.log_call(...)` 落一行：
```json
{"ts": "...", "kind": "enrichment", "provider": "openai", "model": "...", "elapsed_ms": 1234, "status": "ok", "input_tokens": 1024, "output_tokens": 256, "request_id": "..."}
```

成本核算：`jq -s 'map(select(.status == "ok")) | add | {input: .input_tokens, output: .output_tokens}' logs/ai_usage.jsonl`

### 5.4 应用日志（`logs/app.log`）

`DATAOPS_LOG_FORMAT=json` 切换结构化 JSON 日志（生产环境推荐），让 ELK / Loki 直接解析。每条日志带 `request_id` 字段（来自 Phase 9 Day 6 ContextVar），可与 `/metrics` / `audit.jsonl` 跨界 trace。

---

## 6. 常见故障排查

### Q1：`docker compose ps` 显示 `unhealthy`

```bash
# 看 healthcheck 失败原因
docker inspect --format='{{json .State.Health}}' dataops-studio | jq

# 看应用日志（最近 100 行）
docker logs --tail 100 dataops-studio

# 进容器手动 curl 看是不是路由 / Python import 出错
docker exec dataops-studio curl -v http://127.0.0.1:8010/api/drivers
```

### Q2：用户报 5xx 错误，怎么定位

1. 用户报错时记下错误卡片底部的 `request_id`（response header `X-Request-Id` / envelope `request_id` 字段）
2. 查日志：
   ```bash
   docker exec dataops-studio grep <request_id> /app/logs/app.log
   # 或 jq（json 模式）
   docker exec dataops-studio sh -c 'cat /app/logs/app.log | jq "select(.request_id == \"<id>\")"'
   ```
3. 查审计：`grep <request_id> logs/audit.jsonl`

### Q3：AI 调用超时 / 失败激增

```bash
# 看最近 50 条 AI 调用失败
tail -200 logs/ai_usage.jsonl | jq 'select(.status != "ok")' | tail -50

# 看当前队列里堆了多少 in-flight job
curl -fsS http://localhost:8010/metrics | grep ai_jobs_inflight
```

修复路径（按优先级）：
1. admin → `/admin/ai` 关闭 `enable_inference` / 切到 `mock` provider 临时降级
2. 改 `config/lineage_ai.json` 调小 `inference_max_fragments`
3. 等 AI provider 恢复

### Q4：磁盘满了（`results/` / `logs/` 撑爆）

```bash
# 看占用
du -sh config/ results/ logs/

# 清半年前的 workflow_runs（admin 应该走 /api/workflow-runs/{run_id} DELETE，
# 但批量清理直接 rm 也行 —— 删 .json 时一并 rm 整个 <run_id>/ 目录）
find results/workflow_runs/ -name "*.json" -mtime +180 -exec sh -c 'rm "$1" && rm -rf "${1%.json}"' _ {} \;

# 滚动 audit.jsonl（按月归档）
mv logs/audit.jsonl logs/audit-$(date +%Y%m).jsonl
touch logs/audit.jsonl  # 应用 append-only 自动新建写入
```

### Q5：JWT 配置 / 安全提醒

启动日志里出现：
```
DATAOPS_JWT_SECRET 未配置，使用默认 dev key —— 部署生产前请通过 env 设置
```

**生产必须设**：
```bash
# .env 或 docker-compose.yml environment:
DATAOPS_JWT_SECRET="<32+ chars random string>"
```

否则任何人能伪造 admin token 调任意 endpoint。生成方法：
```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

### Q6：CSV / Excel 上传后 `mojibake`（乱码）

- CSV：在 task 配置里设 `source_file_encoding=gbk`（默认 utf-8-sig 适配带 BOM 的 CSV）
- MySQL 容器（demo-db profile）：已强制 `utf8mb4`，初始化 SQL 走 `utf8`，应当正常。如果是用户自有 MySQL，确认 `character-set-server=utf8mb4`

---

## 7. 升级到下个 sprint 时的注意事项

CLAUDE.md "未排期"列出几个会影响生产形态的方向：

- **Repository 抽象 + SQLite**（高优先级）—— 切完之后 `audit.jsonl` / `jobs.json` 不再是 SoT，备份策略要更新成 `data/dataops.db`（迁移会有一次性脚本）
- **`/v1/` API 版本化**：迁移时 `/api/*` 仍是 v1 别名，前端切到 `/api/v1/`，旧客户端兼容
- **元数据扩展点**（custom aspect）：会扩展 `config/lineage_group_rules.yml` schema，向后兼容（旧规则文件不改也能用）
