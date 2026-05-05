/**
 * Lineage store —— 单脚本血缘工作台 state（lineage reactive） + analyze handler。
 *
 * 跟 batch store 拆开是因为 LineageWorkbenchView 同时支持单脚本 / 多脚本 /
 * ZIP 模式，每种模式有自己的本地状态（result + error + 上传文件）。
 *
 * S3.B：迁 .ts。result / ai_enrichment 跟 batch store 同样用 unknown 兜底，
 * 等 lineage 模型在前端有统一类型再细化。
 */
import { reactive, ref } from 'vue'
import { defineStore } from 'pinia'
import { apiForm, apiGet, apiJson } from '../api'


export interface LineageDraft {
  sql: string
  dialect: string
  schemaDatasourceId: string
  schemaName: string
  schemaTableFilter: string
  schemaOnlySqlTables: boolean
  schemaDialect: string
  schemaFiles: File[]
  sqlFile: File | null
  aiEnabled: boolean
  result: Record<string, unknown> | null
  error: string
  isAnalyzing: boolean
  aiPolling: boolean
}

export interface LineageAIStatus {
  enabled?: boolean
  provider?: string
  [key: string]: unknown
}


function makeLineageDraft(): LineageDraft {
  return reactive<LineageDraft>({
    sql: '',
    dialect: '',
    schemaDatasourceId: '',
    schemaName: '',
    schemaTableFilter: '',
    schemaOnlySqlTables: true,
    schemaDialect: '',
    schemaFiles: [],
    sqlFile: null,
    aiEnabled: false,
    result: null,
    error: '',
    isAnalyzing: false,
    aiPolling: false,
  })
}

const sleep = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms))


export const useLineageStore = defineStore('lineage', () => {
  const lineage = makeLineageDraft()
  const lineageAIStatus = ref<LineageAIStatus | null>(null)

  function resetResult(): void {
    lineage.result = null
    lineage.error = ''
  }

  async function analyzeLineage(): Promise<void> {
    lineage.error = ''
    lineage.result = null
    lineage.isAnalyzing = true
    try {
      if (lineage.sqlFile || lineage.schemaFiles.length) {
        const form = new FormData()
        form.append('sql', lineage.sql)
        form.append('dialect', lineage.dialect)
        form.append('schema_datasource_id', lineage.schemaDatasourceId)
        form.append('schema_name', lineage.schemaName)
        form.append('schema_table_filter', lineage.schemaTableFilter)
        form.append('schema_only_sql_tables', lineage.schemaOnlySqlTables ? 'true' : '')
        form.append('schema_dialect', lineage.schemaDialect)
        form.append('ai_enabled', lineage.aiEnabled ? 'true' : '')
        if (lineage.sqlFile) form.append('sql_file', lineage.sqlFile)
        lineage.schemaFiles.forEach((file) => form.append('schema_file', file))
        lineage.result = await apiForm('/api/lineage/analyze-form', form) as Record<string, unknown>
      } else {
        lineage.result = await apiJson('/api/lineage/analyze', 'POST', {
          sql: lineage.sql,
          dialect: lineage.dialect,
          schema_datasource_id: lineage.schemaDatasourceId,
          schema_name: lineage.schemaName,
          schema_table_filter: lineage.schemaTableFilter,
          schema_only_sql_tables: lineage.schemaOnlySqlTables ? 'true' : '',
          schema_dialect: lineage.schemaDialect,
          ai_enabled: lineage.aiEnabled ? 'true' : '',
          schema: '',
        }) as Record<string, unknown>
      }
      pollLineageAIJob(lineage)
    } catch (error) {
      lineage.error = error instanceof Error ? error.message : String(error)
    } finally {
      lineage.isAnalyzing = false
    }
  }

  async function pollLineageAIJob(target: LineageDraft): Promise<void> {
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

  async function loadLineageAIStatus(): Promise<LineageAIStatus | null> {
    lineageAIStatus.value = await apiGet('/api/lineage/ai/status') as LineageAIStatus
    return lineageAIStatus.value
  }

  return { lineage, lineageAIStatus, resetResult, analyzeLineage, loadLineageAIStatus }
})
