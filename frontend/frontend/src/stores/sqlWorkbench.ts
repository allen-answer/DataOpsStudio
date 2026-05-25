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
    if (activeConsoleId.value === id) {
      activeConsoleId.value = consoles.value[0]?.id || ''
    }
  }

  async function execute(consoleId: string, payload: {
    datasource_id: string
    sql: string
    max_rows?: number
  }): Promise<ExecuteResponse> {
    running[consoleId] = true
    try {
      const resp = await apiJson<ExecuteResponse>('/api/sql-workbench/execute', 'POST', {
        datasource_id: payload.datasource_id,
        sql: payload.sql,
        max_rows: payload.max_rows || 1000,
        console_id: consoleId,
      })
      results[consoleId] = resp
      // 异步刷历史(不阻塞)
      loadHistory().catch(() => {})
      return resp
    } finally {
      running[consoleId] = false
    }
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

  function _defaultName(): string {
    let n = 1
    const existing = new Set(consoles.value.map(c => c.name))
    while (existing.has(`Console ${n}`)) n++
    return `Console ${n}`
  }

  return {
    consoles, activeConsoleId, activeConsole,
    results, running, history, loadingConsoles,
    loadConsoles, createConsole, updateConsole, deleteConsole,
    execute, loadHistory, setActive,
  }
})
