/**
 * SQL Workbench store —— 跟后端 /api/sql-workbench/* 同步。
 *
 * 数据流:
 *   1. App.vue / view onMounted → loadConsoles() / loadHistory()
 *   2. 用户点 + tab → createConsole()(乐观更新本地 + 后端同步)
 *   3. 用户改 SQL / 切 ds → debounced saveConsole()
 *   4. 用户点 Run → execute() → 写 lastResult + 触发 history reload
 *   5. 用户点 X 关 tab → deleteConsole()
 */
import { computed, reactive, ref } from 'vue'
import { defineStore } from 'pinia'
import { apiGet, apiJson } from '../api'

export interface Console {
  id: string
  name: string
  datasource_id: string
  sql: string
  project_id: string
  owner_user_id: string
  created_at: string
  updated_at: string
}

export interface ExecuteResponse {
  success: boolean
  columns: string[]
  rows: unknown[][]
  row_count: number
  elapsed_ms: number
  truncated: boolean
  error?: string | null
}

export interface ExecutionEnvelope extends Partial<ExecuteResponse> {
  execution_id: string
  status: 'running' | 'done' | 'failed' | 'cancelled'
  cancel_requested?: boolean
}

export interface FormatResponse {
  success: boolean
  formatted_sql: string
  dialect: string
  error?: string | null
}

export interface ExplainResponse {
  success: boolean
  dialect: string
  columns: string[]
  rows: unknown[][]
  explain_sql: string
  elapsed_ms: number
  unsupported: boolean
  error?: string | null
}

export interface HistoryEntry {
  id: string
  datasource_id: string
  datasource_name: string
  sql: string
  executed_by: string
  project_id: string
  executed_at: string
  success: boolean
  elapsed_ms: number
  row_count: number
  truncated: boolean
  error?: string | null
}

export interface MetadataSchema {
  name: string
  loading?: boolean
  expanded?: boolean
  tables?: MetadataTable[]
}

export interface MetadataTable {
  name: string
  schema: string
  // columns 是惰性的:用户点表头才拉,拉过的写回这里供补全用
  columns?: string[]
  columnsLoading?: boolean
}

// 本地草稿的 localStorage key 前缀。每个 console 独立一份,
// 跟后端 console.sql 是两条并行通道:后端代表"已保存",draft 代表"未保存编辑"。
const DRAFT_KEY_PREFIX = 'dataops.sqlWorkbench.draft.'

export const useSqlWorkbenchStore = defineStore('sqlWorkbench', () => {
  // ─── state ────────────────────────────────────────────────────────────
  const consoles = ref<Console[]>([])
  const activeConsoleId = ref<string>('')
  // 每个 console 一个 result —— 切 tab 时不丢之前的结果
  const results = reactive<Record<string, ExecuteResponse | null>>({})
  // 每个 console 一个 running 状态
  const running = reactive<Record<string, boolean>>({})
  const history = ref<HistoryEntry[]>([])
  const loadingConsoles = ref(false)

  // metadata tree —— 按 datasource_id 缓存,切换数据源时复用
  const metadataByDs = reactive<Record<string, { schemas: MetadataSchema[]; loading: boolean; error: string }>>({})

  // v0.2:每个 console 当前 in-flight execution_id(用于 cancel)
  const currentExecutionId = reactive<Record<string, string>>({})
  // v0.2:explain 结果按 console_id 缓存
  const explainResults = reactive<Record<string, ExplainResponse | null>>({})

  const activeConsole = computed(() =>
    consoles.value.find(c => c.id === activeConsoleId.value) || null,
  )

  // ─── actions ──────────────────────────────────────────────────────────

  async function loadConsoles(): Promise<void> {
    loadingConsoles.value = true
    try {
      const resp = await apiGet<{ items: Console[] }>('/api/sql-workbench/consoles')
      consoles.value = resp.items || []
      // 若没 active,默认选第一个
      if (consoles.value.length && !activeConsole.value) {
        activeConsoleId.value = consoles.value[0].id
      }
    } finally {
      loadingConsoles.value = false
    }
  }

  async function createConsole(payload?: {
    name?: string
    datasource_id?: string
    sql?: string
  }): Promise<Console> {
    const body = {
      name: payload?.name || _defaultName(),
      datasource_id: payload?.datasource_id || '',
      sql: payload?.sql || '',
    }
    const created = await apiJson<Console>('/api/sql-workbench/consoles', 'POST', body)
    consoles.value.push(created)
    activeConsoleId.value = created.id
    return created
  }

  async function updateConsole(
    id: string,
    patch: Partial<Pick<Console, 'name' | 'sql' | 'datasource_id'>>,
  ): Promise<Console> {
    const updated = await apiJson<Console>(`/api/sql-workbench/consoles/${id}`, 'PUT', patch)
    const idx = consoles.value.findIndex(c => c.id === id)
    if (idx >= 0) consoles.value[idx] = updated
    return updated
  }

  async function deleteConsole(id: string): Promise<void> {
    await apiJson(`/api/sql-workbench/consoles/${id}`, 'DELETE')
    consoles.value = consoles.value.filter(c => c.id !== id)
    delete results[id]
    delete running[id]
    clearDraft(id)
    if (activeConsoleId.value === id) {
      activeConsoleId.value = consoles.value[0]?.id || ''
    }
  }

  /**
   * v0.2:execute 改成异步路径 —— 立刻拿 execution_id + status;running 时
   * 客户端 poll 直到 done/failed/cancelled。中间 running 期间用户可点"停止"。
   */
  async function execute(consoleId: string, payload: {
    datasource_id: string
    sql: string
    max_rows?: number
  }): Promise<ExecuteResponse | null> {
    running[consoleId] = true
    try {
      const env = await apiJson<ExecutionEnvelope>('/api/sql-workbench/execute', 'POST', {
        datasource_id: payload.datasource_id,
        sql: payload.sql,
        max_rows: payload.max_rows || 1000,
        console_id: consoleId,
      })
      currentExecutionId[consoleId] = env.execution_id

      // 已完成:envelope 平铺含 success/columns/rows
      if (env.status === 'done' || env.status === 'failed') {
        const r = _envelopeToResult(env)
        results[consoleId] = r
        loadHistory().catch(() => {})
        return r
      }
      if (env.status === 'cancelled') {
        results[consoleId] = { success: false, columns: [], rows: [], row_count: 0, elapsed_ms: 0, truncated: false, error: '已取消' }
        return results[consoleId]
      }

      // running:poll 直到 finished
      const final = await _pollExecution(env.execution_id)
      const r = _envelopeToResult(final)
      results[consoleId] = r
      loadHistory().catch(() => {})
      return r
    } finally {
      running[consoleId] = false
      delete currentExecutionId[consoleId]
    }
  }

  async function _pollExecution(executionId: string, intervalMs: number = 500, maxAttempts: number = 600): Promise<ExecutionEnvelope> {
    for (let i = 0; i < maxAttempts; i++) {
      await new Promise(r => setTimeout(r, intervalMs))
      const env = await apiGet<ExecutionEnvelope>(`/api/sql-workbench/executions/${executionId}`)
      if (env.status !== 'running') return env
    }
    return { execution_id: executionId, status: 'failed', error: 'poll timeout' } as ExecutionEnvelope
  }

  function _envelopeToResult(env: ExecutionEnvelope): ExecuteResponse {
    return {
      success: env.success ?? false,
      columns: env.columns ?? [],
      rows: env.rows ?? [],
      row_count: env.row_count ?? 0,
      elapsed_ms: env.elapsed_ms ?? 0,
      truncated: env.truncated ?? false,
      error: env.error ?? (env.status === 'cancelled' ? '已取消' : null),
    }
  }

  async function cancelExecution(consoleId: string): Promise<void> {
    const execId = currentExecutionId[consoleId]
    if (!execId) return
    await apiJson(`/api/sql-workbench/executions/${execId}/cancel`, 'POST', {})
  }

  // ─── v0.2:format & explain ───────────────────────────────────────────

  async function formatSql(sql: string, datasourceId: string = ''): Promise<FormatResponse> {
    return await apiJson<FormatResponse>('/api/sql-workbench/format', 'POST', {
      datasource_id: datasourceId, sql,
    })
  }

  async function explain(consoleId: string, payload: { datasource_id: string; sql: string }): Promise<ExplainResponse> {
    const resp = await apiJson<ExplainResponse>('/api/sql-workbench/explain', 'POST', payload)
    explainResults[consoleId] = resp
    return resp
  }

  async function loadHistory(datasourceId: string = '', limit: number = 100): Promise<void> {
    const params = new URLSearchParams()
    if (datasourceId) params.set('datasource_id', datasourceId)
    params.set('limit', String(limit))
    const resp = await apiGet<{ items: HistoryEntry[] }>(
      `/api/sql-workbench/history?${params.toString()}`,
    )
    history.value = resp.items || []
  }

  function setActive(id: string): void {
    if (consoles.value.some(c => c.id === id)) activeConsoleId.value = id
  }

  // ─── metadata tree ────────────────────────────────────────────────────

  async function loadSchemas(datasourceId: string): Promise<void> {
    if (!datasourceId) return
    if (!metadataByDs[datasourceId]) {
      metadataByDs[datasourceId] = { schemas: [], loading: false, error: '' }
    }
    metadataByDs[datasourceId].loading = true
    metadataByDs[datasourceId].error = ''
    try {
      const resp = await apiGet<{ items: { name: string }[]; error?: string }>(
        `/api/sql-workbench/metadata/schemas?datasource_id=${encodeURIComponent(datasourceId)}`,
      )
      metadataByDs[datasourceId].schemas = (resp.items || []).map(s => ({
        name: s.name, expanded: false, loading: false, tables: undefined,
      }))
      if (resp.error) metadataByDs[datasourceId].error = resp.error
    } catch (e: unknown) {
      metadataByDs[datasourceId].error = (e as Error)?.message || String(e)
    } finally {
      metadataByDs[datasourceId].loading = false
    }
  }

  async function loadTables(datasourceId: string, schema: string): Promise<void> {
    const meta = metadataByDs[datasourceId]
    if (!meta) return
    const target = meta.schemas.find(s => s.name === schema)
    if (!target) return
    if (target.tables !== undefined) return  // 已加载过,不重复打
    target.loading = true
    try {
      const resp = await apiGet<{ items: { name: string; schema: string }[]; error?: string }>(
        `/api/sql-workbench/metadata/tables?datasource_id=${encodeURIComponent(datasourceId)}&schema=${encodeURIComponent(schema)}`,
      )
      target.tables = resp.items || []
    } catch (e: unknown) {
      target.tables = []
    } finally {
      target.loading = false
    }
  }

  function toggleSchema(datasourceId: string, schema: string): void {
    const meta = metadataByDs[datasourceId]
    if (!meta) return
    const target = meta.schemas.find(s => s.name === schema)
    if (!target) return
    target.expanded = !target.expanded
    if (target.expanded && target.tables === undefined) {
      loadTables(datasourceId, schema).catch(() => {})
    }
  }

  async function loadColumns(datasourceId: string, schema: string, table: string): Promise<string[]> {
    const meta = metadataByDs[datasourceId]
    if (!meta) return []
    const sch = meta.schemas.find(s => s.name === schema)
    if (!sch || !sch.tables) return []
    const t = sch.tables.find(x => x.name === table)
    if (!t) return []
    if (Array.isArray(t.columns)) return t.columns
    if (t.columnsLoading) return []
    t.columnsLoading = true
    try {
      const qs = new URLSearchParams({ datasource_id: datasourceId, table, schema })
      const resp = await apiGet<{ items: { name: string }[]; error?: string }>(
        `/api/sql-workbench/metadata/columns?${qs.toString()}`,
      )
      t.columns = (resp.items || []).map(c => c.name)
      return t.columns
    } catch {
      t.columns = []
      return []
    } finally {
      t.columnsLoading = false
    }
  }

  // ─── 本地草稿(localStorage) ─────────────────────────────────────────
  // 后端 console.sql 是"已保存到服务器",draft 是"用户最近一次未保存到服务器
  // 的编辑"。两者分开,允许用户离线编辑 / 跨刷新恢复未保存。
  function saveDraft(consoleId: string, sql: string): void {
    if (!consoleId) return
    try {
      if (sql) localStorage.setItem(DRAFT_KEY_PREFIX + consoleId, sql)
      else localStorage.removeItem(DRAFT_KEY_PREFIX + consoleId)
    } catch {
      // localStorage 容量满 / 隐私模式 / SSR 环境下静默失败
    }
  }

  function loadDraft(consoleId: string): string {
    if (!consoleId) return ''
    try {
      return localStorage.getItem(DRAFT_KEY_PREFIX + consoleId) || ''
    } catch {
      return ''
    }
  }

  function clearDraft(consoleId: string): void {
    if (!consoleId) return
    try {
      localStorage.removeItem(DRAFT_KEY_PREFIX + consoleId)
    } catch {
      // ignore
    }
  }

  function _defaultName(): string {
    let n = 1
    const existing = new Set(consoles.value.map(c => c.name))
    while (existing.has(`Console ${n}`)) n++
    return `Console ${n}`
  }

  return {
    consoles, activeConsoleId, activeConsole,
    results, running, history, loadingConsoles,
    metadataByDs, currentExecutionId, explainResults,
    loadConsoles, createConsole, updateConsole, deleteConsole,
    execute, cancelExecution, loadHistory, setActive,
    loadSchemas, loadTables, toggleSchema, loadColumns,
    formatSql, explain,
    saveDraft, loadDraft, clearDraft,
  }
})
