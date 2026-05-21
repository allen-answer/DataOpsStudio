# 项目级授权（Project Authorization）

## 模型回顾

- `User.role`：`admin / editor / viewer`
- `Project.owner_id` + `Project.members` 决定项目成员关系
- 资源（`DataSource / CompareTask / Workflow / History`）通过 `project_id` 关联到一个项目；
  `project_id == ""` 视为**全局**资源，所有登录用户可见
- `admin` 全权；`editor / viewer` 仅能访问 owned 或 member 项目的资源（+ 全局）

## 直接 `datasource_id` 接口授权（P0 必读）

**凡是直接接收 `datasource_id` 并执行 SQL / EXPLAIN / 预览 / introspect 的
endpoint，都必须调用 `require_datasource_access`**，否则任意持有该 datasource
id 的登录用户都能跨项目读取数据。

`/api/datasources` 列表已经按 `?project_id=` 过滤，但接受 `datasource_id`
作为入参的接口（preview / introspect / override SQL 等）以前没做项目校验
—— 直接拿 id 就能用。该补丁补齐这条缝。

### Helper API

`app/api/_authz.py`：

```python
from app.api._authz import require_datasource_access

datasource = require_datasource_access(current_user, datasource_id)
```

行为：

1. `datasource_id` 不存在 → 404（`detail` 文案可自定义）
2. 当前用户对 `datasource.project_id` 没访问权 → 403
3. 通过则返回 `DataSource`，调用方复用，避免再调一次 `datasource_store.get`

访问规则在同模块的 `can_access_project(current, project_id)` 里：

- `admin` → True
- 全局资源（`project_id == ""`）→ True
- 否则用户必须是该 project 的 owner 或 member

### 当前已接入的接口

| 模块 | 端点 | 说明 |
| --- | --- | --- |
| `tasks.py` | `POST /api/tasks/{id}/preview` | 包含 `override_datasource_id` 路径 |
| `uploads.py` | `POST /api/preview/rows` (`kind=sql`) | 草稿态行预览 |
| `uploads.py` | `POST /api/preview/columns` (`kind=sql`) | 草稿态列名预览 |
| `assets.py` | `GET /api/assets/introspect/{name}` | 拉真实表结构 |

### 新增直接接收 `datasource_id` 的端点

必须按上述模板加一行 `require_datasource_access`。同时记得：

1. 路由函数加 `current: User = Depends(get_current_user)`（或更高权角色依赖）
2. 用返回的 `datasource` 对象替代原本的 `datasource_store.get(...)` 调用
3. 在 `tests/test_project_authorization.py` 加一组用例：editor 跨项目 → 403；
   owned 项目 → 200；admin → 全权

### 不在范围内的接口

下列接口**不接收 `datasource_id` 做入参**，所以走列表过滤（`?project_id=`）
已经够 —— 不要重复加：

- `/api/datasources` 列表 / 单条 GET（已经只暴露给登录用户）
- `/api/tasks` / `/api/workflows` 等列表 endpoint
- `/api/assets/table/{name}` / `/api/assets/columns/{name}` / 等血缘聚合查询
  （不接 `datasource_id`，按表名反查 lineage / store 数据）

## 测试

回归用例在 `tests/test_project_authorization.py`，覆盖：

- editor 跨项目 datasource 走 preview / introspect → 403
- editor 自有项目 / 全局 datasource → 200
- editor 不能通过 `/api/tasks/{id}/preview` 的 `override_datasource_id`
  把权限"借"出去
- admin 可访问任意 datasource

新增端点务必扩展该文件，让回归覆盖每条新加 `require_datasource_access`
的入口。
