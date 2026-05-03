/**
 * Batch lineage store —— 多脚本血缘分析工作台 state（batch reactive）+
 * batchActiveTab + batchTabs 元数据 + batchSelectedFileNames computed +
 * analyzeBatch handler（form upload）。
 */
import { computed, reactive, ref } from 'vue'
import { defineStore } from 'pinia'
import { apiForm, apiGet } from '../api'


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
    aiEnabled: false,
    result: null,
    exports: null,
    error: '',
    isAnalyzing: false,
    aiPolling: false,
  })
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))


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

  async function analyzeBatch() {
    batch.error = ''
    batch.result = null
    batch.isAnalyzing = true
    const form = new FormData()
    form.append('dialect', batch.dialect)
    form.append('schema_datasource_id', batch.schemaDatasourceId)
    form.append('schema_name', batch.schemaName)
    form.append('schema_table_filter', batch.schemaTableFilter)
    form.append('schema_only_sql_tables', batch.schemaOnlySqlTables ? 'true' : '')
    form.append('schema_dialect', batch.schemaDialect)
    form.append('ai_enabled', batch.aiEnabled ? 'true' : '')
    batch.files.forEach((file) => form.append('sql_files', file))
    batch.schemaFiles.forEach((file) => form.append('schema_file', file))
    try {
      const payload = await apiForm('/api/lineage/batch/analyze', form)
      batch.result = payload.result
      batch.exports = payload.exports
      pollBatchAIJob(batch)
    } catch (error) {
      batch.error = error.message
    } finally {
      batch.isAnalyzing = false
    }
  }

  async function pollBatchAIJob(target) {
    const jobId = target.result?.ai_enrichment?.job_id
    if (!jobId || target.result?.ai_enrichment?.status !== 'pending') return
    target.aiPolling = true
    try {
      for (let i = 0; i < 120; i += 1) {
        await sleep(2000)
        const enrichment = await apiGet(`/api/lineage/ai/jobs/${jobId}`)
        if (target.result?.ai_enrichment?.job_id !== jobId) return
        target.result.ai_enrichment = enrichment
        if (enrichment.status !== 'pending') return
      }
      if (target.result?.ai_enrichment?.job_id === jobId) {
        target.result.ai_enrichment = {
          ...target.result.ai_enrichment,
          status: 'error',
          error: 'AI 辅助分析仍未完成，请稍后刷新或提高超时时间',
        }
      }
    } catch (error) {
      if (target.result?.ai_enrichment?.job_id === jobId) {
        target.result.ai_enrichment = {
          ...target.result.ai_enrichment,
          status: 'error',
          error: error.message,
        }
      }
    } finally {
      target.aiPolling = false
    }
  }

  return {
    batch, batchActiveTab, batchTabs: BATCH_TABS,
    batchSelectedFileNames,
    resetResult, analyzeBatch,
  }
})
