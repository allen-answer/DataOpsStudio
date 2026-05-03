/**
 * History store —— 执行历史选择 / sheet 选择 / 任务过滤 state。
 *
 * 跟 historyTaskOptions / filteredHistory 等 computed 不打包到这里，
 * 因为它们依赖 state.history + state.tasks 列表（仍在 App.vue），后续
 * 全局列表迁到 useBootstrapStore 时再一起搬。
 */
import { ref } from 'vue'
import { defineStore } from 'pinia'


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

  return {
    selectedHistory, selectedSheets, selectedHistoryTaskId, historyActiveTab,
    clearSelection, setHistoryTab,
  }
})
