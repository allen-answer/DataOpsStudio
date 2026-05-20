# 项目级资源授权（Project-Level Resource Authorization）

本文件定义**资源可见性 / 操作权限如何按项目（Project）隔离**。它是
[`AUTHORIZATION_MATRIX.md`](AUTHORIZATION_MATRIX.md) 的补充：授权矩阵管的是
「哪个 role 能调哪个 endpoint」，本文件管的是「同一个 role 的两个用户，能不能
看见 / 改动**对方项目**的资源」。

后端是 SOT。前端的项目切换 dropdown 只是 UX，**不能作为安全屏障**。

实现在 `app/api/_authz.py`，被 `datasources / tasks / workflows / history / system`
这些子模块复用。

---

## 1. 数据模型

- `User`：`id` + `role`（`admin` / `editor` / `viewer`）。
- `Project`：`id` + `owner_id` + `members`（`User.id` 列表，**含 owner**）。
- 业务资源 `DataSource` / `CompareTask` / `Workflow` 各带一个 `project_id` 字段
  （`str`，空串 = 不归属任何项目）。
- 历史结果（`results/<run_id>.json`）本身不带 `project_id`，通过 `task_id`
  反查它所属 task 的 `project_id`。
- 作业流运行产物（`results/workflow_runs/<run_id>/...`）通过 run 的
  `workflow_id` 反查 workflow 的 `project_id`。

「用户能访问的项目集合」= 该用户作为 `owner_id` **或** 出现在 `members` 里的
所有 project。下文记为 `accessible_project_ids(user)`。

---

## 2. 核心规则

### 2.1 admin

admin 可访问、修改、运行、下载**全部资源**，不受 `project_id` 限制。
`accessible_project_ids(admin)` 在实现里返回 `None`，语义是「无限制」，
调用方据此跳过过滤。

### 2.2 editor / viewer

editor / viewer 只能访问 `project_id` 落在 `accessible_project_ids(user)`
里的资源，**外加**全局资源（见 2.3）。

- `viewer`：只读自己项目（+ 全局）的资源。
- `editor`：在自己项目（+ 全局）里 CRUD / 运行；**不能**碰别人项目的资源。

editor / viewer 看不到、改不动、跑不了、下载不了不属于自己项目的资源 ——
即使他们知道 `id`，后端也会拒。

### 2.3 `project_id` 为空 = 全局资源（global resource）

`project_id == ""` 的资源定义为**全局资源**。来源有二：
- 项目空间（D-MVP）之前创建的历史遗留资源。
- 有意不挂到任何项目下的共享资源。

**可见性**：全局资源对**所有已登录用户可见**（任何 role 都能在列表里看到、
能读详情、能下载其结果）。

**可写性**：全局资源不属于任何项目，因此对项目归属的写校验「通过」——
任何 `editor` 及以上都能修改 / 删除 / 运行全局资源（仍受
[`AUTHORIZATION_MATRIX.md`](AUTHORIZATION_MATRIX.md) 的 role 门槛约束）。
`viewer` 永远不能写（被 `require_role("editor")` 拦在前面）。

> 取舍：把全局资源设成「人人可见可写」而不是「admin only」，是为了不破坏
> 项目空间引入之前就存在的资源的可用性。需要收紧时，正确做法是给这些资源
> 补一个真实的 `project_id`，而不是改全局语义。

---

## 3. 各资源的项目作用域

### 3.1 列表读取（GET 列表类）

`GET /api/datasources`、`/api/tasks`、`/api/workflows`、`/api/history`、
`/api/bootstrap` 一律按当前用户的可访问项目过滤：

- admin：返回全部。
- editor / viewer：返回 `project_id ∈ accessible_project_ids(user)`
  **或** `project_id == ""`（全局）的资源。

显式带 `?project_id=` query 时，在上述用户作用域之上**再**收窄到该项目
（外加全局资源）。若用户对该 `project_id` 无权，结果自然为空集 —— 不另抛
403，避免前端项目切换器频繁报错。

### 3.2 历史结果列表 `GET /api/history`

历史 run 通过 `task_id → task.project_id` 归属项目。

- admin：返回全部 run，**含**孤儿 run（task 已删、无法归属）。
- editor / viewer：只返回归属于可访问项目（或全局 task）的 run。
  **孤儿 run（`task_id` 已不存在 / run 无 `task_id`）对非 admin 不可见** ——
  因为无法核实归属，保守地隐藏，避免泄漏已删任务的结果。

### 3.3 首屏 `GET /api/bootstrap`

bootstrap 一次性返回 datasources / tasks / workflows / history。每一类都
按 3.1 / 3.2 的规则按当前用户过滤。datasources 的 `password` 始终脱敏。

### 3.4 结果文件下载 `GET /results/{path}`

**不能只校验登录态。** 下载前先把文件解析回它所属的项目，再校验权限：

| 文件形态 | 归属解析路径 |
|----------|--------------|
| `results/<run_id>.json` / `.xlsx`（对比 / 血缘结果） | 读 JSON 里的 `task_id` → `task_store.get` → `task.project_id` |
| `results/workflow_runs/<run_id>/...`（作业流产物） | `get_workflow_run(run_id)` → `workflow_id` → `workflow.project_id` |
| 其它（上传文件 / 临时导出 / 无法解析） | 不做项目归属，回落到「仅登录态」 |

判定：
- 能解析出归属项目，且用户对该项目无权 → **403**。
- 能解析出归属，且项目为全局（`project_id == ""`）→ 放行（任何登录用户）。
- 无法解析归属（孤儿 / 上传文件 / 未知文件）→ 回落到仅校验登录态放行。

> 已知局限：「无法解析归属的文件回落到仅登录态」是为了不破坏上传文件下载等
> 既有功能。完整归属（每个 result 文件都带 owner / project）属后续工作。

### 3.5 写 / 运行操作（POST / PUT / DELETE / run）

`create` / `update` / `delete` / `run` / `copy` / `preview` / `test` 等操作，
在 role 门槛（`require_role`）之上**再**校验用户对资源所在项目的权限：

- **create**：校验 `payload.project_id` —— 用户必须能访问目标项目（或目标
  为全局）。不能把资源建到别人的项目里。
- **update**：先校验用户能访问**资源现有的** `project_id`；若 payload 改了
  `project_id`，目标项目也要有权（不能把资源「搬」进别人的项目）。
- **delete / run / run-async / copy / preview / test**：校验用户能访问资源
  现有的 `project_id`。
- 资源不存在 → 404；存在但无项目权限 → 403。

`/history/export`（多选导出）对每个选中的 `run_id` 解析归属项目，只要有一个
是用户无权访问的 → 403。

---

## 4. 引用资源授权（防止间接越权）

外壳资源（task / workflow）的项目权限校验过了，**不代表它引用的内部资源
也安全**。典型越权链：

> editor A 对 ProjectA 有写权 → 创建一个 ProjectA 的 task，但把 `source_id`
> 指向 ProjectB 的 datasource → A 之后 run 这个 task，就间接查了 ProjectB
> 的库。外壳校验放行了，内部引用没校验 —— 这就是间接越权。

所以「内部引用资源」必须独立校验。实现在 `app/api/_shared.py` 三个函数。

### 4.1 task 引用的 datasource

`create_task` / `update_task` 走 `ensure_datasources_for_kind_authorized(payload, current_user)`：

1. `source_kind=SQL` 时，`source_id` 指向的 datasource 必须存在（否则 400）。
2. `target_kind=SQL` 时，`target_id` 指向的 datasource 必须存在（否则 400）。
3. 当前用户必须能访问该 datasource 的 `project_id`，否则 **403**。
4. task 的 `project_id` 非空时，datasource 的 `project_id` 必须**与 task 相同**，
   或 datasource 是**全局资源**（`project_id=""`）；否则 **403**。
5. 规则 4 是结构一致性约束，对**所有角色（含 admin）生效** —— 否则 admin
   建的「ProjectA task → ProjectB datasource」会成为 ProjectA 成员越权的跳板。
   规则 3 的访问校验则按用户判定（admin 自然通过）。

### 4.2 workflow compare 节点引用的 task

`create_workflow` / `update_workflow` / 模板实例化 / 存模板走
`ensure_workflow_node_targets_authorized(payload, current_user)`（结构校验 +
引用授权）；`run` / `run-async` 走轻量的
`authorize_workflow_compare_tasks(workflow, current_user)`（只做引用授权，
不重复结构校验，也不因 task 已被删而 400）：

1. compare 节点的 `config.task_id` 必须存在（结构校验阶段，否则 400）。
2. 当前用户必须能访问该 task 的 `project_id`，否则 **403**。
3. workflow 的 `project_id` 非空时，compare task 必须**与 workflow 同项目**
   或是**全局 task**；否则 **403**。
4. run 前再校验一遍 —— 防止 workflow 创建后 task 被移到别的项目、或历史
   遗留的跨项目引用被间接执行。

### 4.3 config 导入 / 导出收紧为 admin only

`/config/import` 是批量创建 / 覆盖 datasources + tasks，导入文件里可携带
**任意 `project_id`**，绕过单对象创建时的项目校验。因此导入 / 导出都 admin only：

| Method | Path | 角色 |
|--------|------|------|
| GET | `/config/export` | **admin only**（含全量配置，可选含明文密码） |
| POST | `/config/import` | **admin only**（批量写入 + 可携带任意 project_id，风险高于 editor 写单对象） |

---

## 5. 状态码约定

| 情况 | 状态码 |
|------|--------|
| 未登录 | 401 |
| 已登录但 role 不够（viewer 想写） | 403 |
| 已登录、role 够，但对资源所在项目无权 | 403 |
| 已登录、role 够，但引用了无权 / 跨项目的 datasource / task | 403 |
| 资源不存在 | 404 |
| 列表读取中无权的资源 | 静默过滤掉（不报错） |

---

## 6. 覆盖范围

**第一轮（外壳资源项目隔离）**：

- `app/api/datasources.py` —— list 过滤 + create/update/delete/test 校验
- `app/api/tasks.py` —— list 过滤 + create/update/delete/copy/run/run-async/preview 校验
- `app/api/workflows.py` —— list 过滤 + create/update/delete/run/run-async/runs/template 校验 + template 实例化目标项目校验
- `app/api/history.py` —— list 按用户作用域过滤 + delete/export 校验
- `app/api/system.py` —— `/api/bootstrap` 按用户过滤 + `/results/*` 按归属项目校验
- `app/services/history.py` —— `list_result_history` 新增 `allowed_project_ids` 参数

**第二轮（引用资源授权，本文件第 4 节）**：

- `app/api/_shared.py` —— `ensure_datasources_for_kind_authorized` /
  `authorize_workflow_compare_tasks` / `ensure_workflow_node_targets_authorized`
- `app/api/tasks.py` —— create/update 用 datasource 授权校验
- `app/api/workflows.py` —— create/update/instantiate/save-template/run 用 task 授权校验
- `app/api/config_io.py` —— `/config/import` 收紧为 admin only

共享 helper：`app/api/_authz.py`。

**不在范围**：workflow templates 库本身（视为跨项目共享库，仅在实例化 /
存模板时校验引用 task）、`/api/runs/{job_id}` 异步 job 状态查询、
assets / search / lineage 索引类 endpoint 的项目过滤 —— 留后续迭代。

---

## 7. 测试契约

`tests/test_project_authorization.py` 覆盖：

| # | 场景 | 预期 |
|---|------|------|
| 1 | viewer A 列 datasource 看不到 viewer B 项目的 datasource | A 的列表不含 B 的 ds |
| 2 | viewer A 列 task 看不到 viewer B 项目的 task | A 的列表不含 B 的 task |
| 3 | editor A 运行 B 项目的 task | 403 |
| 4 | admin 列 datasource / task | 看到全部 |
| 5 | bootstrap 按当前用户过滤 | A 只见自己项目 + 全局 |
| 6 | viewer A 下载 B 项目 run 的 `/results/<run_id>.json` | 403 |
| 7 | 全局资源（`project_id=""`）对所有登录用户可见 | A、B 都能看到 |
| 8 | editor A 创建 ProjectA task 引用 ProjectB datasource | 403 |
| 9 | editor A 更新 ProjectA task 改成引用 ProjectB datasource | 403 |
| 10 | editor A 创建 ProjectA task 引用 ProjectA datasource | 200 |
| 11 | editor A 创建 ProjectA task 引用全局 datasource | 200 |
| 12 | editor A 创建 ProjectA workflow 引用 ProjectB task | 403 |
| 13 | editor A 创建 ProjectA workflow 引用 ProjectA task | 200 |
| 14 | editor A 创建 ProjectA workflow 引用全局 task | 200 |
| 15 | viewer / editor `POST /config/import` | 403；admin 放行 |
