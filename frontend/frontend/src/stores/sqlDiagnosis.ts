/**
 * Phase 14 #3 — /sql-diagnosis 专用 store.
 *
 * 当前实现:facade over useSandboxStore — 引用同一份 reactive state,
 * 只是按职责裁剪暴露的字段 + 重命名(quickXxx → 去掉 quick 前缀)以便
 * SqlDiagnosisView / QuickOptimizeMode 不再直接 import sandbox.ts。
 *
 * 长期目标:把 quick state + analyze/enrich/diff actions 真正抽出 sandbox.ts;
 * 当前 facade 让视图层 import 干净,sandbox.ts 内部待真正迁移。
 *
 * 不暴露:scenario / materialize / record / verify / importForm 等不相关字段。
 */
import { defineStore, storeToRefs } from 'pinia'
import { useSandboxStore } from './sandbox'

export const useSqlDiagnosisStore = defineStore('sqlDiagnosis', () => {
  const sandbox = useSandboxStore()

  // **关键**:用 storeToRefs 把 sandbox 内部的 ref 真正"穿透"过来,而不是写
  // `quickSql: sandbox.quickSql` —— 后者会取出当前 unwrap 后的 value(普通
  // string),丢失 ref 引用,导致 `store.quickSql = x` 写不进底层 sandbox.quickSql.value。
  // 这条 bug 直接表现:从 SQL 工作台 sendTo('diagnosis') 后,SqlDiagnosisView
  // 调 `store.quickSql = transfer.sql` 看似成功,但 runQuickAnalyze 读到的 sandbox
  // 内部 quickSql.value 仍是空 → 立即 return → 整个"跑 EXPLAIN"按钮静默失败。
  const refs = storeToRefs(sandbox)

  return {
    // === SQL 诊断专属 state(reactive ref 透传)===
    quickSql: refs.quickSql,
    quickDatasourceId: refs.quickDatasourceId,
    quickTagScenarioId: refs.quickTagScenarioId,
    quickAnalyzing: refs.quickAnalyzing,
    quickEnriching: refs.quickEnriching,
    quickPlanDiffLoading: refs.quickPlanDiffLoading,
    quickResult: refs.quickResult,
    quickEnrichResult: refs.quickEnrichResult,
    quickPlanDiff: refs.quickPlanDiff,
    quickPlanHistory: refs.quickPlanHistory,
    quickError: refs.quickError,
    quickPlanDiffError: refs.quickPlanDiffError,
    confirmAnalyzePromise: refs.confirmAnalyzePromise,

    // === 共享 derived (computed/getter)===
    diagnosableDatasources: refs.diagnosableDatasources,
    // 兼容旧名(QuickOptimizeMode 历史引用):mysqlDatasources
    mysqlDatasources: refs.mysqlDatasources,
    datasources: refs.datasources,

    // === actions(函数引用,不需要 storeToRefs)===
    runQuickAnalyze: sandbox.runQuickAnalyze,
    runQuickEnrich: sandbox.runQuickEnrich,
    runQuickPlanDiff: sandbox.runQuickPlanDiff,
    refreshQuickHistory: sandbox.refreshQuickHistory,
    clearQuickAnalysis: sandbox.clearQuickAnalysis,

    // === helpers ===
    planColumns: sandbox.planColumns,
    verdictBadgeClass: sandbox.verdictBadgeClass,
    confidenceBadgeClass: sandbox.confidenceBadgeClass,
    isPlanDiffImproved: sandbox.isPlanDiffImproved,
    isPlanDiffRegressed: sandbox.isPlanDiffRegressed,

    // bootstrap reload — 用于初始拉 datasources 列表
    loadList: sandbox.loadList,
  }
})
