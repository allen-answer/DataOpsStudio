# SQL 诊断(/sql-diagnosis) — Phase 14 #3

## 范围

只做 SQL 性能诊断,不修改业务数据:

- 静态 preflight(`POST /api/sql-diagnosis/preflight`)— 不连库,纯 AST 检查
- EXPLAIN 执行计划(按方言 dispatch)
- 规则推断 issues + suggestions
- AI 复核(`POST /api/slow-sql/enrich`)
- plan history + plan diff(`/api/slow-sql/plan-history` + `/plan-diff`)

不包含: materialize / DROP / run-all / record / 造数据 — 那些去
[/scenario-lab](SCENARIO_LAB.md)。

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

1. 选 datasource → **OperationRiskPanel** 显示 environment / allow_* / 禁止项
2. 粘 SQL → 点 🔬 分析按钮:
   - 先调 `/api/sql-diagnosis/preflight` 静态检查
   - `blocking=true` → 拦住不让继续
   - `risk_level=medium` → confirm() 让用户确认
   - prod / staging ds → 二次确认弹窗(说明该操作会被审计)
   - 调 `/api/slow-sql/analyze` 拿 plan + issues
3. ✨ AI 复核 → `/api/slow-sql/enrich`(LLM 复核 + 补漏)
4. 📊 plan diff → 跟同 SQL 上次 plan 对比改善 / 回归

## 审计

每次 analyze 会落 audit_log 事件:

- `sql.explain_mysql.allowed` / `.denied`
- `sql.explain_dm.allowed` / `.denied`
- `sql.explain_oracle_plan_table.allowed` / `.denied`

带 `sql_hash`(不存完整 SQL,防泄露)+ datasource_id + environment + db_type +
request_id。admin 可在审计页按 event 过滤追溯。
