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
  source_rows?: number
  target_rows?: number
  excel_filename?: string
  result_filename?: string
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

  /** 切片 D：HistoryView 列表懒加载分页。
   *
   * 旧方案 `loadHistory()` 一次拉满 limit=2000，2000 行 DOM 让 HistoryView 进入
   * 时卡 1-2s。切片 D 改成分页：
   * - 首屏 / refresh：`loadHistory()` 拉前 `PAGE_SIZE=100` 条覆盖 bootstrap.state.history
   * - 滚到底部 / 点 Load more：`loadMoreHistory()` 拉下一批 append
   * - `historyHasMore` 标识是否还有下一页（最近一次返回 == PAGE_SIZE 视为可能还有）
   *
   * loadHistory 总是从 0 重新拉（覆盖现有 history），保证刷新后拿到最新数据。
   * 想拉全量（如导出脚本场景）显式调 `loadAllHistory()`。
   */
  const PAGE_SIZE = 100
  const historyHasMore = ref<boolean>(false)
  const historyLoading = ref<boolean>(false)

  async function loadHistory(): Promise<void> {
    historyLoading.value = true
    try {
      const items = await apiGet<HistoryRecord[]>(`/api/history?limit=${PAGE_SIZE}`)
      _bootstrap.state.history = items
      historyHasMore.value = items.length >= PAGE_SIZE
      selectedHistory.value = new Set()
    } finally {
      historyLoading.value = false
    }
  }

  async function loadMoreHistory(): Promise<void> {
    if (!historyHasMore.value || historyLoading.value) return
    historyLoading.value = true
    try {
      const existing = _bootstrap.state.history as HistoryRecord[]
      const seen = new Set(existing.map((it) => it.run_id))
      // /api/history 当前不支持 offset，先拉下一页 size 的 2× 倍尝试覆盖 sort 后的边界。
      // 等后端加 offset 参数（同切片或后续 slice）再换。临时方案：拉更大窗口去重 append。
      const nextSize = existing.length + PAGE_SIZE
      const items = await apiGet<HistoryRecord[]>(`/api/history?limit=${nextSize}`)
      const newOnes = items.filter((it) => !seen.has(it.run_id))
      _bootstrap.state.history = [...existing, ...newOnes]
      // 后端返回 < nextSize 说明已到底
      historyHasMore.value = items.length >= nextSize
    } finally {
      historyLoading.value = false
    }
  }

  /** 一次拉全量（导出 / 老调用方兼容）。HistoryView 不再用，留作 escape hatch。 */
  async function loadAllHistory(): Promise<void> {
    historyLoading.value = true
    try {
      _bootstrap.state.history = await apiGet<HistoryRecord[]>('/api/history?limit=2000')
      historyHasMore.value = false
      selectedHistory.value = new Set()
    } finally {
      historyLoading.value = false
    }
  }

  return {
    selectedHistory, selectedSheets, selectedHistoryTaskId, historyActiveTab,
    historyTaskOptions, filteredHistory, compareHistoryCount, lineageHistoryCount,
    historyHasMore, historyLoading,
    clearSelection, setHistoryTab,
    deleteHistory, exportHistory, loadHistory, loadMoreHistory, loadAllHistory,
  }
})
