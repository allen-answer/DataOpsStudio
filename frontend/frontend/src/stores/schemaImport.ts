/**
 * Phase 14 #3 — /schema-import 专用 store.
 *
 * 当前 SchemaImportView 已自包含 form/result 状态(没用 sandbox 内部
 * importForm),所以这个 facade 主要导出 sandbox 的 datasources 列表 +
 * ImportDialog 兼容(用在 ScenarioLabView 顶部入口)。
 */
import { defineStore } from 'pinia'
import { useSandboxStore } from './sandbox'

export const useSchemaImportStore = defineStore('schemaImport', () => {
  const sandbox = useSandboxStore()

  return {
    // === 共享 datasources 列表 ===
    datasources: sandbox.datasources,
    mysqlDatasources: sandbox.mysqlDatasources,
    diagnosableDatasources: sandbox.diagnosableDatasources,

    // === ImportDialog 复用(ScenarioLabView 用) ===
    importDialogOpen: sandbox.importDialogOpen,
    importing: sandbox.importing,
    importResult: sandbox.importResult,
    importError: sandbox.importError,
    importForm: sandbox.importForm,
    openImportDialog: sandbox.openImportDialog,
    submitImport: sandbox.submitImport,
    copyImportYml: sandbox.copyImportYml,

    // bootstrap reload
    loadList: sandbox.loadList,
  }
})
