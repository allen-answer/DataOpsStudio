# DataOpsStudio 配置参考

按部署场景查表配 `config/config.yml`。配置优先级:**环境变量 > config.yml > 代码默认值**。

复制模板:`cp config/config.yml.example config/config.yml`,然后按下面三档选一档套用。

---

## 一、三档场景模板

### 档位 1:开发机 / 本地试用

数据量 < 100 万行,localhost-only,目的是上手体验。**不切 prod 模式**,Guard 开 warn,出问题看日志就好。

```yaml
env: dev
auth:
  jwt_secret: ""        # 不用配,dev key 够用
  admin_password: "admin"
guard:
  results_min_free_gb: 2
  results_max_disk_usage_percent: 90
  compare_auto_stream_bytes: 536870912        # 512MB 切流式
  compare_deny_bytes: 2147483648              # 2GB 拒跑
  db_statement_timeout_seconds: 300           # 5 分钟
memory:
  guard_mode: warn
  soft_limit_mb: 1024
  hard_limit_mb: 2048
rate_limit:
  enforce: false        # 本地调试不限流
```

### 档位 2:中型生产(单机 16C / 32G / 500GB SSD)

日均对比量 100 万 ~ 1 亿行,部门级使用,需要稳定性保障。**切 prod 模式 + enforce**,JWT secret 必配。

```yaml
env: prod
auth:
  jwt_secret: "<64-byte token,见下方生成命令>"
  jwt_ttl_seconds: 28800
  refresh_ttl_seconds: 604800
  admin_password: "<改成强密码>"
guard:
  results_min_free_gb: 20
  results_max_disk_usage_percent: 85
  compare_auto_stream_bytes: 1073741824       # 1GB 切流式
  compare_deny_bytes: 10737418240             # 10GB 拒跑
  db_statement_timeout_seconds: 1800          # 30 分钟,慢 ETL 够
  sql_preflight_enforce: true
memory:
  guard_mode: enforce
  soft_limit_mb: 4096
  hard_limit_mb: 8192
rate_limit:
  enforce: true
  login_per_min: 10
  user_per_min: 10
scheduler:
  enabled: true
  interval_seconds: 60
```

### 档位 3:大型生产(多核 / 大内存 / 阵列盘)

日均对比量 1 亿 ~ 10 亿+ 行,跨部门共享,SLA 要求严。Guard 上限拉高但**不要关掉** —— 边界明确是核心价值。

```yaml
env: prod
auth:
  jwt_secret: "<64-byte token>"
  jwt_ttl_seconds: 14400        # 4 小时,短 TTL 更安全
  refresh_ttl_seconds: 604800
guard:
  results_min_free_gb: 100
  results_max_disk_usage_percent: 80          # 高负载下留更多 buffer
  compare_auto_stream_bytes: 2147483648       # 2GB 切流式
  compare_deny_bytes: 53687091200             # 50GB 拒跑
  db_statement_timeout_seconds: 3600          # 1 小时
  sql_preflight_enforce: true
memory:
  guard_mode: enforce
  soft_limit_mb: 16384
  hard_limit_mb: 32768
rate_limit:
  enforce: true
  login_per_min: 5              # 严控登录爆破
  user_per_min: 30
scheduler:
  enabled: true
  interval_seconds: 30
  sensor_cooldown_seconds: 60   # 高频任务用,冷却时间短
jobs:
  ttl_seconds: 259200           # 3 天,长任务历史保留更久
```

---

## 二、各项详细说明

### env(运行模式)

| 值 | 含义 |
|---|---|
| `dev`(默认) | Guard 跑 dry-run(超阈值只 log warning,任务继续),允许 dev JWT key |
| `prod` | Guard enforce(超阈值直接 abort + 错误返前端),JWT secret 必须显式配置否则启动 RuntimeError |

切 prod 后:**dev 默认 JWT key 失效,所有用户需要重新登录**。

### auth.jwt_secret

生成命令:

```bash
# 容器内:
docker exec dataops-studio python3 -c "import secrets; print(secrets.token_urlsafe(64))"

# Windows 离线包内:
.\python\python.exe -c "import secrets; print(secrets.token_urlsafe(64))"
```

把输出贴进 `auth.jwt_secret`。**改 secret 后所有现有 JWT 失效**,挑无人用的时间切换。

### guard.compare_auto_stream_bytes / compare_deny_bytes

|  阈值 | 行为 |
|---|---|
| 结果集预估字节 < `auto_stream_bytes` | 内存路径(快,但占内存) |
| `auto_stream_bytes` ≤ 字节 < `deny_bytes` | 强制走流式 parquet 写盘 |
| 字节 ≥ `deny_bytes` | 直接拒绝,返 4xx 给前端 |

**估算方法**:行数 × 列数 × 平均字段长度。1 亿行 × 20 列 × 平均 30 字节 ≈ 60GB → 把 `deny_bytes` 设到 80GB(80 * 1073741824 = `85899345920`)。

宁可设大一点拒跑值,然后看实际 `/metrics` 的 compare result_bytes 分布再缩。设太严的代价是合法大任务被拒,设太松的代价是 OOM。

### guard.db_statement_timeout_seconds

应用到所有数据库 cursor。**生产建议至少 1800(30 分钟)**,因为 ETL 慢 SQL 是常态;太短会让合法 ETL 任务超时失败,频繁告警麻木运维。

单任务可通过 `RunLimits.query_timeout_seconds` 覆盖(在前端建任务时配),允许某些已知慢任务用 7200,日常 preview 用 60。

### memory.guard_mode

| 模式 | 行为 |
|---|---|
| `off` | 完全不检查 |
| `warn`(默认 dev) | RSS 超 soft_limit 时 log + 拒接新任务,已跑任务不动 |
| `enforce`(默认 prod) | RSS 超 hard_limit 时强制中止单任务 + raise 错误,**保护整个进程不挂** |

**hard_limit 必须 > soft_limit**,推荐 hard ≈ 2 × soft。设太低会让正常大查询被误杀,设太高就失去保护意义。

### memory.soft/hard_limit_mb

参考表(按容器内存):

| 容器内存 | soft | hard |
|---|---|---|
| 2GB | 1024 | 1536 |
| 4GB | 2048 | 3072 |
| 8GB | 4096 | 6144 |
| 16GB | 8192 | 12288 |
| 32GB | 16384 | 24576 |

**不要把 hard 设到容器内存 100%** —— 留 20% 给 OS / Python runtime / 其他线程开销。

### rate_limit

| 参数 | 默认 | 建议 |
|---|---|---|
| `login_per_min` | 10 | 公网部署降到 5;内网可放到 20 |
| `user_per_min` | 5 | 偏紧;频繁创建任务的用户调高到 10-30 |

`enforce: false` 可以临时关掉(故障排查或压测),但**生产长期不建议关**。

### scheduler.sensor_cooldown_seconds

文件 sensor / workflow_success sensor 触发后的冷却窗口。**不要设 < 60**,否则一个文件 watch 触发后能在 1 分钟内自我触发几十次造成任务雪崩。

### jobs.ttl_seconds

JobInfo 在内存里保留多久。**长任务多的环境调长**(7 天 = 604800),便于回看失败原因;短促环境保持默认 24 小时即可避免内存涨。

---

## 三、常见踩坑

### 1. JWT secret 没配就切 prod

启动直接 RuntimeError:`DATAOPS_JWT_SECRET must be set when DATAOPS_ENV=prod`。**先配 secret 再切 prod**。

### 2. enforce 后大任务被拒跑

`compare_deny_bytes` 默认 5GB 对生产数据量小,**先调大再开 enforce**,顺序很重要。看 `/metrics` 的 `compare_run_total{outcome="rejected"}` 计数,有数字说明 deny_bytes 设太严。

### 3. 改了 yml 没生效

config.yml 在**启动时**读一次,改完要重启进程(docker restart / 关 .bat 重开)。env var 优先级最高,如果发现 yml 改了没用,先检查 docker-compose.yml / start.bat 里是否也 set 了同名 env(env 会盖掉 yml)。

### 4. dev 模式 OOM 还是会挂吗?

会。dev 默认 `guard_mode=warn`,只警告不中止。如果是公司测试机不想动 prod 模式,**单独把 `memory.guard_mode` 设成 enforce** —— 这样保护进程不挂,其他 guard 仍 warn。

### 5. 多个环境(dev / staging / prod)怎么管

用 env 文件分环境,**别**用 yml 切换:

```bash
# docker-compose.dev.yml
environment:
  DATAOPS_ENV: dev

# docker-compose.prod.yml
environment:
  DATAOPS_ENV: prod
  DATAOPS_JWT_SECRET: ${PROD_JWT_SECRET}
```

config.yml 只放跨环境共享的默认值,env-specific 用 docker compose env 覆盖。

---

## 四、验证配置生效

启动后看 log:

```
INFO app.config_loader: config.yml applied 12 env var(s): {'DATAOPS_ENV': 'prod', 'DATAOPS_JWT_SECRET': '***', ...}
```

`***` 是脱敏标记,jwt_secret / api_key / password 类不会打到日志。

线上验证:`GET /healthz` 返回 200 + `{"status": "ok"}` 即配置加载没崩。

更细的:`GET /metrics` 看 `compare_run_total` / `http_requests_total` 这些 counter,正常增长说明服务正常运转。
