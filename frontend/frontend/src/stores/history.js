/**
 * History store —— 执行历史选择 state + 删/导出 handler。
 *
 * historyTaskOptions / filteredHistory 等 computed 仍在 App.vue：它们依赖
 * useBootstrapStore.state.history + state.tasks，组件少看不值得跨 store 迁。
 */
import { ref } from 'vue'
import { defineStore } from 'pinia'
import { apiJson } from '../api'
import { useBootstrapStore } from './bootstrap'
import { useNoticeStore } from './notice'

function _toErrorMessage(error) {
  return error?.message || String(error || '未知错误')
}


export const useHistoryStore = defineStore('history', () => {
  const selectedHistory = ref(new Set())
  const selectedSheets = ref(new Set(['汇总对照']))
  const selectedHistoryTaskId = ref('')
  const historyActiveTab = ref('compare') // 'compare' | 'lineage'

  function clearSelection() {
    selectedHistory.value = new Set()
  }

  function setHistoryTab(tab) {
    historyActiveTab.value = tab
    // 切 tab 时清掉旧选择避免误导出
    clearSelection()
  }

  async function deleteHistory(runId) {
    const notice = useNoticeStore()
    const bootstrap = useBootstrapStore()
    try {
      await apiJson(`/api/history/${runId}`, 'DELETE')
      bootstrap.state.history = bootstrap.state.history.filter(h => h.run_id !== runId)
      selectedHistory.value.delete(runId)
    } catch (error) {
      notice.setNotice(`删除失败：${_toErrorMessage(error)}`)
    }
  }

  async function exportHistory() {
    const notice = useNoticeStore()
    if (!selectedHistory.value.size) {
      notice.setNotice('请先选择要导出的历史记录')
      return
    }
    const form = new FormData()
    Array.from(selectedHistory.value).forEach((id) => form.append('run_ids', id))
    Array.from(selectedSheets.value).forEach((sheet) => form.append('sheet_names', sheet))
    let response
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

  return {
    selectedHistory, selectedSheets, selectedHistoryTaskId, historyActiveTab,
    clearSelection, setHistoryTab,
    deleteHistory, exportHistory,
  }
})
