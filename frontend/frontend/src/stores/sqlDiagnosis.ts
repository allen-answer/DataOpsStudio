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
import { defineStore } from 'pinia'
import { useSandboxStore } from './sandbox'

export const useSqlDiagnosisStore = defineStore('sqlDiagnosis', () => {
  const sandbox = useSandboxStore()

  return {
    // === SQL 诊断专属 state ===
    quickSql: sandbox.quickSql,
    quickDatasourceId: sandbox.quickDatasourceId,
    quickTagScenarioId: sandbox.quickTagScenarioId,
    quickAnalyzing: sandbox.quickAnalyzing,
    quickEnriching: sandbox.quickEnriching,
    quickPlanDiffLoading: sandbox.quickPlanDiffLoading,
    quickResult: sandbox.quickResult,
    quickEnrichResult: sandbox.quickEnrichResult,
    quickPlanDiff: sandbox.quickPlanDiff,
    quickPlanHistory: sandbox.quickPlanHistory,
    quickError: sandbox.quickError,
    quickPlanDiffError: sandbox.quickPlanDiffError,
    confirmAnalyzePromise: sandbox.confirmAnalyzePromise,

    // === 共享 derived (datasources 列表) ===
    diagnosableDatasources: sandbox.diagnosableDatasources,
    // 兼容旧名(QuickOptimizeMode 历史引用):mysqlDatasources
    mysqlDatasources: sandbox.mysqlDatasources,
    datasources: sandbox.datasources,

    // === actions ===
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
