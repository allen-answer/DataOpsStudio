# Roadmap

只描述「下一步要做什么」和「这一轮明确不做什么」。历史已交付的功能和决策见 `CLAUDE.md`，那里是按 phase 归档的完整设计 + ADR；本文只是把"当下重点"和"刻意暂缓"切干净。

---

## 核心定位

DataOps Studio 是**多数据库数据对比 + SQL 血缘 + 参数化作业流**三件套。所有功能必须落到这三件之一，或者直接服务于它们（鉴权 / 项目空间 / 调度 / 通知）。

不是 Atlan / DataHub，不当资产治理平台；不是 Airflow，不当通用编排器；不是 Metabase，不当 BI。

---

## 当前阶段（"工程收敛 / 工程化加固"）

把已有的能力跑稳、跑细、跑可交付，不再铺新功能。重点：

1. **冒烟 / 回归** —— `docs/SMOKE_TEST.md` 跑得通；`pytest` 全绿是发版必要条件。
2. **SQL 安全** —— `tests/test_sql_guard.py` 覆盖所有已知绕过姿势，每加一个 DB 方言就跑一遍。
3. **驱动 / 离线打包** —— `docs/DRIVER_MATRIX.md` 是离线部署 SOP，发版前对照清单确认 wheel 全。
4. **大数据比对结果落盘** —— `docs/COMPARE_RESULT_STORAGE.md`（设计先行，本轮**不实现**），把 JSON 单文件的内存 / IO 上限定下界。
5. **README 收敛** —— 一页讲清楚部署形态，细节都迁到 `docs/`。

完成判定：上面 5 个文档 + tests 都进仓库，发版流程能照单跑。

---

## 接下来要做（短期 backlog）

按优先级：

### P0 · 工程化（本轮 + 紧接一轮）
- [x] `docs/SMOKE_TEST.md` —— 部署 / 升级前必跑
- [x] `docs/DRIVER_MATRIX.md` —— 驱动选择 + 离线打包风险
- [x] `docs/COMPARE_RESULT_STORAGE.md` —— 大结果落盘方案（**仅设计**）
- [x] `tests/test_sql_guard.py` —— SELECT/WITH 允许 + DML/DDL/多语句/FOR UPDATE 拒绝 + 字符串注释不误判
- [x] `release-please` → tag → Windows offline 打包 链路 dry-run 通过 + release.yml 加 `workflow_dispatch` / `draft: false` / `generate_release_notes: false`
- [x] `frontend-build` typecheck 全绿（P0.5 收口；详见下面）
- [x] **P0.4 · 后端 endpoint 强制鉴权**：13 个 API 文件挂 `Depends(get_current_user)` + role check，覆盖核心 5（datasources/tasks/workflows/history/config_io）+ stretch 7（workflow_runs/uploads/scheduler/runs/lineage/assets/search/lineage_graph/scenarios/slow_sql/system）+ projects（已挂）。新增 `docs/AUTHORIZATION_MATRIX.md` 是后端权限 SOT；新增 `tests/test_api_auth_matrix.py` 31 用例覆盖 401/403/200 矩阵。conftest 加 `client / client_admin / client_editor / client_viewer / client_anon` fixture，老业务测试默认 admin token 零改动
- [x] **P0.5 · 收尾全部 view 的 typecheck 红**：14 个 view + bootstrap/history store 的 157 行 TS 报错全部修绿；修法以最小侵入为主（event handler cast `as HTMLInputElement` / 接口加缺失字段 / 顶层 `state.xxx as any[]` 兜底待 schema 抽全）。`npm run typecheck` 0 error；npm test 34/34；npm run build 通过。CI frontend-build job 可全绿
- [ ] **P0.6 · 真 Docker + demo-db SMOKE 实测**：P0.3 本地 uvicorn 跑了 §1/§7/§9 段，但 §3 数据源 / §4 数据对比 / §5 血缘 / §6 作业流 全跳过（需要 Docker + 浏览器）。下次 oncall 在有 Docker 的机器上跑一次全套

### P1 · 数据对比深化
- [ ] 大结果落盘真实实现（按 `docs/COMPARE_RESULT_STORAGE.md` 切片 1 起步）
- [ ] 流式对比 + 文件源支持（Parquet 已自描述有序，CSV 需 ORDER 校验）
- [ ] 字段映射的"按位置自动对齐"在 schema 差异大时给更明确的 warning

### P2 · 血缘可信度
- [ ] DM 真实生产脚本的字段级血缘验证（找一批客户脚本跑回归）
- [ ] sqlglot 解析失败时的 fallback 链路统一（procedure_segments / dynamic_sql / parse_errors 三路报告 UI 收口）
- [ ] OceanBase 独立 enum + UI 显式选项（区分 ob_mysql / ob_oracle，不再借 MySQL/Oracle 类型）

### P3 · 作业流 / 调度
- [ ] sensor 健康面板（最近触发时间 / 失败原因 / 跳过次数）
- [ ] 节点级 retry 策略（次数 + backoff）
- [ ] workflow 模板市场（多个客户场景沉淀模板）

---

## 本轮例外：scenario column_distributions

工程收敛轮启动**之前**已经落地的 commit `f0782b4`（AI filler v2 — realistic 列 dist_params 分布参数）和 `b31f6dd`（CI scenario lint + 夜间回归 workflow 模板）实际上扩展了 Scenario / AI 能力。本轮**承认这是已成事实，纳入冻结基线**，但**之后不再继续扩**：

- ✅ 保留：`column_distributions` / `dist_params` / lognormal / normal / uniform / exponential 4 分布族 / AI filler v2 prompt
- ✅ 保留：`scripts/scenario_lint.py` + `.github/workflows/scenario-nightly.yml`（测试治理性质）
- ❌ 之后不做：v3 接 Faker locale / lineage_script 条件分支 / 新分布族 / 新 anomaly kind / 新 AI 复核场景

如果之后真的需要扩 Scenario / AI，**走单独的 RFC**，不再放任「上一轮的延续」自然展开。

---

## 本轮明确不做（important: 不主动碰）

> 这些功能要么已经做过一轮（在 `CLAUDE.md` 历史 phase 里）但当前不再扩，要么超出"核心定位"。**所有新需求落到这一列必须先升级讨论**，不要一边做工程收敛一边偷偷加。

| 不做的功能 | 不做的原因 | 当前状态 |
|----------|---------|---------|
| **新增 AI 能力** | enrichment / inference / 错误翻译 / 字段映射推荐 / slow-sql 复核 / scenario AI filler 已交付，本轮不加新 LLM 场景；不调 prompt、不接新 provider | 在 `app/ai/` 里冻结 |
| **资产中心（asset center）扩展** | 资产详情页 + custom aspects + 字段血缘热点 + governance dashboard 已交付，本轮**不加新 aspect type、不做血缘图叠加 PII/SLA 徽章**（已有但不深化） | Phase 10 enhancement 已经全落地 |
| **慢 SQL 分析** | 规则推断 + AI 复核 + Oracle/DM EXPLAIN PLAN 已交付；本轮**不加新规则、不接新方言、不做执行计划可视化** | Phase 12 已经交付 |
| **OpenLineage / DataHub / Marquez emitter** | 三 target type 已对接，本轮**不加新外部系统集成、不做事件 schema 升级** | `services/openlineage_emitter.py` 冻结 |
| **企业微信 / 邮箱 / webhook 通知** | 三 channel 已交付，本轮**不加新 channel、不做模板系统** | `services/notifier.py` 冻结 |
| **浏览器自动化 / Playwright** | e2e 测试可以照常用，但**不引入产品级的浏览器自动化能力**（如截屏报告、UI 操作录制） | `tests/e2e/` 冻结，不在产品里增 endpoint |
| **AI 测试沙盒（scenario）扩展** | DSL + materializer + verifier + orchestrator + column_distributions 全交付（见上面「本轮例外」），本轮**不加新 anomaly kind、不接 Faker locale、不做 lineage_script 模板变量条件分支、不加新分布族** | Phase 12 + column_distributions 冻结 |
| **图引擎转正 Cytoscape** | G6 默认 + Cytoscape 实验通道共存，**不强迫切换**；等真实客户大图压测有 empirical 数据再判 | Phase 10 #5 ADR 已记 |

新需求落进这一列，**先到 issue 讨论 → 进入下一轮 Roadmap**，不直接 PR。

---

## 长期方向（不排期，只记方向）

- 字段级血缘的方言独立配置（Oracle PL/SQL / DM 复杂存储过程的边界 case）
- 数据对比的列存格式（Parquet）原生读 + 流式 join
- 大对比任务的分布式执行（一台机器跑不动的场景，目前没需求驱动）
- 多租户 / 强 RBAC（admin / editor / viewer 之上更细的资源权限）

---

## 决策原则（对未来 PR 的硬约束）

1. **新功能必须问"这是不是核心三件套"** —— 不是就拒绝合并。
2. **新依赖必须问"离线包能不能装"** —— 加重依赖前先验证 `docs/DRIVER_MATRIX.md` 流程能不能带过去。
3. **新文件 / 新 endpoint 必须问"有没有删掉的旧文件"** —— 防止文件膨胀。
4. **不做向后兼容垫片** —— 删字段就删，不留 `@deprecated` 包装；用户数据有迁移成本时单独跑迁移脚本。
5. **AI 能力永不替代规则结论** —— 6 不变量见 `CLAUDE.md` Phase 7 章节，这个原则跨 phase 不动。

---

如发现 Roadmap 跟实际 PR 流向脱节（已经在做的事不在 P0~P3 里），优先更新 Roadmap，不要在文档外悄悄展开。
