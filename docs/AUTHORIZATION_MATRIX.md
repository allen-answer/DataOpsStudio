# 授权矩阵（Authorization Matrix）

后端所有 HTTP endpoint 的权限要求。后端 SOT —— 前端 router guard 只是 UX 优化，**不能作为安全屏障**。

实现在 `app/services/auth.py`：`get_current_user` 强制要 token，`require_role(min_role)` 校验 admin > editor > viewer 等级。每个 API 子模块用 `APIRouter(dependencies=[...])` 挂 router-级默认，单 endpoint 上 `Depends(require_role(...))` 覆盖更严格的角色要求。

`role` 三档语义：
- **viewer**：只读列表 / 详情
- **editor**：所有 viewer 权限 + 创建 / 修改 / 删除 / 执行业务对象（数据源、对比任务、作业流、上传文件、运行任务、写 aspect、写血缘 trace-compare 等）
- **admin**：所有 editor 权限 + 用户管理 / 项目管理 / 审计日志 / AI 配置 / 调度器控制 / 系统配置导出（含密码）

---

## 公开（匿名可访问 / 无 token 要求）

这些不挂任何 auth dependency。专门给登录前 UI / 健康检查 / 文档使用。

| Method | Path | 用途 |
|--------|------|------|
| GET | `/` | 重定向到 `/spa` |
| GET | `/spa` | SPA index.html（前端自己处理登录态） |
| GET | `/static/*` | SPA assets（hashed bundle，匿名可下） |
| GET | `/api/drivers` | 驱动可用性自检（部署前打 curl 用） |
| POST | `/api/auth/login` | 登录入口（没 token 怎么登） |
| GET | `/docs` | Swagger UI |
| GET | `/openapi.json` | OpenAPI 规范 |
| GET | `/metrics` | Prometheus 指标 ⚠ |

### `/metrics` 公开性说明

按当前实现 `/metrics` 走 `app/api/system.py` 路由器，**默认公开**给 Prometheus scrape job。**内网部署假设**：scrape endpoint 不暴露到公网。

如果要在公网暴露 app 又不暴露 `/metrics`，两条路：
1. 反代层 nginx / Caddy 在 `/metrics` 加 IP allow list 或 basic auth
2. 后端加 env var `DATAOPS_METRICS_AUTH=admin`，启用后 `/metrics` 走 `require_role("admin")`

本轮 P0.4 **不动**这个 — flag 给运维 / nginx 配置即可。后续看需求再决定加 env switch。

---

## viewer 及以上（要 token，role >= viewer）

只读列表 / 详情 / 下载 / bootstrap。

| Method | Path | Router 级默认 |
|--------|------|----------------|
| GET | `/api/bootstrap` | viewer |
| GET | `/api/datasources` | viewer |
| GET | `/api/tasks` | viewer |
| GET | `/api/workflows` | viewer |
| GET | `/api/workflow-templates` | viewer |
| GET | `/api/workflows/{id}/runs` | viewer |
| GET | `/api/workflow-runs` | viewer |
| GET | `/api/workflow-runs/{id}` | viewer |
| GET | `/api/workflow-runs/{id}/openlineage` | viewer |
| GET | `/api/history` | viewer |
| GET | `/results/*` | viewer ★ |
| GET | `/api/runs/{job_id}` | viewer |
| GET | `/api/scheduler/status` | viewer |
| GET | `/api/projects` | viewer（结果按 owner/member 过滤）|
| GET | `/api/assets/table/{name}` | viewer |
| GET | `/api/assets/datasources` | viewer |
| GET | `/api/assets/columns/{name}` | viewer |
| GET | `/api/assets/column-lineage/{name}` | viewer |
| GET | `/api/assets/introspect/{name}` | viewer |
| GET | `/api/assets/aspects/types` | viewer |
| GET | `/api/assets/aspects/history` | viewer |
| GET | `/api/assets/aspects/search` | viewer |
| GET | `/api/assets/aspects/index` | viewer |
| GET | `/api/search` | viewer |
| GET | `/api/lineage/ai/status` | viewer |
| GET | `/api/lineage/ai/jobs/{job_id}` | viewer |
| GET | `/api/lineage/stress-fixture` | viewer（dev fixture，无业务数据）|

★ `/results/*` 是历史结果文件下载（compare JSON / Excel / workflow run artifacts）。文件名是 `<run_id>.{json,xlsx}`，含业务数据，必须 viewer+。**当前 `system.py` 已用 path-traversal 防御，但没挂 auth**。本轮要加。

---

## editor 及以上

所有写操作 / 运行 / 配置导入 / 上传 / 业务对象 CRUD。

| Method | Path | 角色 |
|--------|------|------|
| POST | `/api/datasources` | editor |
| PUT | `/api/datasources/{id}` | editor |
| DELETE | `/api/datasources/{id}` | editor |
| POST | `/api/datasources/{id}/test` | editor |
| POST | `/api/tasks` | editor |
| PUT | `/api/tasks/{id}` | editor |
| DELETE | `/api/tasks/{id}` | editor |
| POST | `/api/tasks/{id}/copy` | editor |
| POST | `/api/tasks/{id}/run` | editor |
| POST | `/api/tasks/{id}/run-async` | editor |
| POST | `/api/tasks/{id}/preview` | editor |
| POST | `/api/workflows` | editor |
| PUT | `/api/workflows/{id}` | editor |
| DELETE | `/api/workflows/{id}` | editor |
| POST | `/api/workflows/{id}/run` | editor |
| POST | `/api/workflows/{id}/run-async` | editor |
| POST | `/api/workflows/{id}/template` | editor |
| POST | `/api/workflow-templates` | editor |
| DELETE | `/api/workflow-templates/{id}` | editor |
| POST | `/api/workflow-templates/{id}/instantiate` | editor |
| POST | `/api/workflow-runs/{id}/rerun` | editor |
| DELETE | `/api/workflow-runs/{id}` | editor |
| POST | `/api/workflow-runs/{id}/openlineage/emit` | editor |
| DELETE | `/api/history/{id}` | editor |
| POST | `/history/export` | editor（生成临时下载文件）|
| POST | `/api/runs/{id}/cancel` | editor |
| POST | `/api/preview/rows` | editor |
| POST | `/api/preview/columns` | editor |
| POST | `/api/sql/assist` | editor |
| POST | `/api/uploads/excel` | editor |
| POST | `/api/uploads/csv` | editor |
| POST | `/api/uploads/parquet` | editor |
| POST | `/api/uploads/lineage-script` | editor |
| POST | `/api/lineage/analyze` | editor |
| POST | `/api/lineage/analyze-form` | editor |
| POST | `/api/lineage/batch/analyze` | editor |
| POST | `/api/lineage/trace-compare` | editor（已挂）|
| POST | `/api/ai/translate-error` | viewer ★★ |
| POST | `/api/ai/suggest-column-mapping` | viewer ★★ |
| POST | `/api/projects` | editor（已挂）|
| PUT | `/api/assets/aspects` | editor（已挂）|
| DELETE | `/api/assets/aspects` | editor（已挂）|

★★ AI utility 端点 (`translate-error` / `suggest-column-mapping`) 当前已挂 `get_current_user` —— viewer 也能用，因为它们不改业务数据，只是 LLM 辅助查询。保持 viewer。

注：`/config/import` 已从 editor 收紧为 **admin only**，见下方 admin 表。

---

## admin 专属

用户 / 项目 / 审计 / AI 配置 / 调度器 / 系统配置导出。

| Method | Path | 备注 |
|--------|------|------|
| GET | `/api/users` | 已挂 |
| POST | `/api/users` | 已挂 |
| PUT | `/api/users/{id}` | 已挂（admin / 本人） |
| DELETE | `/api/users/{id}` | 已挂 |
| PUT | `/api/projects/{id}` | 已挂（admin / owner） |
| DELETE | `/api/projects/{id}` | 已挂（admin / owner） |
| GET | `/api/audit-logs` | 已挂 |
| GET | `/api/lineage/ai/config` | 已挂 |
| PUT | `/api/lineage/ai/config` | 已挂 |
| POST | `/api/lineage/ai/test` | 已挂 |
| POST | `/api/scheduler/start` | admin |
| POST | `/api/scheduler/stop` | admin |
| POST | `/api/scheduler/tick` | admin |
| GET | `/config/export` | admin ★ |
| POST | `/config/import` | admin ★ |

★ `/config/export` / `/config/import` 是系统级配置备份 / 恢复，两者都 **admin only**。导出即使 `include_passwords=false` 默认脱敏，仍含全量 datasources / tasks 配置；导入是批量创建 / 覆盖，且导入文件可携带任意 `project_id`（绕过单对象创建时的项目校验），风险高于普通 editor 写单个对象。详见 `docs/PROJECT_AUTHORIZATION.md` 第 4.3 节。如果业务 viewer 需要导出查看，将来加 `/api/datasources/export-viewer` 走 viewer + 强制脱敏。

---

## 实施细节

### Router-级 default

每个业务 router 顶部挂 `dependencies=[Depends(get_current_user)]` —— 默认 viewer 即可访问。这覆盖了所有 GET 列表 / 详情。

```python
router = APIRouter(dependencies=[Depends(get_current_user)])
```

### 单 endpoint 升级

mutation 端点（POST/PUT/DELETE）显式加 `Depends(require_role("editor"))`：

```python
@router.post("/api/tasks", response_model=CompareTask)
def create_task(payload: CompareTaskCreate, _: User = Depends(require_role("editor"))):
    ...
```

`require_role("editor")` 自动包含 `get_current_user` 校验 → 不会双重 401/403。

### 公开 endpoint 怎么不被 router-default 拦

`system.py` / `auth.py` 这两个 router 整体不挂 dependencies（按 endpoint 决定）。`/api/auth/login` `/api/drivers` `/spa` `/metrics` 等留在公开 router 里。

---

## 测试矩阵契约

`tests/test_api_auth_matrix.py` 必须覆盖：

| # | 场景 | 预期 |
|---|------|------|
| 1 | 未登录 GET `/api/datasources` | 401 |
| 2 | viewer GET `/api/datasources` | 200 |
| 3 | viewer POST `/api/datasources` | 403 |
| 4 | editor POST `/api/datasources` | 200 / 422（具体看 body）|
| 5 | 未登录 POST `/api/tasks/{id}/run` | 401 |
| 6 | viewer POST `/api/tasks/{id}/run` | 403 |
| 7 | editor POST `/api/tasks/{id}/run` | 200 / 404（任务不存在）|
| 8 | 未登录 GET `/config/export` | 401 |
| 9 | viewer GET `/config/export` | 403 |
| 10 | editor GET `/config/export` | 403（admin only）|
| 11 | admin GET `/config/export` | 200 |
| 12 | 未登录 POST `/api/scheduler/start` | 401 |
| 13 | editor POST `/api/scheduler/start` | 403 |
| 14 | admin POST `/api/scheduler/start` | 200 |
| 15 | 未登录 GET `/api/drivers` | 200（公开）|
| 16 | 未登录 GET `/metrics` | 200（公开 ⚠ 见上）|

---

## 未覆盖 / 后续

- `/metrics` 是否需要默认非公开 → 看运维需求
- `/results/*` 当前不区分文件归属，将来按 project_id / owner 过滤需要 service 层支持
- WebSocket / SSE 路径（如果之后加）需要单独设计 token 传递
- API key（非 JWT）模式（如果对接 CI / 第三方系统需要）—— 当前不做
