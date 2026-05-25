# Schema 导入(/scenario-lab/import) — Phase 14 #3 Round 3

> **信息架构更新**:Schema 导入不再作为一级菜单。它现在是 **场景测试沙盒
> 的子流程**:
>
> - 路由:`/scenario-lab/import`(SchemaImportView 不变,只换路径)
> - 入口:`/scenario-lab` 顶部按钮「从 datasource 导入 schema」
> - 旧 `/schema-import` 仍 redirect 到 `/scenario-lab/import`(老书签兼容)
> - 侧边栏:**已删除** Schema 导入一级菜单项
>
> 前端 store 是 `stores/schemaImport.ts`(facade,引用 sandbox.ts backing state)。
> SchemaImportView + ImportDialog 通过此 store 访问,不再直接 import sandbox.ts。
> 页面顶部含面包屑「场景测试沙盒 / 从 datasource 导入 schema」+ 返回链接。

## 范围

从 datasource 读 `information_schema` / `all_tab_columns` / `SYSCAT.COLUMNS`,
反向生成 scenario yml(给 [/scenario-lab](SCENARIO_LAB.md) 用)。

- 表元数据反查 — 纯只读
- yml 文本预览
- 保存到 `config/scenarios/<scenario_id>.yml`(可选)

## 风控

| 操作 | 控制 flag | unknown | sandbox | staging | prod |
|---|---|---|---|---|---|
| Preview (`save=false`) | `allow_schema_import` | ✗ | ✓ | flag | flag |
| Save (`save=true`)     | `allow_schema_save` | ✗ | flag | ✗ 红线 | ✗ 红线 |

**Save 红线**:仅 sandbox + allow_schema_save=True 允许。生产 ds 上不允许
把 schema 落到 yml(防 admin 误把生产 schema 当 sandbox fixture 模板,继而又
materialize 灌假数据)。前端在 prod ds 时 Save 按钮 disabled + tooltip 解释。

后端 `app/api/scenarios.py::import_from_datasource_api` 强制:

```python
ds = require_datasource_access(current, payload.datasource_id, ...)
assert_operation_allowed(current, ds, Operation.SCHEMA_IMPORT_PREVIEW, context=...)
if payload.save:
    assert_operation_allowed(current, ds, Operation.SCHEMA_IMPORT_SAVE, context=...)
```

## API

```http
POST /api/scenarios/import-from-datasource
Body:
  datasource_id: str
  table_names: list[str]      # 至少 1 张
  scenario_id: str            # ^[A-Za-z0-9_\-]+$
  scenario_name: str = ""
  default_rows: int = 1000    # 每表默认行数
  save: bool = False          # True 时写入 config/scenarios/<id>.yml
Response:
  scenario_id, yml_text, saved_path, tables_imported, rows_per_table
```

详细决策矩阵见 [DATASOURCE_ENVIRONMENT_POLICY.md](DATASOURCE_ENVIRONMENT_POLICY.md)。

## 审计

`schema_import.preview.allowed` / `.denied` 和 `schema_import.save.allowed`
/ `.denied` 事件,带 scenario_id + table_count + datasource_id + environment。
admin 可在审计页追溯。
