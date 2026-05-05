/**
 * Batch lineage store —— 多脚本血缘分析工作台 state（batch reactive）+
 * batchActiveTab + batchTabs 元数据 + batchSelectedFileNames computed +
 * analyzeBatch handler（form upload）。
 *
 * S3.B：迁 .ts。result / exports 来自后端 lineage payload，结构复杂跨多 view
 * 共享，先用 unknown 兜底；等 lineage models 在前端有统一类型定义再细化。
 */
import { computed, reactive, ref } from 'vue'
import { defineStore } from 'pinia'
import { apiForm, apiGet } from '../api'


export interface BatchDraft {
  dialect: string
  schemaDatasourceId: string
  schemaName: string
  schemaTableFilter: string
  schemaOnlySqlTables: boolean
  schemaDialect: string
  schemaFiles: File[]
  files: File[]
  aiEnabled: boolean
  result: Record<string, unknown> | null
  exports: Record<string, unknown> | null
  error: string
  isAnalyzing: boolean
  aiPolling: boolean
}

function makeBatchDraft(): BatchDraft {
  return reactive<BatchDraft>({
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

const sleep = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms))


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
  const batchActiveTab = ref<string>('overview')

  const batchSelectedFileNames = computed<string[]>(
    () => batch.files.map((file) => file.name),
  )

  function resetResult(): void {
    batch.result = null
    batch.exports = null
    batch.error = ''
  }

  async function analyzeBatch(): Promise<void> {
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
      const payload = await apiForm('/api/lineage/batch/analyze', form) as { result?: Record<string, unknown>; exports?: Record<string, unknown> }
      batch.result = payload.result || null
      batch.exports = payload.exports || null
      pollBatchAIJob(batch)
    } catch (error) {
      batch.error = error instanceof Error ? error.message : String(error)
    } finally {
      batch.isAnalyzing = false
    }
  }

  async function pollBatchAIJob(target: BatchDraft): Promise<void> {
    const enrich = target.result?.ai_enrichment as { job_id?: string; status?: string } | undefined
    const jobId = enrich?.job_id
    if (!jobId || enrich?.status !== 'pending') return
    target.aiPolling = true
    try {
      for (let i = 0; i < 120; i += 1) {
        await sleep(2000)
        const enrichment = await apiGet(`/api/lineage/ai/jobs/${jobId}`) as { status?: string }
        const cur = target.result?.ai_enrichment as { job_id?: string } | undefined
        if (cur?.job_id !== jobId) return
        if (target.result) target.result.ai_enrichment = enrichment
        if (enrichment.status !== 'pending') return
      }
      const stillPending = target.result?.ai_enrichment as { job_id?: string } | undefined
      if (stillPending?.job_id === jobId && target.result) {
        target.result.ai_enrichment = {
          ...(target.result.ai_enrichment as object),
          status: 'error',
          error: 'AI 辅助分析仍未完成，请稍后刷新或提高超时时间',
        }
      }
    } catch (error) {
      const cur = target.result?.ai_enrichment as { job_id?: string } | undefined
      if (cur?.job_id === jobId && target.result) {
        target.result.ai_enrichment = {
          ...(target.result.ai_enrichment as object),
          status: 'error',
          error: error instanceof Error ? error.message : String(error),
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
