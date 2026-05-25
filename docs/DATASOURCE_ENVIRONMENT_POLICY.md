# Datasource Environment Policy (Phase 14 #3)

## 设计目标

把"哪些操作允许"从 API 端点散在的 if-else 改成集中决策,按
`(environment, db_type, allow_*)` 三元组矩阵化。fail-safe:未确认环境 / 未开
flag 一律拒绝。后端 `app/services/operation_policy.py` 强制,前端只做 UX 提示。

## environment 标签

| 值 | 语义 | 默认 |
|---|---|---|
| `unknown` | 环境未确认 — fail-safe 默认值 | ✓ 新 ds / 旧 ds 缺字段 |
| `sandbox` | 沙盒环境 — 可造数据 / 跑模拟流程 | demo init_db 显式标 |
| `staging` | 预发环境 — 默认收紧(只读) | 由 admin 手动标 |
| `prod` | 生产环境 — 仅按 allow_* 翻开能力 | 由 admin 手动标 |

旧 datasources.json 缺 `environment` 字段时 pydantic 自动赋 `unknown`,
所有高风险操作拒。admin 必须手动到数据源管理页确认环境标签后才能解锁。

## 9 个 allow_* 显式开关

```python
environment_verified: bool = False   # admin 是否确认过环境
allow_select: bool = True             # 普通业务 SELECT(默认 True)
allow_explain: bool = False           # MySQL EXPLAIN
allow_dm_explain: bool = False        # DM EXPLAIN
allow_oracle_plan_table: bool = False # Oracle EXPLAIN PLAN FOR + PLAN_TABLE
allow_schema_import: bool = False     # information_schema 读取
allow_schema_save: bool = False       # 落 yml 到 config/scenarios
allow_scenario_write: bool = False    # materialize / run-all
allow_record_task: bool = False       # record:CompareTask 落库
```

## 11 个 Operation 决策矩阵

| Operation | unknown | sandbox | staging | prod |
|---|---|---|---|---|
| `SQL_STATIC_PREFLIGHT` | ✓ | ✓ | ✓ | ✓ |
| `SQL_AI_ENRICH` | ✓ | ✓ | ✓ | ✓ |
| `SQL_EXPLAIN_MYSQL` | ✗ | ✓ | allow_explain | allow_explain |
| `SQL_EXPLAIN_DM` | ✗ | ✓ | allow_dm_explain or allow_explain | allow_dm_explain or allow_explain |
| `SQL_EXPLAIN_ORACLE_PLAN_TABLE` | ✗ | ✓ | allow_oracle_plan_table | allow_oracle_plan_table |
| `SCENARIO_VERIFY` | ✓ | ✓ | ✓ | ✓ |
| `SCENARIO_MATERIALIZE` | ✗ | allow_scenario_write | ✗ 红线 | ✗ 红线 |
| `SCENARIO_RUN_ALL` | ✗ | allow_scenario_write | ✗ 红线 | ✗ 红线 |
| `SCENARIO_RECORD` | ✗ | allow_record_task | ✗ 红线 | ✗ 红线 |
| `SCHEMA_IMPORT_PREVIEW` | ✗ | ✓ | allow_schema_import | allow_schema_import |
| `SCHEMA_IMPORT_SAVE` | ✗ | allow_schema_save | ✗ 红线 | ✗ 红线 |

**红线** = 即使 allow_* 翻开也拒。产品红线:scenario 写入 / schema save 仅
sandbox 允许,防 admin 误把 prod ds 翻开后造数据。

## DM / Oracle / MySQL 执行计划差异

| 方言 | 命令 | 是否写表 | 控制 flag |
|---|---|---|---|
| MySQL | `EXPLAIN <select>` | 不写表 | `allow_explain` |
| DM 达梦 | `EXPLAIN <select>` | 不写表(纯只读) | `allow_dm_explain` or `allow_explain` |
| Oracle | `EXPLAIN PLAN FOR <select>` → `SELECT FROM PLAN_TABLE` | ⚠ 写诊断 PLAN_TABLE(非业务表) | `allow_oracle_plan_table` |

**DM 不再走 Oracle PLAN_TABLE 路径**(Phase 14 #3 fix)— 之前共用导致 DM
也会被引去写 PLAN_TABLE,这是错误的。现在 `_analyze_dm` 完全独立用
`EXPLAIN SELECT` + `fetch_rows`。

**/sql-diagnosis 前端支持三方言**(Phase 14 #3 Round 2)— `diagnosableDatasources`
computed 含 `db_type in ("mysql", "dm", "oracle")`。下拉文案改成
「选择 MySQL / DM / Oracle 数据源,系统将按方言查看执行计划」。

## 升级旧 datasource

升级到 Phase 14 #3 后,旧 datasources.json 缺 `environment` 字段时 pydantic
自动赋 `unknown` — 高风险操作全拒。admin 必须:

1. 进数据源管理页
2. 编辑每个 ds → 选 environment(sandbox / staging / prod)
3. 按需翻开对应 allow_* flag

`make_sandbox_datasource_kwargs()` helper 给 demo / 测试 fixture 一键全开:

```python
from app.models.datasource import DataSourceCreate, make_sandbox_datasource_kwargs

DataSourceCreate(
    name="demo-mysql", db_type=DatabaseType.MYSQL, host="...", port=3306,
    **make_sandbox_datasource_kwargs(),
)
```

## audit_log

`assert_operation_allowed` 同时落审计,allow / deny 都记:

```
event = "{operation}.{allowed|denied}"   # 如 sql.explain_dm.denied
extra = {
    operation, datasource_id, datasource_name,
    environment, db_type, allowed, reason,
    request_id, **caller_context,        # sql_hash / scenario_id / 等
}
```

admin 在审计页按 `resource='auth_event'` + `event` 过滤可看到"谁试图在 prod
上跑沙盒写入"等触发记录。
