/**
 * Phase 14 #3 — /scenario-lab 专用 store.
 *
 * 当前实现:facade over useSandboxStore — 只暴露 scenario / materialize /
 * record / verify 相关字段,view 和子组件不再直接 import sandbox.ts。
 *
 * 不暴露:quick* SQL 诊断字段、importForm schema 导入字段。
 */
import { defineStore } from 'pinia'
import { useSandboxStore } from './sandbox'

export const useScenarioLabStore = defineStore('scenarioLab', () => {
  const sandbox = useSandboxStore()

  return {
    // === scenario 列表 / 详情 ===
    items: sandbox.items,
    loadingList: sandbox.loadingList,
    selectedId: sandbox.selectedId,
    detail: sandbox.detail,
    detailPath: sandbox.detailPath,
    loadingDetail: sandbox.loadingDetail,
    validScenarios: sandbox.validScenarios,
    brokenScenarios: sandbox.brokenScenarios,

    // === datasource + 选项 ===
    datasourceId: sandbox.datasourceId,
    dropFirst: sandbox.dropFirst,
    aiFill: sandbox.aiFill,
    projectId: sandbox.projectId,
    selectedDs: sandbox.selectedDs,
    selectedDsEnvironment: sandbox.selectedDsEnvironment,
    sandboxWriteLocked: sandbox.sandboxWriteLocked,
    mysqlDatasources: sandbox.mysqlDatasources,
    datasources: sandbox.datasources,

    // === run 状态 + 结果 ===
    materializing: sandbox.materializing,
    recording: sandbox.recording,
    verifying: sandbox.verifying,
    runningAll: sandbox.runningAll,
    materializeResult: sandbox.materializeResult,
    recordResult: sandbox.recordResult,
    verifyResult: sandbox.verifyResult,
    runAllResult: sandbox.runAllResult,
    lastError: sandbox.lastError,

    // === slow_sql per-workload state(嵌在 scenario 详情里) ===
    slowSqlResults: sandbox.slowSqlResults,
    slowSqlAnalyzing: sandbox.slowSqlAnalyzing,
    slowSqlExpanded: sandbox.slowSqlExpanded,
    slowSqlErrors: sandbox.slowSqlErrors,
    planDiffs: sandbox.planDiffs,
    planDiffLoading: sandbox.planDiffLoading,
    planDiffErrors: sandbox.planDiffErrors,
    enrichResults: sandbox.enrichResults,
    enrichLoading: sandbox.enrichLoading,

    // === viewMode(template only — Quick 走 sqlDiagnosis store) ===
    viewMode: sandbox.viewMode,

    // === actions ===
    loadList: sandbox.loadList,
    selectScenario: sandbox.selectScenario,
    runMaterialize: sandbox.runMaterialize,
    runAll: sandbox.runAll,
    runVerify: sandbox.runVerify,
    runRecord: sandbox.runRecord,
    runSlowSqlAnalysis: sandbox.runSlowSqlAnalysis,
    runAiEnrich: sandbox.runAiEnrich,
    runPlanDiff: sandbox.runPlanDiff,
    toggleSlowSqlExpansion: sandbox.toggleSlowSqlExpansion,

    // === helpers ===
    totalRows: sandbox.totalRows,
    anomalyLabel: sandbox.anomalyLabel,
    isSelected: sandbox.isSelected,
    renderSql: sandbox.renderSql,
    planColumns: sandbox.planColumns,
    statusBadgeClass: sandbox.statusBadgeClass,
    statusLabel: sandbox.statusLabel,
    verdictBadgeClass: sandbox.verdictBadgeClass,
    confidenceBadgeClass: sandbox.confidenceBadgeClass,
    isPlanDiffImproved: sandbox.isPlanDiffImproved,
    isPlanDiffRegressed: sandbox.isPlanDiffRegressed,

    // Import 也开 — ScenarioLabView 顶部有"从 datasource 导入"快捷入口
    openImportDialog: sandbox.openImportDialog,

    // 跳转 (ResultPanels 用)
    gotoTask: sandbox.gotoTask,
    gotoHistory: sandbox.gotoHistory,
  }
})
