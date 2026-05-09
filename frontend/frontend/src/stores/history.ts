/**
 * History store —— 执行历史选择 state + 删/导出 handler + 派生 computed。
 *
 * S2.B：把 historyTaskOptions / filteredHistory / *HistoryCount 从 App.vue
 * 迁过来。这些 computed 依赖 bootstrapStore.state.history + state.tasks。
 *
 * S3.B：迁 .ts。HistoryRecord / HistoryTaskOption 公开类型给 view 用。
 * tasks / history 在 bootstrap.state 里仍是 unknown[]，这里收口转 cast 到具体
 * shape；等 task store 也迁 ts 后双向收口。
 */
import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { apiGet, apiJson } from '../api'
import { useBootstrapStore } from './bootstrap'
import { useNoticeStore } from './notice'


export interface HistoryRecord {
  run_id: string
  task_id?: string
  task_name?: string
  type?: 'compare' | 'lineage' | string
  started_at?: string
  status?: string
  summary?: Record<string, unknown>
}

export interface TaskMinimal {
  id: string
  name: string
}

export interface HistoryTaskOption {
  id: string
  name: string
}


function _toErrorMessage(error: unknown): string {
  if (error && typeof error === 'object' && 'message' in error) {
    return String((error as { message?: unknown }).message)
  }
  return String(error ?? '未知错误')
}


export const useHistoryStore = defineStore('history', () => {
  const selectedHistory = ref<Set<string>>(new Set())
  const selectedSheets = ref<Set<string>>(new Set(['汇总对照']))
  const selectedHistoryTaskId = ref<string>('')
  const historyActiveTab = ref<'compare' | 'lineage'>('compare')

  function clearSelection(): void {
    selectedHistory.value = new Set()
  }

  function setHistoryTab(tab: 'compare' | 'lineage'): void {
    historyActiveTab.value = tab
    // 切 tab 时清掉旧选择避免误导出
    clearSelection()
  }

  async function deleteHistory(runId: string): Promise<void> {
    const notice = useNoticeStore()
    const bootstrap = useBootstrapStore()
    try {
      await apiJson(`/api/history/${runId}`, 'DELETE')
      bootstrap.state.history = (bootstrap.state.history as HistoryRecord[]).filter(
        (h) => h.run_id !== runId,
      )
      selectedHistory.value.delete(runId)
    } catch (error) {
      notice.setNotice(`删除失败：${_toErrorMessage(error)}`)
    }
  }

  async function exportHistory(): Promise<void> {
    const notice = useNoticeStore()
    if (!selectedHistory.value.size) {
      notice.setNotice('请先选择要导出的历史记录')
      return
    }
    const form = new FormData()
    Array.from(selectedHistory.value).forEach((id) => form.append('run_ids', id))
    Array.from(selectedSheets.value).forEach((sheet) => form.append('sheet_names', sheet))
    let response: Response
    try {
      response = await fetch('/history/export', { method: 'POST', body: form })
    } catch (error) {
      notice.setNotice(`导出失败：${_toErrorMessage(error)}`)
      return
    }
    if (!response.ok) {
      notice.setNotice(`导出失败：${await response.text()}`)
      return
    }
    // 从 Content-Disposition 抽 filename；FastAPI FileResponse 会给。
    // 必须主动 <a download> 触发：浏览器对 .xlsx 默认 inline 渲染会乱码。
    const disposition = response.headers.get('Content-Disposition') || ''
    const match = disposition.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i)
    const filename = match ? decodeURIComponent(match[1]) : `history_export_${Date.now()}.xlsx`
    const blob = await response.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }

  // S2.B：从 App.vue 迁来的派生 computed + loadHistory
  const _bootstrap = useBootstrapStore()

  /** 任务过滤 dropdown 的选项：list 里所有任务 + history 里残留的已删除任务名。 */
  const historyTaskOptions = computed<HistoryTaskOption[]>(() => {
    const options: HistoryTaskOption[] = (_bootstrap.state.tasks as TaskMinimal[]).map(
      (task) => ({ id: task.id, name: task.name }),
    )
    const seen = new Set(options.map((item) => item.id))
    ;(_bootstrap.state.history as HistoryRecord[]).forEach((item) => {
      if (item.task_id && !seen.has(item.task_id)) {
        seen.add(item.task_id)
        options.push({
          id: item.task_id,
          name: item.task_name || `已删除任务 ${item.task_id.slice(0, 8)}`,
        })
      }
    })
    return options
  })

  /** 当前 tab + 任务过滤后的 history 列表。 */
  const filteredHistory = computed<HistoryRecord[]>(() => {
    let items = _bootstrap.state.history as HistoryRecord[]
    if (historyActiveTab.value === 'compare') {
      items = items.filter((item) => item.type !== 'lineage')
    } else if (historyActiveTab.value === 'lineage') {
      items = items.filter((item) => item.type === 'lineage')
    }
    if (!selectedHistoryTaskId.value) return items
    return items.filter((item) => item.task_id === selectedHistoryTaskId.value)
  })

  const compareHistoryCount = computed<number>(
    () => (_bootstrap.state.history as HistoryRecord[]).filter((item) => item.type !== 'lineage').length,
  )
  const lineageHistoryCount = computed<number>(
    () => (_bootstrap.state.history as HistoryRecord[]).filter((item) => item.type === 'lineage').length,
  )

  /** 拉一遍 /api/history 写回 bootstrapStore.state；HistoryView 删除 / 跑完 task
   * 后调用。不复用 bootstrap.reload() 是因为这里只刷历史，不刷 datasources/tasks。
   *
   * limit=2000 是 /api/history 当前允许的上限。HistoryView 是「看所有历史」的专属
   * 页面，bootstrap 默认 200 条不够；显式拉够。如果将来真有项目历史超 2000 条，
   * 再考虑前端分页。 */
  async function loadHistory(): Promise<void> {
    _bootstrap.state.history = await apiGet<HistoryRecord[]>('/api/history?limit=2000')
    selectedHistory.value = new Set()
  }

  return {
    selectedHistory, selectedSheets, selectedHistoryTaskId, historyActiveTab,
    historyTaskOptions, filteredHistory, compareHistoryCount, lineageHistoryCount,
    clearSelection, setHistoryTab,
    deleteHistory, exportHistory, loadHistory,
  }
})
