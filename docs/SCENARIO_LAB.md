# 场景测试沙盒(/scenario-lab) — Phase 14 #3 Round 3

> **信息架构更新**:scenario-lab 现在含 1 个子流程 `/scenario-lab/import`(原
> /schema-import,现归为子流程)。一级菜单只有 SQL 诊断 / 场景测试沙盒 两个。
>
> 旧 /sql-optimize 已废弃(redirect → /sql-diagnosis)。所有 scenario yml
> 模板 / materialize / record / verify 操作走 /scenario-lab 入口。
> 前端 store 是 `stores/scenarioLab.ts`(facade,引用 sandbox.ts backing state)。
>
> 顶部入口按钮:
> - 「从 datasource 导入 schema」 → `/scenario-lab/import`
> - 「刷新模板列表」 → 重新拉 yml 列表

## 范围

scenario yml 模板驱动的测试沙盒 — fixture 基建:

- yml 模板列表 + 详情(表 / 偏差 / 工作负载)
- 选 datasource(**仅 sandbox 可写入**)+ datasource picker + 风险面板
- materialize:DDL + INSERT 造数据
- run-all:fill → generate → materialize → record → run tasks → verify 一气呵成
- record:把 workload 翻成 CompareTask 落库
- verify:回归校验 — actual run summary vs yml expected

不包含: SQL EXPLAIN / AI 复核 — 那些去
[/sql-diagnosis](SQL_DIAGNOSIS.md)。

## 写入红线 — 仅 sandbox

`SCENARIO_MATERIALIZE` / `SCENARIO_RUN_ALL` / `SCENARIO_RECORD` 三个 op
**无条件拒** prod / staging,即使 `allow_scenario_write=True` / `allow_record_task=True`
翻开也拒。

这是产品红线:防 admin 误把 prod ds 翻开后造数据灌生产。后端
`app/services/operation_policy.py` 强制。前端按钮 disabled + tooltip 解释只是
UX 提示。

## sandbox 内部 allow_* flag

sandbox 环境 ds 仍要按需翻开:

- `allow_scenario_write=True` → materialize / run-all 放行
- `allow_record_task=True` → record 放行
- `allow_schema_save=True` → schema import save 放行(默认 False)

`make_sandbox_datasource_kwargs()` helper 一键全开(给 demo / 测试用)。

## verify 是只读操作

`SCENARIO_VERIFY` 不走 datasource —— 只读 task_store + history。**任何环境
允许**(包括 unknown)。

## API

```http
POST /api/scenarios/{id}/materialize    # 写入 ds — sandbox only
POST /api/scenarios/{id}/run-all        # 写入 ds — sandbox only
POST /api/scenarios/{id}/record         # 落 CompareTask — sandbox only
GET  /api/scenarios/{id}/verify         # 只读 — 任何环境
POST /api/scenarios/{id}/ai-fill        # LLM 填血肉 — 纯静态,任何环境
POST /api/scenarios/import-from-datasource  # 见 SCHEMA_IMPORT.md
```

详细决策矩阵见 [DATASOURCE_ENVIRONMENT_POLICY.md](DATASOURCE_ENVIRONMENT_POLICY.md)。

## 审计

`scenario.materialize.allowed` / `.denied` 等事件,带 scenario_id + datasource_id
+ environment + db_type + request_id。被拒尝试 admin 在审计页可追溯。
