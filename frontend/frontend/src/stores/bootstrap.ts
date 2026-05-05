/**
 * Bootstrap store —— 应用全局列表的单一数据源。
 *
 * 持有 GET /api/bootstrap 返回的所有 list 数据：
 *   - datasources / tasks / workflows / history / historySheets
 *   - drivers / dbTypes（驱动可用性 + DB 类型枚举）
 *
 * 暴露 `reload()` 拉一次接口写入 state；业务联动（reload 完后切默认任务、
 * 默认 datasource、默认 db_type）由 App.vue 的 loadBootstrap 包一层做，
 * 避免 store 内反向 import 其它 store 造成循环。
 *
 * S2.B 后所有 view 直接 `useBootstrapStore()` —— state 是 reactive，解构后
 * 仍是同一个 proxy，不需要 storeToRefs。driverItems 是 computed，给
 * sidebar 显示驱动可用性 dot 用。
 *
 * S3.B：迁 .ts —— BootstrapState 接口公开给 view / store 类型推断用。
 *   list 字段都用 unknown[]（具体 shape 在各 domain store 里）；
 *   等 task / workflow / datasource store 也迁 ts 后再换具体类型。
 */
import { computed, reactive } from 'vue'
import { defineStore } from 'pinia'
import { apiGet } from '../api'


export interface BootstrapState {
  datasources: unknown[]
  tasks: unknown[]
  workflows: unknown[]
  workflowTemplates: unknown[]
  drivers: Record<string, boolean>
  dbTypes: string[]
  history: unknown[]
  historySheets: unknown[]
}

interface BootstrapApiResponse {
  datasources?: unknown[]
  tasks?: unknown[]
  workflows?: unknown[]
  workflow_templates?: unknown[]
  drivers?: Record<string, boolean>
  db_types?: string[]
  history?: unknown[]
  history_sheets?: unknown[]
}


export const useBootstrapStore = defineStore('bootstrap', () => {
  const state = reactive<BootstrapState>({
    datasources: [],
    tasks: [],
    workflows: [],
    workflowTemplates: [],
    drivers: {},
    dbTypes: [],
    history: [],
    historySheets: [],
  })

  async function reload(): Promise<BootstrapState> {
    const data = await apiGet('/api/bootstrap') as BootstrapApiResponse
    state.datasources = data.datasources || []
    state.tasks = data.tasks || []
    state.workflows = data.workflows || []
    state.workflowTemplates = data.workflow_templates || []
    state.drivers = data.drivers || {}
    state.dbTypes = data.db_types || []
    state.history = data.history || []
    state.historySheets = data.history_sheets || []
    return state
  }

  // sidebar 显示驱动可用性 dot；从 state.drivers map 派生 [(name, ok), ...]
  const driverItems = computed<[string, boolean][]>(
    () => Object.entries(state.drivers || {})
  )

  return { state, reload, driverItems }
})
