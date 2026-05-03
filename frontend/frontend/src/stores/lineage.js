/**
 * Lineage store —— 单脚本血缘工作台 state（lineage reactive）。
 *
 * 跟 batch store 拆开是因为 LineageWorkbenchView 同时支持单脚本 / 多脚本 /
 * ZIP 模式，每种模式有自己的本地状态（result + error + 上传文件）。
 * 把它们挪到独立 store，App.vue 进一步瘦身。
 */
import { reactive } from 'vue'
import { defineStore } from 'pinia'


function makeLineageDraft() {
  return reactive({
    sql: '',
    dialect: '',
    schemaDatasourceId: '',
    schemaName: '',
    schemaTableFilter: '',
    schemaOnlySqlTables: true,
    schemaDialect: '',
    schemaFiles: [],
    sqlFile: null,
    result: null,
    error: '',
  })
}


export const useLineageStore = defineStore('lineage', () => {
  const lineage = makeLineageDraft()

  function resetResult() {
    lineage.result = null
    lineage.error = ''
  }

  return { lineage, resetResult }
})
