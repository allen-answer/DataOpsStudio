# SQL 诊断(/sql-diagnosis) — Phase 14 #3

## 范围

只做 SQL 性能诊断,不修改业务数据。**支持 MySQL / DM / Oracle 三方言。**

- 静态 preflight(`POST /api/sql-diagnosis/preflight`)— 不连库,纯 AST 检查
- EXPLAIN 执行计划(按方言 dispatch)
- 规则推断 issues + suggestions
- AI 复核(`POST /api/slow-sql/enrich`)
- plan history + plan diff(`/api/slow-sql/plan-history` + `/plan-diff`)

不包含: materialize / DROP / run-all / record / 造数据 — 那些去
[/scenario-lab](SCENARIO_LAB.md)。

旧 `/sql-optimize` 已废弃为迁移提示页,不再承载混合功能。`/admin/sandbox`
老 URL 仍 redirect 到 /sql-optimize → 引导用户去 3 个新入口之一。

## 前端 store

`stores/sqlDiagnosis.ts`(facade,过渡阶段引用 sandbox.ts 同一份 reactive
state)— SqlDiagnosisView + QuickOptimizeMode 通过此 store 访问。**不允许
再直接 import sandbox.ts。**

## 方言执行计划

| 方言 | 命令 | 不修改业务表 |
|---|---|---|
| MySQL | `EXPLAIN <select>` | ✓ |
| DM 达梦 | `EXPLAIN <select>` | ✓ |
| Oracle | `EXPLAIN PLAN FOR <select>` → `SELECT FROM PLAN_TABLE` | ✓(但写诊断 PLAN_TABLE) |

**DM 不走 Oracle PLAN_TABLE 路径** — Phase 14 #3 之前共用,现在已拆。
`app/services/slow_sql.py::_analyze_dm` 用 `EXPLAIN SELECT` + `fetch_rows`,
完全独立。

## 生产环境红线

prod / staging datasource 上**禁止**:

- 业务 DML — INSERT / UPDATE / DELETE / MERGE
- 业务 DDL — DROP / ALTER / TRUNCATE / CREATE
- 事务 / 调用 — CALL / EXEC / BEGIN
- SELECT FOR UPDATE(加行锁)
- scenario materialize / run-all / record

prod / staging **允许**(按 allow_* flag 翻开):

- 静态 SQL 检查(preflight)
- EXPLAIN 执行计划 — MySQL/DM 纯只读;Oracle 写诊断 PLAN_TABLE
- AI 复核(纯静态,不连库)
- Plan history / Plan diff(本地操作)

详细决策矩阵见 [DATASOURCE_ENVIRONMENT_POLICY.md](DATASOURCE_ENVIRONMENT_POLICY.md)。

## 前端流程

`/sql-diagnosis` 页面:

1. 选 datasource(MySQL / DM / Oracle)→ **OperationRiskPanel** 业务语义视图:
   - 本次允许的操作(按方言 + allow_* 计算)
   - 本次禁止的操作(prod/staging 显示红线)
   - 方言说明(DM EXPLAIN SELECT / Oracle PLAN_TABLE)
   - 审计字段说明
   - 高级展开 → 9 个技术 allow_* flag 状态
2. 粘 SQL → 点 🔬 分析按钮:
   - 先调 `/api/sql-diagnosis/preflight` 静态检查
   - `blocking=true` → 拦住不让继续
   - `risk_level=medium` → confirm() 让用户确认
   - **任何环境**都弹 `OperationPreviewModal`(替换原 confirm):
     - datasource 名称 / environment / db_type
     - 将执行的动作(按方言)
     - 是否修改业务数据(否)
     - 是否写诊断表(MySQL/DM=否,Oracle=是)
     - 审计字段说明
     - 用户必须勾选 confirm checkbox 才能继续
   - 调 `/api/slow-sql/analyze` 拿 plan + issues
3. ✨ AI 复核 → `/api/slow-sql/enrich`(LLM 复核 + 补漏)
4. 📊 plan diff → 跟同 SQL 上次 plan 对比改善 / 回归

## plan-history 跨项目泄露收口(Phase 14 #3 Round 2)

`GET /api/slow-sql/plan-history` 之前支持 `scenario_id-only` 查询(不绑
datasource),理论上 editor 可拉到别项目的 plan。现已禁用此模式 — 必须同时
提供 `datasource_id + sql_hash` 走 `require_datasource_access` 授权路径。

`scenario_id` 和 `workload_name` 仍可作为筛选条件叠加在 `datasource_id +
sql_hash` 之上(server-side 二次过滤)。

## verify 跨项目泄露收口

`GET /api/scenarios/{id}/verify?project_id=X` 之前不校 project_id,editor
可传别项目 id 拿那项目 verify 结果。现已加 `can_access_project(current, project_id)`,
无权 → 403。

## 审计

每次 analyze 会落 audit_log 事件:

- `sql.explain_mysql.allowed` / `.denied`
- `sql.explain_dm.allowed` / `.denied`
- `sql.explain_oracle_plan_table.allowed` / `.denied`

带 `sql_hash`(不存完整 SQL,防泄露)+ datasource_id + environment + db_type +
request_id。admin 可在审计页按 event 过滤追溯。
