# resource_guard —— 对比作业准入控制

安全加固方案 P0 #1。把「重查询 + 重写盘」变成有硬上限的系统能力：在任务进入
runner 之前评估 allow / queue / deny，避免 OOM、磁盘打满、队列雪崩。

实现：`app/services/resource_guard.py`。接入点：`app/api/tasks.py` 的
`/api/tasks/{id}/run`（同步）和 `/run-async`（异步）。

## 分层决策

`evaluate()` 是纯函数，按「先硬拒绝、再排队」合并三类规则：有 deny 类原因 →
`deny`；否则有 queue 类原因 → `queue`；都没有 → `allow`。

### 规则表

| 规则代码 | 触发条件 | 决策 |
|---|---|---|
| `json_large_result` | `result_format=json` 且 `max_rows > 30 万` | deny |
| `huge_requires_parquet` | `max_rows > 100 万` 且 `result_format != parquet` | deny |
| `huge_requires_stream` | `max_rows > 500 万` 且 `stream_compare=false` | deny |
| `same_persist_large` | `persist_same_bucket=true` 且 `max_rows > 100 万` | queue |
| `disk_low_watermark` | 结果盘剩余 `< 5GB` 或 使用率 `> 85%` | deny |
| `mem_pressure` | 可用内存 `< 15%`（仅 Linux，读 `/proc/meminfo`） | deny |
| `queue_full` | 活跃作业数 `>= 50` | deny |
| `compare_cap` | 运行中对比作业 `>= 2` | queue |
| `export_cap` | 运行中导出作业 `>= 1` | queue |
| `project_cap` | 同项目运行中对比作业 `>= 2` | queue |
| `datasource_cap` | 同数据源运行中对比查询 `>= 2` | queue |

阈值全部可经环境变量覆盖（见下表）。`mem` 规则在拿不到 `/proc/meminfo` 时
（非 Linux dev 机）自动跳过，绝不因测量失败误拦。

### 端点行为

- **同步 `/run`**：`deny` → 429；`queue` → 429（同步执行不排队，提示改后台执行）。
- **异步 `/run-async`**：`deny` → 429；`queue` → 放行（自然进 ThreadPoolExecutor 队列）。
- 同步 run 不进 `jobs` 表，故 `compare_cap` 只统计异步作业 —— 这是 MVP 已知限制，
  静态规则与磁盘/内存规则对同步 run 完全生效。

## dry-run → enforce 推进

`DATAOPS_GUARD_ENFORCE` 默认 `false`（dry-run）：决策照算、照打指标
（`dataops_guard_decisions_total`）、照记 warning 日志，但端点**不拦**。

上线步骤：

1. 部署后保持 `DATAOPS_GUARD_ENFORCE=false`，观测 3~7 天
   `dataops_guard_decisions_total{decision="deny"}` 是否误伤正常任务。
2. 确认无误伤后设 `DATAOPS_GUARD_ENFORCE=true` 强制。
3. 回滚只需切回 `false`。

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DATAOPS_GUARD_ENFORCE` | `false` | dry-run / 强制 |
| `DATAOPS_MAX_COMPARE_JOBS` | `2` | 运行中对比作业上限（超出排队） |
| `DATAOPS_MAX_EXPORT_JOBS` | `1` | 运行中导出作业上限（超出排队） |
| `DATAOPS_MAX_JOBS_PER_PROJECT` | `2` | 单项目运行中对比作业上限（超出排队） |
| `DATAOPS_MAX_QUERIES_PER_DATASOURCE` | `2` | 单数据源运行中对比查询上限（超出排队） |
| `DATAOPS_GUARD_QUEUE_MAX` | `50` | 活跃作业总数上限（超出拒绝） |
| `DATAOPS_RESULTS_MIN_FREE_GB` | `5` | 结果盘最低剩余 GB |
| `DATAOPS_RESULTS_MAX_DISK_USAGE_PERCENT` | `85` | 结果盘最高使用率% |
| `DATAOPS_GUARD_JSON_MAX_ROWS` | `300000` | json 格式 max_rows 上限 |
| `DATAOPS_GUARD_HUGE_ROWS` | `1000000` | 「大任务」阈值（须 parquet） |
| `DATAOPS_GUARD_STREAM_ROWS` | `5000000` | 「超大任务」阈值（须 stream_compare） |
| `DATAOPS_GUARD_MEM_MIN_PERCENT` | `15` | 最低可用内存% |

## 观测

`/metrics` 暴露 `dataops_guard_decisions_total{decision,reason}` —— 按
`decision`（allow/queue/deny）和首要原因码计数。dry-run 阶段据此判断阈值是否
合理。

## ✅ Phase 13 + v0.2.0 落地

- **JobInfo 三字段补全**(Phase 13 #2):`owner_user_id` / `project_id` /
  `target_run_id` 字段就位,scheduler 触发用 `owner_user_id="system"`
- **mid-run 磁盘水位**(Phase 13 #4):`check_disk_critical` + 5000 行间隔检
  + `_cleanup_partial_parquet`,临时 run 目录不残留半成品
- **per-run 磁盘配额**(Phase 13 #5):`RunLimits.run_disk_quota_mb` +
  `check_run_quota` + `RunQuotaExceeded(DiskWatermarkExceeded)` 子类共享 cleanup
- **per-project 跨 run 配额**(Phase 13 #11):`check_project_quota` 扫
  `results/` 折成 per-project 累积 MB,超 `DATAOPS_PROJECT_DISK_QUOTA_MB` 拦
- **DB 语句超时**(Phase 13 #1):MySQL `SET SESSION MAX_EXECUTION_TIME` /
  Oracle/DM `conn.callTimeout` / DB2 `ibm_db.set_option`,详见 [DB_STATEMENT_TIMEOUT.md](./DB_STATEMENT_TIMEOUT.md)
- **API 限流**(v0.2.0):`services/rate_limit.py` 滑窗 + per-IP 10/min +
  per-username 5/min + 429 Retry-After
- **审计事件管道**(v0.2.0):`record_auth_event` 写 SQLite + jsonl,
  `resource_type=auth_event` 覆盖 login / refresh / mfa / step_up / rate_limit
- **`/api/runs/{id}/export-excel` 异步**(Phase 10 完成):`excel_export.submit_excel_export`
  ThreadPoolExecutor + JobInfo `kind=excel_export`
- **user_cap per-user 配额**(Phase 14):`DATAOPS_MAX_JOBS_PER_USER=1` 默认,
  靠 JobInfo.owner_user_id 直接 cap 不再依赖 task lookup

## 后续

- per-project user-cap 二维交叉(同项目下每用户配额)—— 真出现误用再加
- DB2 / 其它 RDBMS 的 query timeout 走驱动属性(目前 ibm_db.set_option 已接)
