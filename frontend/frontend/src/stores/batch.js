/**
 * Batch lineage store —— 多脚本血缘分析工作台 state（batch reactive）+
 * batchActiveTab + batchTabs 元数据 + batchSelectedFileNames computed。
 */
import { computed, reactive, ref } from 'vue'
import { defineStore } from 'pinia'


function makeBatchDraft() {
  return reactive({
    dialect: '',
    schemaDatasourceId: '',
    schemaName: '',
    schemaTableFilter: '',
    schemaOnlySqlTables: true,
    schemaDialect: '',
    schemaFiles: [],
    files: [],
    result: null,
    exports: null,
    error: '',
  })
}


const BATCH_TABS = [
  { id: 'overview', label: '流程总览' },
  { id: 'graph', label: '数据流图' },
  { id: 'files', label: '脚本清单' },
  { id: 'edges', label: '表级流转' },
  { id: 'deps', label: '跨脚本依赖' },
  { id: 'dag', label: 'DAG 分析' },
  { id: 'warnings', label: '风险提示' },
]


export const useBatchStore = defineStore('batch', () => {
  const batch = makeBatchDraft()
  const batchActiveTab = ref('overview')

  const batchSelectedFileNames = computed(() => batch.files.map((file) => file.name))

  function resetResult() {
    batch.result = null
    batch.exports = null
    batch.error = ''
  }

  return {
    batch, batchActiveTab, batchTabs: BATCH_TABS,
    batchSelectedFileNames,
    resetResult,
  }
})
