/**
 * Bootstrap store —— 应用全局列表的单一数据源。
 *
 * 持有 GET /api/bootstrap 返回的所有 list 数据：
 *   - datasources / tasks / workflows / history / historySheets
 *   - drivers / dbTypes（驱动可用性 + DB 类型枚举）
 *
 * 暴露 `reload()` 拉一次接口写入 state；业务联动（reload 完后切默认任务、
 * 默认 datasource、默认 db_type）仍由 App.vue 的 loadBootstrap 包一层做，
 * 避免 store 内反向 import 其它 store 造成循环。
 *
 * View 通过 `inject('app').state` 访问保持 backward compat —— App.vue 把
 * `bootstrapStore.state` 平铺到 provide('app').state。reactive 解构后仍是
 * 同一个 proxy，不需要 storeToRefs。
 */
import { reactive } from 'vue'
import { defineStore } from 'pinia'
import { apiGet } from '../api'


export const useBootstrapStore = defineStore('bootstrap', () => {
  const state = reactive({
    datasources: [],
    tasks: [],
    workflows: [],
    drivers: {},
    dbTypes: [],
    history: [],
    historySheets: [],
  })

  async function reload() {
    const data = await apiGet('/api/bootstrap')
    state.datasources = data.datasources || []
    state.tasks = data.tasks || []
    state.workflows = data.workflows || []
    state.drivers = data.drivers || {}
    state.dbTypes = data.db_types || []
    state.history = data.history || []
    state.historySheets = data.history_sheets || []
    return state
  }

  return { state, reload }
})
