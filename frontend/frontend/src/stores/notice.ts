/**
 * 全局 notice + actionStatus + AI 翻译 + 复用 helpers（copyField / errorMessage）。
 *
 * S2.B 后所有 view 直接 `useNoticeStore()` —— App.vue 不再 provide('app')。
 * S3.B：迁 .ts，给 actionStatus / AI 翻译 / history item 加类型。
 */
import { reactive, ref } from 'vue'
import { defineStore } from 'pinia'
import { useClipboard } from '@vueuse/core'

export type ActionStatusKind = 'idle' | 'running' | 'success' | 'error' | 'warning'

export interface ActionStatus {
  type: ActionStatusKind
  title: string
  message: string
}

export interface AITranslation {
  translation: string
  suggestions?: string[]
  original?: string
}

/** history record 的最小结构 —— historyItemTaskLabel / summaryValue 用。
 * 完整字段在 historyStore；这里只列 helpers 触达的那几个。 */
export interface HistoryItem {
  task_id?: string
  task_name?: string
  summary?: Record<string, unknown>
}

export const useNoticeStore = defineStore('notice', () => {
  // useClipboard 必须在 setup 内调（依赖 inject）
  const _clipboard = useClipboard({ legacy: true })
  const notice = ref<string>('')
  const noticeTimer = ref<ReturnType<typeof setTimeout> | null>(null)
  const actionStatus = reactive<ActionStatus>({
    type: 'idle',
    title: '等待操作',
    message: '保存任务后可执行对比、预览、后台执行或复制任务。',
  })

  // AI 错误翻译：api.js 在 5xx 错误时调 /api/ai/translate-error，把结果丢这里
  // 让 AppShell 弹更醒目的卡片（不是普通 toast 4 秒就消失的那种）
  const aiTranslation = ref<AITranslation | null>(null)

  function setNotice(msg: string): void {
    notice.value = msg
    if (noticeTimer.value) clearTimeout(noticeTimer.value)
    if (msg) {
      noticeTimer.value = setTimeout(() => { notice.value = '' }, 4000)
    }
  }

  function setActionStatus(type: ActionStatusKind, title: string, message = ''): void {
    actionStatus.type = type
    actionStatus.title = title
    actionStatus.message = message
  }

  function setAITranslation(payload: AITranslation | null): void {
    aiTranslation.value = payload
  }

  function dismissAITranslation(): void {
    aiTranslation.value = null
  }

  // S2.B：把 App.vue 的 copyField 迁过来 —— 复制 + setNotice 提示。
  // 跨组件常用，纯依赖 noticeStore 自己的 setNotice，没理由让每个调用方
  // 自己重新实例化 useClipboard。
  async function copyField(text: string): Promise<void> {
    if (!text) return
    try {
      await _clipboard.copy(text)
      setNotice(`已复制：${text}`)
    } catch {
      setNotice('复制失败，请手动选中复制')
    }
  }

  // S2.B：常用 utility helpers，便于 view 直接 useNoticeStore() 一站取
  function toErrorMessage(error: unknown): string {
    if (error && typeof error === 'object' && 'message' in error) {
      return String((error as { message?: unknown }).message)
    }
    return String(error ?? '未知错误')
  }
  function historyItemTaskLabel(item: HistoryItem): string {
    return item.task_name
      || (item.task_id ? `任务 ${item.task_id.slice(0, 8)}` : '非对比任务')
  }
  function summaryValue(item: HistoryItem, key: string): unknown {
    return item.summary?.[key] ?? '-'
  }

  return {
    notice, actionStatus, aiTranslation,
    setNotice, setActionStatus,
    setAITranslation, dismissAITranslation,
    copyField, toErrorMessage, historyItemTaskLabel, summaryValue,
  }
})
